"""Accept a standalone skill assignment through either authenticated interface."""

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Self

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, model_validator

from secondshift.config.constants import RunStart, TaskOrigin
from secondshift.jobs.manifest import TaskManifest
from secondshift.jobs.planning import build_skill_execution_plan
from secondshift.jobs.readiness import execution_problems
from secondshift.repositories.github_app import GitHubAppError
from secondshift.repositories.local import LocalRepositoryError
from secondshift.repositories.models import Repository
from secondshift.repositories.skill_source import SkillSourceError, skill_commit
from secondshift.runs.models import Run, Task
from secondshift.runs.orchestration.dispatch import enqueue_run
from secondshift.runs.services import RunInProgress, RunNotReady, RunRefused


class SkillSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: int = Field(gt=0)
    definition: TaskManifest
    revision: str | None = Field(default=None, min_length=1, max_length=255)
    input: str = Field(default="", max_length=16000)
    idempotency_key: str = Field(
        min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    )

    @model_validator(mode="after")
    def standalone_definition(self) -> Self:
        if self.definition.skill is None and not self.input.strip():
            raise ValueError("Write task instructions or select a repository skill.")
        if (
            self.definition.schedule
            or self.definition.triggers
            or self.definition.history
        ):
            raise ValueError(
                "A standalone task cannot set a schedule, triggers or job history."
            )
        return self


@dataclass(frozen=True)
class SubmissionResult:
    run: Run
    started: bool


def submission_fingerprint(submission: SkillSubmission, user: User) -> str:
    data = submission.model_dump(mode="json", exclude={"idempotency_key"})
    data["submitted_by"] = user.pk
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def admission(
    repository: Repository, submission: SkillSubmission, fingerprint: str
) -> SubmissionResult | None:
    """Called with the repository row locked, before and after source access."""
    if not repository.enabled or repository.removed_at is not None:
        raise RunRefused("Enable the repository before submitting a task.")
    task = Task.objects.filter(
        repository=repository, submission_key=submission.idempotency_key
    ).first()
    if task is not None:
        if task.input_snapshot.get("submission_fingerprint") != fingerprint:
            raise RunRefused(
                "This idempotency key was already used for a different submission."
            )
        run = task.runs.order_by("created_at", "pk").first()
        if run is None:
            raise RunRefused("This submission no longer has an execution attempt.")
        return SubmissionResult(run, False)
    active = (
        Run.objects.filter(
            job__isnull=True,
            task__repository=repository,
            status__in=Run.ACTIVE_STATUSES,
        )
        .order_by("created_at", "pk")
        .first()
    )
    if active is not None:
        raise RunInProgress(
            active, f"Run #{active.pk} is still active for this repository."
        )
    return None


def submit_skill(
    repository: Repository,
    submission: SkillSubmission,
    user: User,
    origin: TaskOrigin,
) -> SubmissionResult:
    if repository.pk != submission.repository_id:
        raise RunRefused("The submission does not match the selected repository.")
    fingerprint = submission_fingerprint(submission, user)
    with transaction.atomic():
        repository = Repository.objects.select_for_update().get(pk=repository.pk)
        if existing := admission(repository, submission, fingerprint):
            return existing
    try:
        commit = skill_commit(
            repository, submission.definition.skill, submission.revision
        )
    except (LocalRepositoryError, GitHubAppError, SkillSourceError) as error:
        # GitHub helpers already redact credentials from errors.
        message = (
            "The skill or revision is unavailable in this repository."
            if submission.definition.skill is not None
            else "The requested repository revision is unavailable."
        )
        raise RunRefused(message) from error
    with transaction.atomic():
        repository = (
            Repository.objects.select_for_update()
            .select_related("workspace")
            .get(pk=repository.pk)
        )
        if existing := admission(repository, submission, fingerprint):
            return existing
        plan = build_skill_execution_plan(
            repository,
            submission.definition,
            key=submission.idempotency_key,
            commit_sha=commit,
        )
        if not plan["ready"]:
            raise RunNotReady(execution_problems(repository, plan))
        snapshot = {
            "name": submission.definition.name,
            "skill_path": submission.definition.skill,
            "config_hash": plan["job"]["config_hash"],
            "definition": submission.definition.model_dump(mode="json"),
            "submission_input": submission.input,
            "requested_revision": submission.revision,
            "submission_fingerprint": fingerprint,
        }
        now = timezone.now()
        task = Task.objects.create(
            repository=repository,
            submission_key=submission.idempotency_key,
            origin=origin,
            submitted_by=user,
            commit_sha=commit,
            input_snapshot=deepcopy(snapshot),
            scheduled_for=now,
        )
        run = Run.objects.create(
            task=task,
            started_by=RunStart.MANUAL,
            commit_sha=commit,
            input_snapshot=snapshot,
            execution_plan=plan,
            scheduled_for=now,
        )
        transaction.on_commit(lambda: enqueue_run(run))
    return SubmissionResult(run, True)
