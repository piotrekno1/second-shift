"""Schedule a skill someone tried, by opening a pull request that adds its job.

The job goes into the repository's .secondshift.yaml on its default branch,
pinned to the commit of the skill the run used. Nothing changes in
SecondShift until the pull request is merged and the repository syncs. Then
the job is synced like any other, and an owner approves what it needs.
"""

from typing import Any

from django.utils import timezone

from secondshift.connections.github import shows_attribution
from secondshift.jobs.manifest import ManifestError
from secondshift.jobs.manifest_edit import add_job, job_key
from secondshift.repositories.models import MANIFEST_FILE
from secondshift.repositories.pull_requests import (
    DraftPullRequest,
    PublishedPullRequest,
    PublishError,
    publish_file_change,
)
from secondshift.runs.links import attribution_footer
from secondshift.runs.models import Run
from secondshift.runs.publishing import CO_AUTHOR
from secondshift.runs.schemas import ScheduleSkillSchema
from secondshift.runs.services import RunRefused

BRANCH_PREFIX = "secondshift/schedule-"


def scheduled_job(run: Run, choice: ScheduleSkillSchema) -> dict[str, Any]:
    """The job as it goes into the file. Only fields the dialog set appear."""
    plan = run.plan.job
    source = plan.skill_source
    assert source is not None
    job: dict[str, Any] = {
        "name": choice.name,
        "skill": plan.skill_path,
        "skill_source": {"repository": source.repository, "ref": source.commit_sha},
    }
    if choice.schedule:
        job["schedule"] = choice.schedule
        if choice.timezone != "UTC":
            job["timezone"] = choice.timezone
    if choice.notify:
        job["notify"] = [{"binding": binding} for binding in choice.notify]
    if choice.open_pull_request:
        job["open_pull_request"] = True
    if choice.open_issues:
        job["open_issues"] = True
    return job


def refusal(run: Run) -> str:
    """Why this run can't be scheduled with a pull request, or an empty string."""
    if run.job_id is not None or run.plan.job.skill_source is None:
        return "Only a skill tried from GitHub can be scheduled from its run."
    if run.status != Run.Status.SUCCEEDED:
        return "Schedule a skill once a run of it succeeds."
    if not run.repository.receives_pull_requests:
        return (
            "An owner has not allowed pull requests for this repository. "
            "Copy the job instead."
        )
    return ""


def schedule_draft(run: Run, key: str, choice: ScheduleSkillSchema) -> DraftPullRequest:
    repository = run.repository
    attribution = shows_attribution(repository.github_connection)
    source = run.plan.job.skill_source
    assert source is not None
    title = f"Schedule {choice.name}"
    body = (
        f"Adds the job `{key}` to `{MANIFEST_FILE}`. It runs the skill "
        f"`{run.plan.job.skill_path}` from {source.repository} at "
        f"`{source.commit_sha[:12]}`, the commit that was tried.\n\n"
        "The job starts once this is merged and the repository syncs. If it "
        "uses a connected service, an owner approves that access first."
    )
    message = title
    if attribution:
        body = f"{body}\n\n---\n\n{attribution_footer('Opened', run)}"
        message = f"{title}\n\nSecondShift-Run: {run.pk}\nCo-authored-by: {CO_AUTHOR}"
    # One branch per job and run, so asking again finds the same draft.
    branch = f"{BRANCH_PREFIX if attribution else 'schedule-'}{key}-{run.pk}"
    return DraftPullRequest(
        branch=branch,
        base=repository.default_branch,
        title=title,
        body=body,
        message=message,
        committed_at=timezone.now(),
    )


def schedule_skill(run: Run, choice: ScheduleSkillSchema) -> PublishedPullRequest:
    """Open a draft pull request that adds the job. Raises RunRefused."""
    if reason := refusal(run):
        raise RunRefused(reason)
    key = job_key(choice.name)
    job = scheduled_job(run, choice)
    try:
        return publish_file_change(
            run.repository,
            MANIFEST_FILE,
            lambda text: add_job(text, key, job),
            schedule_draft(run, key, choice),
        )
    except (ManifestError, PublishError) as error:
        raise RunRefused(str(error)) from error
