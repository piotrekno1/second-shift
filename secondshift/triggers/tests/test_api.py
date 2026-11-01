import hashlib
import hmac
import json
import time

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.test import Client

from secondshift.connections.models import Connection
from secondshift.jobs.models import CapabilityRequest, Job
from secondshift.repositories.models import Repository
from secondshift.runs.models import Run
from secondshift.triggers.models import JobTrigger, TriggerEvent
from secondshift.workspaces.models import WorkspaceMembership
from secondshift.workspaces.services import create_workspace

ALERT = {"status": "firing", "alerts": [{"fingerprint": "abc"}]}


@pytest.fixture
def owner(db: object) -> AbstractUser:
    return get_user_model().objects.create_user(username="owner")


@pytest.fixture
def job(owner: AbstractUser) -> Job:
    workspace = create_workspace(name="Test", slug="test", owner=owner)
    repository = Repository.objects.create(
        workspace=workspace,
        full_name="example/project",
        enabled=True,
        config_sha="abc123",
    )
    Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.ANTHROPIC,
        kind=Connection.Kind.MODEL,
        display_name="Anthropic",
        status=Connection.Status.CONNECTED,
        allowed_models=["claude-opus-5-5"],
        default_model="claude-opus-5-5",
        is_default=True,
    )
    return Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="triage",
        name="Triage",
        skill_path=".agents/skills/triage/SKILL.md",
        config_hash="hash",
        triggers=["webhook"],
    )


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    started: list[int] = []
    monkeypatch.setattr(
        "secondshift.runs.services.enqueue_run", lambda run: started.append(run.pk)
    )
    return started


