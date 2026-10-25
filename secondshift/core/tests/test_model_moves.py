"""Exercise upgrades and rollback against real historical models and rows."""

from collections.abc import Iterator

import pytest
from django.conf import settings as django_settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import override_settings
from django.utils import timezone

from secondshift.core.model_moves import MODEL_APP_MOVES

OLD_TARGETS = [
    ("connections", None),
    ("jobs", None),
    ("runs", None),
    ("automations", "0004_alter_job_workspace_squashed_0005_run_runner_session_key"),
    (
        "integrations",
        "0002_alter_connection_workspace_and_more_squashed_0003_alter_connection_provider_and_more",
    ),
    (
        "repositories",
        "0002_alter_repository_workspace_squashed_0003_repository_auto_sync",
    ),
    ("gateway", "0001_initial"),
]


@pytest.fixture
def migrated_database(transactional_db: object) -> Iterator[MigrationExecutor]:
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate(OLD_TARGETS)
        yield MigrationExecutor(connection)
    finally:
        MigrationExecutor(connection).migrate(latest)


def test_app_moves_preserve_data_permissions_and_tables(
    migrated_database: MigrationExecutor,
) -> None:
    executor = migrated_database
    latest = executor.loader.graph.leaf_nodes()
    old = executor.loader.project_state(
        [
            node
            for node in executor.loader.applied_migrations
            if node in executor.loader.graph.nodes
        ]
    ).apps
    user = old.get_model("auth", "User").objects.create(username="migration-owner")
    workspace = old.get_model("workspaces", "Workspace").objects.create(
        name="Migration workspace", slug="migration-workspace"
    )
    connection_row = old.get_model("integrations", "Connection").objects.create(
        workspace=workspace, provider="github", binding_key="source", kind="mcp"
    )
    repository = old.get_model("repositories", "Repository").objects.create(
        workspace=workspace,
        full_name="example/migration",
        github_connection=connection_row,
    )
    job = old.get_model("automations", "Job").objects.create(
        workspace=workspace, repository=repository, key="review", name="Review"
    )
    capability = old.get_model("automations", "CapabilityRequest").objects.create(
        job=job, binding="source", kind="mcp", requested_tools=["read"]
    )
    grant = old.get_model("automations", "JobGrant").objects.create(
        capability_request=capability,
        connection=connection_row,
        status="approved",
        allowed_tools=["read"],
        reviewed_by=user,
    )
    run = old.get_model("automations", "Run").objects.create(
        job=job,
        scheduled_for=timezone.now(),
        execution_plan={"ready": True, "frozen": "keep"},
        output_path="runs/report.md",
        dbos_workflow_id="secondshift-run-1",
    )
    event = old.get_model("automations", "RunEvent").objects.create(
        run=run, message="Existing audit event"
    )
    token = old.get_model("gateway", "RunToken").objects.create(
        run=run,
        connection=connection_row,
        token_hash="a" * 64,
        expires_at=timezone.now(),
        budget_tokens=100,
    )
    content_type = old.get_model("contenttypes", "ContentType").objects.get(
        app_label="automations", model="job"
    )
    permission = old.get_model("auth", "Permission").objects.get(
        content_type=content_type, codename="change_job"
    )
    user.user_permissions.add(permission)
    secret = old.get_model("integrations", "Secret").objects.create(
        workspace=workspace, name="migration-secret", value="synthetic-test-secret"
    )
    authorization = old.get_model("integrations", "OAuthAuthorization").objects.create(
        workspace=workspace,
        user=user,
        connection=connection_row,
        state="migration-state",
        code_verifier="synthetic-test-verifier",
        provider="github",
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT value FROM integrations_secret WHERE id = %s", [secret.pk]
        )
        encrypted_before = cursor.fetchone()[0]
    content_type_ids = {
        (target, model): old.get_model("contenttypes", "ContentType")
        .objects.get(app_label=source, model=model)
        .pk
        for source, target, models in MODEL_APP_MOVES
        for model in models
    }
    tables_before = set(connection.introspection.table_names())

    executor.migrate(latest)
    new = MigrationExecutor(connection).loader.project_state().apps
    assert set(connection.introspection.table_names()) == tables_before
    assert (
        new.get_model("jobs", "Job").objects.get(pk=job.pk).repository_id
        == repository.pk
    )
    assert (
        new.get_model("jobs", "JobGrant").objects.get(pk=grant.pk).connection_id
        == connection_row.pk
    )
    current = new.get_model("runs", "Run").objects.get(pk=run.pk)
    assert current.execution_plan == run.execution_plan
    assert current.runner_session_key == run.runner_session_key
    assert current.output_path == run.output_path
    assert current.dbos_workflow_id == run.dbos_workflow_id
    assert new.get_model("runs", "RunEvent").objects.get(pk=event.pk).run_id == run.pk
    assert (
        new.get_model("gateway", "RunToken").objects.get(pk=token.pk).run_id == run.pk
    )
    assert (
        new.get_model("repositories", "Repository")
        .objects.get(pk=repository.pk)
        .github_connection_id
        == connection_row.pk
    )
    assert (
        new.get_model("connections", "OAuthAuthorization")
        .objects.get(pk=authorization.pk)
        .connection_id
        == connection_row.pk
    )
    assert (
        new.get_model("connections", "Secret").objects.get(pk=secret.pk).value
        == "synthetic-test-secret"
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT value FROM integrations_secret WHERE id = %s", [secret.pk]
        )
        assert cursor.fetchone()[0] == encrypted_before
    for (app_label, model), primary_key in content_type_ids.items():
        assert (
            new.get_model("contenttypes", "ContentType")
            .objects.get(app_label=app_label, model=model)
            .pk
            == primary_key
        )
    moved_type = new.get_model("contenttypes", "ContentType").objects.get(
        pk=content_type.pk
    )
    assert (moved_type.app_label, moved_type.model) == ("jobs", "job")
    assert (
        new.get_model("auth", "User")
        .objects.get(pk=user.pk)
        .user_permissions.filter(pk=permission.pk)
        .exists()
    )

    MigrationExecutor(connection).migrate(OLD_TARGETS)
    assert (
        old.get_model("automations", "Run").objects.get(pk=run.pk).execution_plan
        == run.execution_plan
    )
    assert (
        old.get_model("contenttypes", "ContentType")
        .objects.get(pk=content_type.pk)
        .app_label
        == "automations"
    )
    assert (
        old.get_model("auth", "User")
        .objects.get(pk=user.pk)
        .user_permissions.filter(pk=permission.pk)
        .exists()
    )
    assert set(connection.introspection.table_names()) == tables_before


def test_control_plane_migration_graph_does_not_require_runner() -> None:

    control_plane_apps = [
        app for app in django_settings.INSTALLED_APPS if app != "secondshift.runner"
    ]
    with override_settings(INSTALLED_APPS=control_plane_apps):
        loader = MigrationLoader(None)
        assert (
            "runs",
            "0002_transfer_model_content_types",
        ) in loader.graph.leaf_nodes()
        assert not any(app == "runner" for app, name in loader.graph.nodes)
