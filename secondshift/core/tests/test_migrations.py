"""Check fresh schemas and the separately deployed migration graphs."""

import pytest
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import override_settings


def test_control_plane_migrations_do_not_require_runner() -> None:
    control_plane_apps = [
        app for app in settings.INSTALLED_APPS if app != "secondshift.runner"
    ]
    with override_settings(INSTALLED_APPS=control_plane_apps):
        loader = MigrationLoader(None)
        assert ("runs", "0001_initial") in loader.graph.leaf_nodes()
        assert not any(
            app in {"runner", "automations", "integrations"}
            for app, name in loader.graph.nodes
        )


def test_runner_migrations_do_not_require_control_plane() -> None:
    with override_settings(
        INSTALLED_APPS=["django.contrib.contenttypes", "secondshift.runner"]
    ):
        loader = MigrationLoader(None)
        assert "runner" in {app for app, name in loader.graph.leaf_nodes()}
        assert {app for app, name in loader.graph.nodes} == {"contenttypes", "runner"}


@pytest.mark.django_db
def test_migrate_creates_the_cache_table() -> None:
    assert "secondshift_cache" in connection.introspection.table_names()

    cache.set("probe", "shared")

    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM secondshift_cache")
        assert cursor.fetchone() == (1,)
    assert cache.get("probe") == "shared"


@pytest.mark.django_db
def test_fresh_database_uses_current_app_tables_and_content_types() -> None:
    tables = set(connection.introspection.table_names())
    assert {
        "connections_connection",
        "connections_secret",
        "connections_oauthauthorization",
        "jobs_job",
        "jobs_capabilityrequest",
        "jobs_jobgrant",
        "runs_run",
        "runs_runevent",
        "workspaces_workspace",
        "runner_runnersession",
    } <= tables
    assert not any(
        name.startswith(("automations_", "integrations_", "core_workspace"))
        for name in tables
    )
    assert not ContentType.objects.filter(
        app_label__in=["automations", "integrations"]
    ).exists()
