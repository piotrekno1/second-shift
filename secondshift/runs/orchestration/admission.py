"""Admission policy for standalone tasks, shared by dispatch and execution."""

from secondshift.repositories.models import Repository
from secondshift.runs.models import Run
from secondshift.workspaces.access import Action, role_allows
from secondshift.workspaces.dao import active_member_role


def task_admission_problem(run: Run, repository: Repository) -> str:
    if not repository.accepts_runs:
        return "The repository was disabled or removed before this task started."
    role = active_member_role(repository.workspace_id, run.task.submitted_by_id)
    if not role_allows(role, Action.RUN_JOBS):
        return "The person who submitted this task can no longer run tasks in this workspace."
    return ""
