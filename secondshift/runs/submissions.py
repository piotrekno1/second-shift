"""Accept a standalone skill assignment through either authenticated interface."""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Self

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from secondshift.config.constants import COMMIT_SHA_PATTERN, RunStart
from secondshift.github.errors import GitHubError
from secondshift.jobs.manifest import TaskManifest
from secondshift.jobs.plan import PullRequestPlan, SkillSourcePlan
from secondshift.jobs.planning import build_skill_execution_plan
from secondshift.jobs.readiness import execution_problems
from secondshift.repositories.dao import lock_repository
from secondshift.repositories.external_skills import (
    ExternalSkillError,
    resolve_external_skill,
)
from secondshift.repositories.local import LocalRepositoryError
from secondshift.repositories.models import Repository
from secondshift.repositories.skill_source import SkillSourceError, skill_commit
from secondshift.runs.dao import (
    active_task_count,
    create_task,
    find_submitted_task,
    first_attempt,
)
from secondshift.runs.inputs import RunInput
from secondshift.runs.models import Run
from secondshift.runs.orchestration.dispatch import enqueue_run
from secondshift.runs.services import RunNotReady, RunRefused, create_attempt


class SkillSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: int = Field(gt=0)
    definition: TaskManifest
    revision: str | None = Field(default=None, min_length=1, max_length=255)
    input: str = Field(default="", max_length=16000)
    not_before: AwareDatetime | None = None
    pull_request: bool = Field(
        default=False,
        description=(
            "Open a draft pull request with the agent's changes. The repository "
            "owner must allow pull requests first."
        ),
    )
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
        if self.definition.open_pull_request:
            raise ValueError(
                "Set pull_request on the task, not open_pull_request in its definition."
            )
        if self.definition.open_issues:
            raise ValueError("Only saved jobs can open issues.")
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
    if not repository.accepts_runs:
        raise RunRefused("Enable the repository before submitting a task.")
    task = find_submitted_task(repository.pk, submission.idempotency_key)
    if task is not None:
        if task.fingerprint != fingerprint:
            raise RunRefused(
                "This idempotency key was already used for a different submission."
            )
        run = first_attempt(task)
        if run is None:
            raise RunRefused("This submission no longer has an execution attempt.")
        return SubmissionResult(run, False)
    active_count = active_task_count(repository.pk)
    if active_count >= settings.TASK_QUEUE_LIMIT:
        raise RunRefused(
            "This repository's task queue is full. Wait for a task to finish or cancel a queued task."
        )
    return None


def pull_request_plan(
    repository: Repository, submission: SkillSubmission
) -> PullRequestPlan | None:
    """The draft's base is the requested branch, else the followed one."""
    if not submission.pull_request:
        return None
    if not repository.receives_pull_requests:
        raise RunRefused("This repository does not allow tasks to open pull requests.")
    revision = submission.revision
    is_branch = revision and not re.fullmatch(COMMIT_SHA_PATTERN, revision)
    return PullRequestPlan(
        base=revision if is_branch else repository.default_branch,
        run_ci=repository.pull_request_ci,
    )


def external_source(
    workspace_id: int, definition: TaskManifest
) -> SkillSourcePlan | None:
    """Pin a skill from another repository to the commit the run gets."""
    source = definition.skill_source
    if source is None or definition.skill is None:
        return None
    try:
        skill = resolve_external_skill(
            workspace_id,
            source.repository,
            definition.skill,
            source.ref,
            source.digest,
        )
    except ExternalSkillError as error:
        raise RunRefused(str(error)) from error
    return SkillSourcePlan(
        repository=skill.repository, commit_sha=skill.commit_sha, digest=skill.digest
    )


def submit_skill(
    repository: Repository,
    submission: SkillSubmission,
    user: User,
    started_by: RunStart,
) -> SubmissionResult:
    if repository.pk != submission.repository_id:
        raise RunRefused("The submission does not match the selected repository.")
    fingerprint = submission_fingerprint(submission, user)
    with transaction.atomic():
        repository = lock_repository(repository.pk)
        if existing := admission(repository, submission, fingerprint):
            return existing
    definition = submission.definition
    source = external_source(repository.workspace_id, definition)
    try:
        commit = skill_commit(
            repository,
            definition.skill if source is None else None,
            submission.revision,
        )
    except (LocalRepositoryError, GitHubError, SkillSourceError) as error:
        # GitHub helpers already redact credentials from errors.
        message = (
            "The skill or revision is unavailable in this repository."
            if definition.skill is not None and source is None
            else "The requested repository revision is unavailable."
        )
        raise RunRefused(message) from error
    with transaction.atomic():
        repository = lock_repository(repository.pk, with_workspace=True)
        if existing := admission(repository, submission, fingerprint):
            return existing
        plan = build_skill_execution_plan(
            repository,
            submission.definition,
            key=submission.idempotency_key,
            commit_sha=commit,
            pull_request=pull_request_plan(repository, submission),
            skill_source=source,
        )
        if not plan.ready:
            raise RunNotReady(execution_problems(repository, plan))
        task = create_task(
            repository=repository,
            submission_key=submission.idempotency_key,
            fingerprint=fingerprint,
            submitted_by=user,
        )
        run = create_attempt(
            task,
            plan,
            RunInput(
                name=submission.definition.name,
                definition=submission.definition.model_dump(mode="json"),
                submission_input=submission.input,
                requested_revision=submission.revision,
            ),
            started_by=started_by,
            commit_sha=commit,
            scheduled_for=submission.not_before or timezone.now(),
        )
        transaction.on_commit(lambda: enqueue_run(run))
    return SubmissionResult(run, True)


def resubmission(run: Run, idempotency_key: str) -> SkillSubmission:
    """The task this run did, submitted again as it was first submitted.

    A branch, or a skill source named by branch, resolves to its newest commit
    again, like Run again on a job.
    """
    if run.job_id is not None:
        raise RunRefused("Start a job's run from the job.")
    inputs = run.inputs
    if inputs.definition is None:
        raise RunRefused("This run did not keep its task, so it cannot run again.")
    try:
        return SkillSubmission.model_validate(
            {
                "repository_id": run.repository.pk,
                "definition": inputs.definition,
                "revision": inputs.requested_revision,
                "input": inputs.submission_input,
                "pull_request": run.plan.pull_request is not None,
                "idempotency_key": idempotency_key,
            }
        )
    except ValueError as error:
        raise RunRefused(
            "This task can no longer be submitted as it was. Start a new task."
        ) from error
