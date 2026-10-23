from datetime import datetime
from typing import Literal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import NinjaAPI, Schema, Status
from ninja.errors import HttpError
from ninja.security import django_auth
from pydantic import Field

from secondshift.automations.connection_usage import ConnectionUsage, connection_usage
from secondshift.automations.health import failing_jobs, spend_by_job, week_summary
from secondshift.automations.models import Job, Run
from secondshift.automations.needs import capability_needs
from secondshift.core.models import Workspace
from secondshift.core.services import workspace_for_user
from secondshift.core.workspace_settings import (
    environment_label,
    members,
    sandbox_label,
    setting_groups,
)
from secondshift.integrations.mcp.base import MCPError
from secondshift.integrations.mcp.client import (
    create_mcp_connection,
    discover_tools,
    validate_mcp_connection,
)
from secondshift.integrations.mcp.providers import MCP_PROVIDERS
from secondshift.integrations.model_providers import (
    ModelProviderError,
    check_model_connection,
    create_model_connection,
    update_model_connection,
)
from secondshift.integrations.models import Connection, Secret
from secondshift.integrations.services import (
    ConnectionConflict,
    connection_target,
    create_webhook_connection,
    delete_connection,
    revoke_connection,
    update_webhook_connection,
)
from secondshift.integrations.webhooks import (
    WebhookError,
    connection_test_payload,
    deliver_webhook,
)
from secondshift.repositories.local import (
    LocalRepositoryError,
    attach_local_repository,
    inspect_local_repository,
)
from secondshift.repositories.models import Repository

api = NinjaAPI(title="SecondShift API", version="1.0")
api.add_router("", "secondshift.automations.api.router")


class UserSchema(Schema):
    email: str
    workspace_name: str | None
    workspace_slug: str | None
    local_repositories_enabled: bool
    environment: str = "Production"
    sandbox: str = "gVisor sandbox"


class FailingJobSchema(Schema):
    job_id: int
    job: str
    repository: str
    run_id: int
    failed_at: datetime | None
    label: str
    reason: str


class SettingValueSchema(Schema):
    label: str
    value: str
    env: str
    help: str


class SettingGroupSchema(Schema):
    title: str
    items: list[SettingValueSchema]


class MemberSchema(Schema):
    email: str
    role: str


class WorkspaceSettingsSchema(Schema):
    workspace_name: str | None
    workspace_slug: str | None
    environment: str
    sandbox: str
    members: list[MemberSchema]
    groups: list[SettingGroupSchema]


class JobSpendSchema(Schema):
    job_id: int
    job: str
    repository: str
    runs: int
    spend_usd: float
    partial: bool


class OverviewSchema(Schema):
    repositories: int
    local_repositories: int
    enabled_jobs: int
    runs_today: int
    failed_runs: int
    connected_services: int
    failing_jobs: list[FailingJobSchema] = []
    runs_7d: int = 0
    finished_7d: int = 0
    succeeded_7d: int = 0
    spend_7d_usd: float = 0
    spend_previous_7d_usd: float = 0
    spend_partial: bool = False
    spend_by_job: list[JobSpendSchema] = []


class RepositorySchema(Schema):
    id: int
    full_name: str
    display_name: str
    default_branch: str
    enabled: bool
    job_count: int
    last_synced_at: datetime | None
    source_type: str
    local_path: str | None
    revision: str


class LocalRepositoryCreateSchema(Schema):
    path: str = Field(min_length=1, max_length=1000)


class RepositoryEnabledSchema(Schema):
    enabled: bool


class RepositoryValidationSchema(Schema):
    status: Literal["valid", "warning", "error"]
    message: str
    current_revision: str | None
    synced_revision: str
    needs_sync: bool


class RepositoryJobSchema(Schema):
    id: int
    name: str
    skill_path: str
    cron: str
    timezone: str
    enabled: bool
    grant_status: str


class RepositoryDetailSchema(RepositorySchema):
    config_path: str
    validation: RepositoryValidationSchema
    jobs: list[RepositoryJobSchema]


