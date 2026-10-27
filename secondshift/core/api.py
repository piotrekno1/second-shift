from django.conf import settings
from django.http import HttpRequest
from django.utils import timezone
from ninja import Router
from ninja.security import django_auth

from secondshift.connections.models import Connection
from secondshift.core.schemas import (
    FailingJobSchema,
    JobSpendSchema,
    OverviewSchema,
    UserSchema,
)
from secondshift.core.workspace_settings import (
    environment_label,
    sandbox_label,
)
from secondshift.jobs.models import Job
from secondshift.repositories.github_app import configured as github_app_configured
from secondshift.repositories.models import Repository
from secondshift.runs.models import Run
from secondshift.runs.reporting.health import failing_jobs, spend_by_job, week_summary
from secondshift.workspaces.access import (
    current_membership,
    current_workspace,
    is_operator,
    permitted_actions,
)
from secondshift.workspaces.services import can_create_workspace

router = Router(auth=django_auth, tags=["core"])


@router.get("/me", response=UserSchema, auth=django_auth)
def me(request: HttpRequest) -> UserSchema:
    membership = current_membership(request)
    workspace = membership.workspace if membership else None
    return UserSchema(
        email=request.user.email or request.user.get_username(),
        workspace_name=workspace.name if workspace else None,
        workspace_slug=workspace.slug if workspace else None,
        role=membership.role if membership else None,
        permissions=permitted_actions(request),
        can_use_admin=is_operator(request),
        can_create_workspace=can_create_workspace(request.user),
        local_repositories_enabled=settings.LOCAL_REPOSITORIES_ENABLED,
        github_app_configured=github_app_configured(),
        environment=environment_label(),
        sandbox=sandbox_label(),
    )


@router.get("/overview", response=OverviewSchema, auth=django_auth)
def overview(request: HttpRequest) -> OverviewSchema:
    workspace = current_workspace(request)
    if not workspace:
        return OverviewSchema(
            repositories=0,
            local_repositories=0,
            enabled_jobs=0,
            runs_today=0,
            failed_runs=0,
            connected_services=0,
        )
    today = timezone.localdate()
    active_repositories = Repository.objects.filter(
        workspace=workspace,
        removed_at__isnull=True,
    )
    runs = Run.objects.filter(job__workspace=workspace)
    week = week_summary(workspace)
    return OverviewSchema(
        repositories=active_repositories.count(),
        local_repositories=active_repositories.filter(
            source_type=Repository.SourceType.LOCAL
        ).count(),
        enabled_jobs=Job.objects.filter(
            workspace=workspace,
            repository__removed_at__isnull=True,
            enabled=True,
        ).count(),
        runs_today=runs.filter(created_at__date=today).count(),
        failed_runs=runs.filter(status=Run.Status.FAILED).count(),
        connected_services=Connection.objects.filter(
            workspace=workspace,
            status=Connection.Status.CONNECTED,
        ).count(),
        failing_jobs=[
            FailingJobSchema(**vars(failing)) for failing in failing_jobs(workspace)
        ],
        runs_7d=week.runs,
        finished_7d=week.finished,
        succeeded_7d=week.succeeded,
        spend_7d_usd=float(week.spend_usd),
        spend_previous_7d_usd=float(week.previous_spend_usd),
        spend_partial=week.spend_partial,
        spend_by_job=[
            JobSpendSchema(**{**vars(row), "spend_usd": float(row.spend_usd)})
            for row in spend_by_job(workspace)
        ],
    )
