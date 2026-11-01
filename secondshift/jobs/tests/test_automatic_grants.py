import json
from importlib import import_module
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.test import Client

from secondshift.connections.models import Connection
from secondshift.jobs.automatic_grants import set_require_approval
from secondshift.jobs.grants import decide_grant
from secondshift.jobs.manifest import parse_manifest
from secondshift.jobs.models import CapabilityRequest, JobGrant
from secondshift.jobs.sync import sync_jobs
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace, WorkspaceMembership
from secondshift.workspaces.services import create_workspace

COMMIT = "a" * 40


def manifest(tools: list[str], binding: str = "billing") -> str:
    return f"""
jobs:
  review:
    name: Review
    skill: .agents/skills/review/SKILL.md
    requires:
      apis:
        - binding: {binding}
          tools: [{", ".join(tools)}]
""".strip()


@pytest.fixture
def owner(db: object) -> AbstractUser:
    return get_user_model().objects.create_user(username="owner")


@pytest.fixture
def workspace(owner: AbstractUser) -> Workspace:
    return create_workspace(name="Test", slug="test", owner=owner)


@pytest.fixture
def repository(workspace: Workspace) -> Repository:
    return Repository.objects.create(
        workspace=workspace, full_name="example/project", config_sha=COMMIT
    )


def api_connection(workspace: Workspace, **fields: Any) -> Connection:
    defaults: dict[str, Any] = {
        "provider": Connection.Provider.API,
        "kind": Connection.Kind.API,
        "binding_key": "billing",
        "display_name": "Billing",
        "available_tools": ["listCharges"],
        "metadata": {"base_url": "https://billing.example.com"},
    }
    return Connection.objects.create(workspace=workspace, **{**defaults, **fields})


def sync(repository: Repository, text: str) -> None:
    sync_jobs(repository, parse_manifest(text))


def grant() -> JobGrant:
    return JobGrant.objects.get(capability_request__binding="billing")


def test_new_connections_do_not_require_approval(workspace: Workspace) -> None:
    assert api_connection(workspace).require_approval is False


def test_existing_connections_keep_requiring_approval() -> None:
    migration = import_module(
        "secondshift.connections.migrations.0003_require_approval"
    ).Migration
    added = next(
        operation
        for operation in migration.operations
        if getattr(operation, "model_name", "") == "connection"
        and operation.name == "require_approval"
    )
    assert added.field.default is True


def test_sync_approves_allowed_tools_without_an_owner(
    repository: Repository,
) -> None:
    connection = api_connection(repository.workspace)

    sync(repository, manifest(["listCharges"]))

    saved = grant()
    assert saved.status == JobGrant.Status.APPROVED
    assert saved.connection == connection
    assert saved.allowed_tools == ["listCharges"]
    assert saved.reviewed_by is None
    assert saved.reviewed_at is not None


def test_a_tool_outside_the_allowed_list_waits_for_an_owner(
    repository: Repository,
) -> None:
    api_connection(repository.workspace)

    sync(repository, manifest(["listCharges", "createRefund"]))

    assert grant().status == JobGrant.Status.PENDING


def test_connections_that_require_approval_wait_for_an_owner(
    repository: Repository,
) -> None:
    api_connection(repository.workspace, require_approval=True)

    sync(repository, manifest(["listCharges"]))

    assert grant().status == JobGrant.Status.PENDING


def test_a_disconnected_connection_is_never_granted(repository: Repository) -> None:
    api_connection(repository.workspace, status=Connection.Status.REVOKED)

    sync(repository, manifest(["listCharges"]))

    assert grant().status == JobGrant.Status.PENDING


def test_turning_approval_off_approves_waiting_requests(repository: Repository) -> None:
    connection = api_connection(repository.workspace, require_approval=True)
    sync(repository, manifest(["listCharges"]))

    set_require_approval(connection, False)

    assert grant().status == JobGrant.Status.APPROVED


def test_turning_approval_on_keeps_grants_and_reviews_changes(
    repository: Repository,
) -> None:
    connection = api_connection(repository.workspace)
    connection.available_tools = ["listCharges", "getCharge"]
    connection.save()
    sync(repository, manifest(["listCharges"]))

    set_require_approval(connection, True)
    sync(repository, manifest(["listCharges"]))
    assert grant().status == JobGrant.Status.APPROVED

    sync(repository, manifest(["listCharges", "getCharge"]))
    assert grant().status == JobGrant.Status.PENDING


def test_an_owner_denial_is_kept(repository: Repository, owner: AbstractUser) -> None:
    connection = api_connection(repository.workspace, require_approval=True)
    sync(repository, manifest(["listCharges"]))
    request = CapabilityRequest.objects.get(binding="billing")
    decide_grant(request, JobGrant.Status.DENIED, connection, None, owner)

    set_require_approval(connection, False)
    sync(repository, manifest(["listCharges"]))

    assert grant().status == JobGrant.Status.DENIED