class ConnectionCheckSchema(Schema):
    ok: bool
    message: str
    checked_at: datetime


class ConnectionUsageSchema(Schema):
    jobs: int
    runs_7d: int
    spend_7d_usd: float
    spend_partial: bool
    last_used_at: datetime | None


class ConnectionSchema(Schema):
    id: int
    provider: str
    kind: str
    binding_key: str
    display_name: str
    status: str
    expires_at: datetime | None
    available_tools: list[str]
    target: str
    allowed_models: list[str]
    default_model: str
    is_default: bool
    available_models: list[str]
    last_check: ConnectionCheckSchema | None = None
    usage: ConnectionUsageSchema | None = None


class WebhookConnectionCreateSchema(Schema):
    binding_key: str = Field(
        min_length=1, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    display_name: str = Field(default="", max_length=200)
    url: str = Field(min_length=1, max_length=2000)


class WebhookConnectionUpdateSchema(Schema):
    display_name: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=2000)


class MCPConnectionCreateSchema(Schema):
    provider: str = Field(min_length=1, max_length=40)
    binding_key: str = Field(
        min_length=1, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    display_name: str = Field(default="", max_length=200)
    token: str = Field(min_length=1, max_length=2000)


class MCPProviderSchema(Schema):
    key: str
    label: str
    description: str
    default_binding: str
    token_label: str
    token_hint: str
    setup_url: str
    setup_help: str
    scope_label: str
    missing_scope_message: str
    tools: list[str]


class ModelConnectionCreateSchema(Schema):
    provider: Literal["anthropic", "openai", "openai_compatible"]
    display_name: str = Field(default="", max_length=200)
    api_key: str = Field(default="", max_length=1000)
    base_url: str = Field(default="", max_length=2000)
    allowed_models: list[str] = Field(default_factory=list, max_length=100)
    default_model: str = Field(default="", max_length=200)
    is_default: bool | None = None


class ModelConnectionUpdateSchema(Schema):
    display_name: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=1000)
    base_url: str | None = Field(default=None, max_length=2000)
    allowed_models: list[str] | None = Field(default=None, max_length=100)
    default_model: str | None = Field(default=None, max_length=200)
    is_default: bool | None = None


class ConnectionTestSchema(Schema):
    ok: bool
    status_code: int | None
    message: str
    models: list[str] = Field(default_factory=list)


class NeedJobSchema(Schema):
    job_id: int
    job_name: str
    repository: str
    capability_id: int
    required: bool
    grant_status: str
    mcp_scopes: dict[str, str | None] = Field(default_factory=dict)


class CapabilityNeedSchema(Schema):
    binding: str
    kind: str
    required: bool
    requested_tools: list[str]
    status: Literal["missing", "unavailable", "needs_approval", "ready"]
    detail: str
    connection: ConnectionSchema | None
    jobs: list[NeedJobSchema]


class SecretSchema(Schema):
    name: str
    description: str
    last_used_at: datetime | None


def user_workspace(request: HttpRequest) -> Workspace | None:
    return workspace_for_user(request.user)


def repository_for_request(request: HttpRequest, repository_id: int) -> Repository:
    workspace = user_workspace(request)
    return get_object_or_404(
        Repository,
        pk=repository_id,
        workspace=workspace,
        removed_at__isnull=True,
    )


def owner_local_path(request: HttpRequest, repository: Repository) -> str | None:
    if (
        repository.source_type == Repository.SourceType.LOCAL
        and repository.workspace.owner_id == request.user.pk
    ):
        return repository.local_path
    return None


def serialize_repository(
    request: HttpRequest,
    repository: Repository,
) -> RepositorySchema:
    return RepositorySchema(
        id=repository.pk,
        full_name=repository.full_name,
        display_name=repository.display_name,
        default_branch=repository.default_branch,
        enabled=repository.enabled,
        job_count=repository.jobs.count(),
        last_synced_at=repository.last_synced_at,
        source_type=repository.source_type,
        local_path=owner_local_path(request, repository),
        revision=repository.config_sha,
    )


