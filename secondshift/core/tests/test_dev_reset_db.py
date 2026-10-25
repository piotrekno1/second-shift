from typing import Any

import click
import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from secondshift.connections.models import Connection
from secondshift.core.management.commands import dev_reset_db
from secondshift.jobs.models import Job
from secondshift.repositories.models import Repository
from secondshift.runs.models import Run, RunEvent


def test_dev_reset_db_requires_development_and_confirmation(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.DEV = False
    with pytest.raises(click.ClickException, match="DJANGO_ENV=dev"):
        call_command("dev_reset_db", yes=True)

    settings.DEV = True
    with pytest.raises(click.ClickException, match="Pass --yes"):
        call_command("dev_reset_db")


def test_dev_reset_db_uses_framework_reset_then_seeds(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.DEV = True
    commands: list[tuple[str, dict[str, Any]]] = []
    seeded: list[bool] = []
    monkeypatch.setattr(
        dev_reset_db,
        "call_command",
        lambda name, **options: commands.append((name, options)),
    )
    monkeypatch.setattr(dev_reset_db, "seed_demo_data", lambda: seeded.append(True))

    call_command("dev_reset_db", yes=True)

    assert [name for name, _ in commands] == ["reset_db", "migrate", "bootstrap_dev"]
    assert commands[0][1]["close_sessions"] is True
    assert seeded == [True]


@pytest.mark.django_db
def test_seed_demo_data_creates_credential_free_disabled_jobs(settings: Any) -> None:
    settings.DEV = True
    settings.ARTIFACT_STORAGE = "filesystem"
    call_command("bootstrap_dev")

    dev_reset_db.seed_demo_data()

    assert get_user_model().objects.filter(is_superuser=True).count() == 1
    assert Repository.objects.count() == 3
    assert all(
        len(repository.config_sha) == 40 for repository in Repository.objects.all()
    )
    assert Job.objects.count() == 4
    assert Job.objects.filter(enabled=True).count() == 0
    assert Run.objects.count() == 4
    assert RunEvent.objects.count() == 4
    assert Connection.objects.filter(metadata={"demo": True}).count() == 3
    assert all(connection.access_token == "" for connection in Connection.objects.all())