def test_an_owner_approval_records_the_reviewer(
    repository: Repository, owner: AbstractUser
) -> None:
    connection = api_connection(repository.workspace, require_approval=True)
    sync(repository, manifest(["listCharges"]))
    request = CapabilityRequest.objects.get(binding="billing")

    decide_grant(request, JobGrant.Status.APPROVED, connection, None, owner)

    assert grant().reviewed_by == owner


def test_repository_input_cannot_turn_off_approval_or_pick_a_connection() -> None:
    for extra in ("require_approval: false", "connection: 1"):
        text = manifest(["listCharges"]) + f"\n          {extra}"
        with pytest.raises(ValueError):
            parse_manifest(text)


def webhook_payload(**fields: Any) -> str:
    return json.dumps(
        {"binding_key": "alerts", "url": "https://hooks.example.com/x", **fields}
    )


NOTIFY = """
jobs:
  review:
    name: Review
    skill: .agents/skills/review/SKILL.md
    notify:
      - binding: alerts
""".strip()


@pytest.mark.django_db
def test_creating_a_connection_approves_jobs_waiting_for_it(
    client: Client, settings: Any, repository: Repository, owner: AbstractUser
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = True
    sync(repository, NOTIFY)
    client.force_login(owner)

    response = client.post(
        "/api/connections/webhook",
        data=webhook_payload(),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["require_approval"] is False
    saved = JobGrant.objects.get(capability_request__binding="alerts")
    assert saved.status == JobGrant.Status.APPROVED


@pytest.mark.django_db
def test_owner_can_create_a_connection_that_requires_approval(
    client: Client, settings: Any, repository: Repository, owner: AbstractUser
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = True
    sync(repository, NOTIFY)
    client.force_login(owner)

    response = client.post(
        "/api/connections/webhook",
        data=webhook_payload(require_approval=True),
        content_type="application/json",
    )

    assert response.json()["require_approval"] is True
    assert not JobGrant.objects.filter(status=JobGrant.Status.APPROVED).exists()


@pytest.mark.django_db
def test_only_owners_can_change_the_setting(
    client: Client, workspace: Workspace
) -> None:
    connection = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.WEBHOOK,
        kind=Connection.Kind.NOTIFICATION,
        binding_key="alerts",
        display_name="Alerts",
        require_approval=True,
    )
    admin = get_user_model().objects.create_user(username="admin")
    workspace.memberships.create(user=admin, role=WorkspaceMembership.Role.ADMIN)
    client.force_login(admin)

    response = client.post(
        f"/api/connections/{connection.pk}/webhook",
        data=json.dumps({"require_approval": False}),
        content_type="application/json",
    )

    assert response.status_code == 403
    connection.refresh_from_db()
    assert connection.require_approval is True


def test_asking_for_more_allowed_tools_stays_approved(repository: Repository) -> None:
    connection = api_connection(repository.workspace)
    connection.available_tools = ["listCharges", "getCharge"]
    connection.save()
    sync(repository, manifest(["listCharges"]))

    sync(repository, manifest(["listCharges", "getCharge"]))

    assert grant().status == JobGrant.Status.APPROVED
    assert grant().allowed_tools == ["getCharge", "listCharges"]


def test_asking_for_a_tool_outside_the_list_sends_a_grant_back(
    repository: Repository,
) -> None:
    api_connection(repository.workspace)
    sync(repository, manifest(["listCharges"]))

    sync(repository, manifest(["listCharges", "createRefund"]))

    assert grant().status == JobGrant.Status.PENDING
    assert grant().reviewed_at is None


def test_removed_repositories_are_never_granted(
    repository: Repository,
) -> None:
    connection = api_connection(repository.workspace, require_approval=True)
    sync(repository, manifest(["listCharges"]))
    repository.removed_at = repository.created_at
    repository.save()

    set_require_approval(connection, False)

    assert grant().status == JobGrant.Status.PENDING


@pytest.mark.django_db
def test_allowing_a_tool_approves_jobs_waiting_for_it(
    client: Client, repository: Repository, owner: AbstractUser
) -> None:
    offered = {
        "listCharges": {"readOnly": True, "description": "GET /charges"},
        "createRefund": {"readOnly": False, "description": "POST /refunds"},
    }
    connection = api_connection(
        repository.workspace,
        access_token="token",
        metadata={"base_url": "https://billing.example.com", "offered_tools": offered},
    )
    sync(repository, manifest(["listCharges", "createRefund"]))
    assert grant().status == JobGrant.Status.PENDING
    client.force_login(owner)

    response = client.post(
        f"/api/connections/{connection.pk}/api",
        data=json.dumps({"operations": ["listCharges", "createRefund"]}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert grant().status == JobGrant.Status.APPROVED