def validate_repository(repository: Repository) -> RepositoryValidationSchema:
    if repository.source_type != Repository.SourceType.LOCAL:
        return RepositoryValidationSchema(
            status="valid",
            message="GitHub repository metadata is managed by its installation.",
            current_revision=repository.config_sha or None,
            synced_revision=repository.config_sha,
            needs_sync=False,
        )
    try:
        metadata = inspect_local_repository(repository.local_path)
    except LocalRepositoryError as error:
        return RepositoryValidationSchema(
            status="error",
            message=str(error),
            current_revision=None,
            synced_revision=repository.config_sha,
            needs_sync=False,
        )
    needs_sync = metadata.revision != repository.config_sha
    return RepositoryValidationSchema(
        status="warning" if needs_sync else "valid",
        message=(
            "The repository has a newer revision. Resync before enabling schedules."
            if needs_sync
            else "Manifest, skills, and synchronized revision are valid."
        ),
        current_revision=metadata.revision,
        synced_revision=repository.config_sha,
        needs_sync=needs_sync,
    )


def serialize_repository_detail(
    request: HttpRequest,
    repository: Repository,
) -> RepositoryDetailSchema:
    summary = serialize_repository(request, repository)
    jobs = repository.jobs.prefetch_related("capability_requests__grant").all()
    return RepositoryDetailSchema(
        **summary.model_dump(),
        config_path=repository.config_path,
        validation=validate_repository(repository),
        jobs=[
            RepositoryJobSchema(
                id=job.pk,
                name=job.name,
                skill_path=job.skill_path,
                cron=job.cron,
                timezone=job.timezone,
                enabled=job.enabled,
                grant_status=job.capability_grant_status(),
            )
            for job in jobs
        ],
    )


@api.get("/settings", response=WorkspaceSettingsSchema, auth=django_auth)
def workspace_settings(request: HttpRequest) -> WorkspaceSettingsSchema:
    workspace = user_workspace(request)
    return WorkspaceSettingsSchema(
        workspace_name=workspace.name if workspace else None,
        workspace_slug=workspace.slug if workspace else None,
        environment=environment_label(),
        sandbox=sandbox_label(),
        members=[vars(member) for member in members(workspace)] if workspace else [],
        groups=[
            SettingGroupSchema(
                title=group.title, items=[vars(item) for item in group.items]
            )
            for group in setting_groups()
        ],
    )


@api.get("/me", response=UserSchema, auth=django_auth)
def me(request: HttpRequest) -> UserSchema:
    workspace = user_workspace(request)
    return UserSchema(
        email=request.user.email or request.user.get_username(),
        workspace_name=workspace.name if workspace else None,
        workspace_slug=workspace.slug if workspace else None,
        local_repositories_enabled=settings.LOCAL_REPOSITORIES_ENABLED,
        environment=environment_label(),
        sandbox=sandbox_label(),
    )


@api.get("/overview", response=OverviewSchema, auth=django_auth)
def overview(request: HttpRequest) -> OverviewSchema:
    workspace = user_workspace(request)
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


@api.get("/repositories", response=list[RepositorySchema], auth=django_auth)
def repositories(request: HttpRequest) -> list[RepositorySchema]:
    workspace = user_workspace(request)
    if not workspace:
        return []
    queryset = Repository.objects.filter(
        workspace=workspace,
        removed_at__isnull=True,
    ).prefetch_related("jobs")
    return [serialize_repository(request, item) for item in queryset]


@api.post("/repositories/local", response=RepositorySchema, auth=django_auth)
def create_local_repository(
    request: HttpRequest,
    payload: LocalRepositoryCreateSchema,
) -> RepositorySchema:
    workspace = user_workspace(request)
    if not settings.LOCAL_REPOSITORIES_ENABLED:
        raise HttpError(404, "Local repositories are disabled.")
    if not workspace or workspace.owner_id != request.user.pk:
        raise HttpError(403, "Only the workspace owner can add local repositories.")
    try:
        repository = attach_local_repository(workspace, payload.path)
    except LocalRepositoryError as error:
        raise HttpError(422, str(error)) from error
    return serialize_repository(request, repository)


