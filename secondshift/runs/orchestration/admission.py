"""Admission policy for standalone tasks, shared by dispatch and execution."""

from secondshift.repositories.models import Repository
from secondshift.runs.models import Run
from secondshift.workspaces.access import Action, role_allows
from secondshift.workspaces.models import WorkspaceMembership


def task_admission_problem(run: Run, repository: Repository) -> str:
    if not repository.enabled or repository.removed_at is not None:
        return "The repository was disabled or removed before this task started."
    role = (
        WorkspaceMembership.objects.filter(
            workspace_id=repository.workspace_id,
            user_id=run.task.submitted_by_id,
            user__is_active=True,
        )
        .values_list("role", flat=True)
        .first()
    )
    if not role_allows(role, Action.RUN_JOBS):
        return "The person who submitted this task can no longer run tasks in this workspace."
    return ""