def turn_on(client: Client, owner: AbstractUser, job: Job) -> dict[str, object]:
    client.force_login(owner)
    response = client.post(
        f"/api/jobs/{job.pk}/triggers/webhook/enabled",
        {"enabled": True},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    client.logout()
    return response.json()


def path_of(change: dict[str, object]) -> str:
    url = str(change["trigger"]["url"])  # type: ignore[index]
    return url[url.index("/api/") :]


def send(client: Client, path: str, secret: str | None, body: object = ALERT) -> object:
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    return client.post(
        path, json.dumps(body), content_type="application/json", headers=headers
    )


def test_turning_a_trigger_on_shows_its_secret_once(
    client: Client, owner: AbstractUser, job: Job
) -> None:
    change = turn_on(client, owner, job)

    secret = str(change["secret"])
    assert secret.startswith("sst_")
    client.force_login(owner)
    listed = client.get(f"/api/jobs/{job.pk}/triggers").json()
    assert listed[0]["enabled"] is True
    assert listed[0]["url"] == change["trigger"]["url"]  # type: ignore[index]
    assert secret not in json.dumps(listed)
    # The URL holds a random id, not the job's.
    assert path_of(change) == f"/api/triggers/{JobTrigger.objects.get().public_id}"


def test_webhook_starts_one_run(
    client: Client, owner: AbstractUser, job: Job, queued: list[int]
) -> None:
    change = turn_on(client, owner, job)

    response = send(client, path_of(change), str(change["secret"]))

    assert response.status_code == 202
    run = Run.objects.get(pk=response.json()["run_id"])
    assert run.started_by == "webhook"
    assert queued == [run.pk]
    event = TriggerEvent.objects.get()
    assert (event.outcome, event.run_id) == ("started", run.pk)


def test_grafana_signed_request_starts_a_run(
    client: Client, owner: AbstractUser, job: Job, queued: list[int]
) -> None:
    change = turn_on(client, owner, job)
    body = json.dumps(ALERT).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(
        str(change["secret"]).encode(), f"{timestamp}:".encode() + body, hashlib.sha256
    ).hexdigest()

    response = client.post(
        path_of(change),
        body,
        content_type="application/json",
        headers={
            "X-Grafana-Alerting-Signature": signature,
            "X-Grafana-Alerting-Timestamp": timestamp,
        },
    )

    assert response.status_code == 202


@pytest.mark.parametrize("secret", [None, "sst_wrong"])
def test_missing_or_wrong_token_is_refused(
    client: Client, owner: AbstractUser, job: Job, queued: list[int], secret: str | None
) -> None:
    change = turn_on(client, owner, job)

    response = send(client, path_of(change), secret)

    assert response.status_code == 401
    assert queued == []
    assert TriggerEvent.objects.get().outcome == "refused"


def test_unknown_trigger_is_not_found(client: Client, job: Job) -> None:
    assert send(client, "/api/triggers/nothing-here", "sst_x").status_code == 404


def test_turned_off_trigger_is_refused(
    client: Client, owner: AbstractUser, job: Job, queued: list[int]
) -> None:
    change = turn_on(client, owner, job)
    client.force_login(owner)
    client.post(
        f"/api/jobs/{job.pk}/triggers/webhook/enabled",
        {"enabled": False},
        content_type="application/json",
    )
    client.logout()

    response = send(client, path_of(change), str(change["secret"]))

    assert response.status_code == 409
    assert response.json()["detail"] == "This trigger is turned off."
    assert queued == []


def test_rotating_the_secret_refuses_the_old_one(
    client: Client, owner: AbstractUser, job: Job, queued: list[int]
) -> None:
    change = turn_on(client, owner, job)
    client.force_login(owner)
    rotated = client.post(f"/api/jobs/{job.pk}/triggers/webhook/rotate").json()
    client.logout()

    assert rotated["secret"] != change["secret"]
    assert rotated["trigger"]["url"] == change["trigger"]["url"]  # type: ignore[index]
    assert send(client, path_of(change), str(change["secret"])).status_code == 401
    assert send(client, path_of(change), rotated["secret"]).status_code == 202


def test_job_that_is_not_ready_refuses_with_the_reason(
    client: Client, owner: AbstractUser, job: Job, queued: list[int]
) -> None:
    CapabilityRequest.objects.create(
        job=job,
        binding="grafana",
        kind=CapabilityRequest.Kind.MCP,
        requested_tools=["query_prometheus"],
    )
    change = turn_on(client, owner, job)

    response = send(client, path_of(change), str(change["secret"]))

    assert response.status_code == 409
    assert "`grafana` waits for an owner's approval" in response.json()["detail"]
    assert "grafana" in TriggerEvent.objects.get().reason
    assert queued == []


def test_second_request_while_a_run_is_active_is_refused(
    client: Client, owner: AbstractUser, job: Job, queued: list[int]
) -> None:
    change = turn_on(client, owner, job)
    first = send(client, path_of(change), str(change["secret"])).json()

    response = send(client, path_of(change), str(change["secret"]))

    assert response.status_code == 409
    assert f"Run #{first['run_id']}" in response.json()["detail"]
    assert len(queued) == 1


def test_trigger_the_configuration_dropped_is_refused(
    client: Client, owner: AbstractUser, job: Job, queued: list[int]
) -> None:
    change = turn_on(client, owner, job)
    Job.objects.filter(pk=job.pk).update(triggers=[])

    response = send(client, path_of(change), str(change["secret"]))

    assert response.status_code == 409
    assert "no longer names" in response.json()["detail"]


def test_disabled_repository_is_refused(
    client: Client, owner: AbstractUser, job: Job, queued: list[int]
) -> None:
    change = turn_on(client, owner, job)
    Repository.objects.filter(pk=job.repository_id).update(enabled=False)

    response = send(client, path_of(change), str(change["secret"]))

    assert response.status_code == 409
    assert queued == []


def test_large_body_is_refused(
    client: Client, owner: AbstractUser, job: Job, queued: list[int], settings
) -> None:
    settings.TRIGGER_MAX_BODY_BYTES = 100
    change = turn_on(client, owner, job)

    response = send(client, path_of(change), str(change["secret"]), {"a": "x" * 200})

    assert response.status_code == 413
    assert queued == []


def test_only_the_newest_events_are_kept(
    client: Client, owner: AbstractUser, job: Job, settings
) -> None:
    settings.TRIGGER_EVENTS_KEPT = 3
    change = turn_on(client, owner, job)

    for _ in range(5):
        send(client, path_of(change), "sst_wrong")

    assert TriggerEvent.objects.count() == 3


def test_configuration_must_name_the_trigger(
    client: Client, owner: AbstractUser, job: Job
) -> None:
    Job.objects.filter(pk=job.pk).update(triggers=[])
    client.force_login(owner)

    response = client.post(
        f"/api/jobs/{job.pk}/triggers/webhook/enabled",
        {"enabled": True},
        content_type="application/json",
    )

    assert response.status_code == 409
    assert "triggers: [webhook]" in response.json()["detail"]
    assert not JobTrigger.objects.exists()


def test_members_cannot_manage_triggers(client: Client, job: Job) -> None:
    member = get_user_model().objects.create_user(username="member")
    WorkspaceMembership.objects.create(
        workspace=job.workspace, user=member, role=WorkspaceMembership.Role.MEMBER
    )
    client.force_login(member)

    response = client.post(
        f"/api/jobs/{job.pk}/triggers/webhook/enabled",
        {"enabled": True},
        content_type="application/json",
    )

    assert response.status_code == 403
    assert not JobTrigger.objects.exists()


def test_another_workspace_cannot_see_or_change_triggers(
    client: Client, owner: AbstractUser, job: Job
) -> None:
    turn_on(client, owner, job)
    stranger = get_user_model().objects.create_user(username="stranger")
    create_workspace(name="Other", slug="other", owner=stranger)
    client.force_login(stranger)

    assert client.get(f"/api/jobs/{job.pk}/triggers").status_code == 404
    assert client.post(f"/api/jobs/{job.pk}/triggers/webhook/rotate").status_code == 404