@api.get(
    "/repositories/{repository_id}",
    response=RepositoryDetailSchema,
    auth=django_auth,
)
def repository_detail(
    request: HttpRequest,
    repository_id: int,
) -> RepositoryDetailSchema:
    repository = repository_for_request(request, repository_id)
    return serialize_repository_detail(request, repository)


@api.post(
    "/repositories/{repository_id}/validate",
    response=RepositoryValidationSchema,
    auth=django_auth,
)
def validate_repository_endpoint(
    request: HttpRequest,
    repository_id: int,
) -> RepositoryValidationSchema:
    return validate_repository(repository_for_request(request, repository_id))


@api.post(
    "/repositories/{repository_id}/resync",
    response=RepositoryDetailSchema,
    auth=django_auth,
)
def resync_repository(
    request: HttpRequest,
    repository_id: int,
) -> RepositoryDetailSchema:
    repository = repository_for_request(request, repository_id)
    if repository.workspace.owner_id != request.user.pk:
        raise HttpError(403, "Only the workspace owner can resync repositories.")
    if repository.source_type != Repository.SourceType.LOCAL:
        raise HttpError(400, "Only local repositories support manual resync.")
    try:
        repository = attach_local_repository(
            repository.workspace, repository.local_path
        )
    except LocalRepositoryError as error:
        raise HttpError(422, str(error)) from error
    return serialize_repository_detail(request, repository)


@api.post(
    "/repositories/{repository_id}/enabled",
    response=RepositoryDetailSchema,
    auth=django_auth,
)
def set_repository_enabled(
    request: HttpRequest,
    repository_id: int,
    payload: RepositoryEnabledSchema,
) -> RepositoryDetailSchema:
    repository = repository_for_request(request, repository_id)
    if repository.workspace.owner_id != request.user.pk:
        raise HttpError(403, "Only the workspace owner can change repository state.")
    if payload.enabled:
        validation = validate_repository(repository)
        if validation.status != "valid":
            raise HttpError(409, validation.message)
    repository.enabled = payload.enabled
    repository.save(update_fields=["enabled", "updated_at"])
    return serialize_repository_detail(request, repository)


@api.delete(
    "/repositories/{repository_id}",
    response={204: None},
    auth=django_auth,
)
def remove_repository(request: HttpRequest, repository_id: int) -> Status[None]:
    repository = repository_for_request(request, repository_id)
    if repository.workspace.owner_id != request.user.pk:
        raise HttpError(403, "Only the workspace owner can remove repositories.")
    repository.enabled = False
    repository.removed_at = timezone.now()
    repository.save(update_fields=["enabled", "removed_at", "updated_at"])
    repository.jobs.update(enabled=False)
    return Status(204, None)


def serialize_connection(
    connection: Connection, usage: ConnectionUsage | None = None
) -> ConnectionSchema:
    return ConnectionSchema(
        id=connection.id,
        provider=connection.provider,
        kind=connection.kind,
        binding_key=connection.binding_key,
        display_name=connection.display_name,
        status=connection.status,
        expires_at=connection.expires_at,
        available_tools=connection.available_tools,
        target=connection_target(connection),
        allowed_models=connection.allowed_models,
        default_model=connection.default_model,
        is_default=connection.is_default,
        available_models=list(connection.metadata.get("available_models", [])),
        last_check=connection.metadata.get("last_check"),
        usage=None
        if usage is None
        else ConnectionUsageSchema(
            **{**vars(usage), "spend_7d_usd": float(usage.spend_7d_usd)}
        ),
    )


def owner_workspace(request: HttpRequest, action: str) -> Workspace:
    workspace = user_workspace(request)
    if not workspace or workspace.owner_id != request.user.pk:
        raise HttpError(403, f"Only the workspace owner can {action}.")
    return workspace


@api.get("/connections", response=list[ConnectionSchema], auth=django_auth)
def connections(request: HttpRequest) -> list[ConnectionSchema]:
    workspace = user_workspace(request)
    if not workspace:
        return []
    usage = connection_usage(workspace)
    return [
        serialize_connection(item, usage.get(item.pk, ConnectionUsage()))
        for item in Connection.objects.filter(workspace=workspace)
    ]


@api.get(
    "/connections/mcp-providers", response=list[MCPProviderSchema], auth=django_auth
)
def list_mcp_providers(request: HttpRequest) -> list[MCPProviderSchema]:
    """The built-in MCP servers an owner can connect. Never includes secrets."""
    return [
        MCPProviderSchema(
            key=provider.key,
            label=provider.label,
            description=provider.description,
            default_binding=provider.default_binding,
            token_label=provider.token_label,
            token_hint=provider.token_hint,
            setup_url=provider.setup_url,
            setup_help=provider.setup_help,
            scope_label=provider.scope_label,
            missing_scope_message=provider.missing_scope_message,
            tools=list(provider.tools),
        )
        for provider in MCP_PROVIDERS.values()
    ]


@api.post("/connections/mcp", response=ConnectionSchema, auth=django_auth)
def create_mcp(
    request: HttpRequest, payload: MCPConnectionCreateSchema
) -> ConnectionSchema:
    workspace = owner_workspace(request, "add MCP connections")
    try:
        connection = create_mcp_connection(
            workspace,
            payload.provider,
            payload.binding_key,
            payload.display_name,
            payload.token,
        )
    except ConnectionConflict as error:
        raise HttpError(409, str(error)) from error
    except MCPError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, "The MCP connection configuration is invalid.") from error
    return serialize_connection(connection)


@api.post("/connections/webhook", response=ConnectionSchema, auth=django_auth)
def create_webhook(
    request: HttpRequest,
    payload: WebhookConnectionCreateSchema,
) -> ConnectionSchema:
    workspace = owner_workspace(request, "add connections")
    try:
        connection = create_webhook_connection(
            workspace,
            payload.binding_key,
            payload.display_name,
            payload.url,
        )
    except ConnectionConflict as error:
        raise HttpError(409, str(error)) from error
    except WebhookError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, " ".join(error.messages)) from error
    return serialize_connection(connection)


def owned_connection(
    request: HttpRequest, connection_id: int, action: str
) -> Connection:
    workspace = owner_workspace(request, action)
    return get_object_or_404(Connection, pk=connection_id, workspace=workspace)


@api.post(
    "/connections/{connection_id}/webhook",
    response=ConnectionSchema,
    auth=django_auth,
)
def update_webhook(
    request: HttpRequest,
    connection_id: int,
    payload: WebhookConnectionUpdateSchema,
) -> ConnectionSchema:
    connection = owned_connection(request, connection_id, "edit connections")
    try:
        connection = update_webhook_connection(
            connection, payload.display_name, payload.url
        )
    except ConnectionConflict as error:
        raise HttpError(409, str(error)) from error
    except WebhookError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, " ".join(error.messages)) from error
    return serialize_connection(connection)


@api.post("/connections/model", response=ConnectionSchema, auth=django_auth)
def create_model_provider(
    request: HttpRequest,
    payload: ModelConnectionCreateSchema,
) -> ConnectionSchema:
    workspace = owner_workspace(request, "add model providers")
    try:
        connection = create_model_connection(
            workspace,
            payload.provider,
            payload.display_name,
            payload.api_key,
            endpoint_url=payload.base_url,
            allowed_models=payload.allowed_models,
            default_model=payload.default_model,
            is_default=payload.is_default,
        )
    except ModelProviderError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, " ".join(error.messages)) from error
    return serialize_connection(connection)


@api.post(
    "/connections/{connection_id}/model",
    response=ConnectionSchema,
    auth=django_auth,
)
def update_model_provider(
    request: HttpRequest,
    connection_id: int,
    payload: ModelConnectionUpdateSchema,
) -> ConnectionSchema:
    connection = owned_connection(request, connection_id, "edit model providers")
    try:
        connection = update_model_connection(
            connection,
            display_name=payload.display_name,
            api_key=payload.api_key,
            endpoint_url=payload.base_url,
            allowed_models=payload.allowed_models,
            default_model=payload.default_model,
            is_default=payload.is_default,
        )
    except ModelProviderError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, " ".join(error.messages)) from error
    return serialize_connection(connection)


@api.post(
    "/connections/{connection_id}/revoke",
    response=ConnectionSchema,
    auth=django_auth,
)
def revoke(request: HttpRequest, connection_id: int) -> ConnectionSchema:
    connection = owned_connection(request, connection_id, "disconnect connections")
    if connection.kind == Connection.Kind.IDENTITY:
        raise HttpError(409, "Sign-in connections are managed by their provider.")
    return serialize_connection(revoke_connection(connection))


@api.delete(
    "/connections/{connection_id}",
    response={204: None},
    auth=django_auth,
)
def remove_connection(request: HttpRequest, connection_id: int) -> Status[None]:
    connection = owned_connection(request, connection_id, "delete connections")
    try:
        delete_connection(connection)
    except ConnectionConflict as error:
        raise HttpError(409, str(error)) from error
    return Status(204, None)


@api.post(
    "/connections/{connection_id}/test",
    response=ConnectionTestSchema,
    auth=django_auth,
)
def test_connection(request: HttpRequest, connection_id: int) -> ConnectionTestSchema:
    connection = owned_connection(request, connection_id, "test connections")
    if connection.kind == Connection.Kind.MCP:
        try:
            provider = validate_mcp_connection(connection)
            tools = discover_tools(connection)
        except MCPError as error:
            return ConnectionTestSchema(ok=False, status_code=None, message=str(error))
        connection.available_tools = sorted(tools)
        connection.metadata = {**connection.metadata, "tools": tools}
        connection.save(update_fields=["available_tools", "metadata", "updated_at"])
        return ConnectionTestSchema(
            ok=True,
            status_code=None,
            message=f"Discovered {len(tools)} supported {provider.label} tools.",
        )
    if connection.kind == Connection.Kind.MODEL:
        check = check_model_connection(connection)
        return ConnectionTestSchema(
            ok=check.ok, status_code=None, message=check.message, models=check.models
        )
    if connection.provider != Connection.Provider.WEBHOOK:
        raise HttpError(400, "Testing is not available for this connection yet.")
    delivery = deliver_webhook(connection, connection_test_payload(connection))
    return ConnectionTestSchema(
        ok=delivery.ok,
        status_code=delivery.status_code,
        message=delivery.message,
    )


@api.get("/capability-needs", response=list[CapabilityNeedSchema], auth=django_auth)
def needs(request: HttpRequest) -> list[CapabilityNeedSchema]:
    workspace = user_workspace(request)
    if not workspace:
        return []
    return [
        CapabilityNeedSchema(
            binding=need.binding,
            kind=need.kind,
            required=need.required,
            requested_tools=need.requested_tools,
            status=need.status,
            detail=need.detail,
            connection=(
                serialize_connection(need.connection) if need.connection else None
            ),
            jobs=[NeedJobSchema(**vars(job)) for job in need.jobs],
        )
        for need in capability_needs(workspace)
    ]


@api.get("/secrets", response=list[SecretSchema], auth=django_auth)
def secrets(request: HttpRequest) -> list[SecretSchema]:
    workspace = user_workspace(request)
    if not workspace:
        return []
    return [
        SecretSchema(
            name=item.name,
            description=item.description,
            last_used_at=item.last_used_at,
        )
        for item in Secret.objects.filter(workspace=workspace).defer("value")
    ]
