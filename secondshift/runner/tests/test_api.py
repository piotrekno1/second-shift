from unittest.mock import Mock

import pytest
from django.test import Client

from secondshift.runner.tests.fakes import sandboxes
from secondshift.runner_protocol.sessions import CommandResult

pytestmark = pytest.mark.urls("secondshift.runner.urls")


@pytest.mark.django_db
def test_runner_api_requires_service_auth(settings, monkeypatch) -> None:
    service = Mock()
    service.start.return_value = "run-7"
    monkeypatch.setattr("secondshift.runner.api.get_runner_service", lambda: service)
    payload = {
        "run_id": 7,
        "session_key": "0123456789abcdef0123456789abcdef",
        "repository": "example/project",
        "commit_sha": "a" * 40,
        "environment": "default",
        "source_sha256": "d" * 64,
    }

    denied = Client().post("/v1/sessions", payload, content_type="application/json")
    allowed = Client().post(
        "/v1/sessions",
        payload,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {settings.RUNNER_SERVICE_TOKEN}",
    )

    assert denied.status_code == 401
    assert allowed.status_code == 201
    assert allowed.json() == {"session_id": "run-7"}


@pytest.mark.django_db
def test_the_runner_lists_its_environments_to_the_control_plane_only(
    settings,
) -> None:
    settings.RUNNER_ENVIRONMENTS = sandboxes(
        "secondshift-runner:test", node="registry.example/node@sha256:def"
    )

    denied = Client().get("/v1/environments")
    allowed = Client().get(
        "/v1/environments",
        HTTP_AUTHORIZATION=f"Bearer {settings.RUNNER_SERVICE_TOKEN}",
    )

    assert denied.status_code == 401
    assert allowed.json() == {
        "environments": {
            "python": "secondshift-runner:test",
            "node": "registry.example/node@sha256:def",
        },
        "default": "python",
    }


@pytest.mark.django_db
def test_runner_does_not_expose_interactive_api_docs() -> None:
    client = Client()

    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


@pytest.mark.django_db
def test_runner_api_rejects_repository_runtime_configuration(
    settings, monkeypatch
) -> None:
    service = Mock()
    monkeypatch.setattr("secondshift.runner.api.get_runner_service", lambda: service)

    response = Client().post(
        "/v1/sessions",
        {
            "run_id": 7,
            "session_key": "0123456789abcdef0123456789abcdef",
            "repository": "example/project",
            "commit_sha": "a" * 40,
            "environment": "default",
            # A request never chooses the image. The runner maps the name.
            "image": "attacker.example/image:latest",
            "network": "host",
            "mounts": ["/:/workspace"],
        },
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {settings.RUNNER_SERVICE_TOKEN}",
    )

    assert response.status_code == 422
    service.start.assert_not_called()


@pytest.mark.django_db
def test_command_result_is_returned_without_exposing_runner_state(
    settings, monkeypatch
) -> None:
    service = Mock()
    service.execute.return_value = CommandResult("call-1", 0, "ok")
    monkeypatch.setattr("secondshift.runner.api.get_runner_service", lambda: service)

    response = Client().post(
        "/v1/sessions/run-7/commands",
        {"call_id": "call-1", "argv": ["git", "status"]},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {settings.RUNNER_SERVICE_TOKEN}",
    )

    assert response.status_code == 200
    assert response.json() == {
        "call_id": "call-1",
        "exit_code": 0,
        "output": "ok",
        "timed_out": False,
    }


@pytest.mark.django_db
def test_runner_api_streams_a_collection_only_with_service_auth(
    settings, monkeypatch, tmp_path
) -> None:
    archive = tmp_path / "collection.tar.gz"
    archive.write_bytes(b"archive bytes")
    service = Mock()
    service.collect.return_value = archive
    monkeypatch.setattr("secondshift.runner.api.get_runner_service", lambda: service)

    denied = Client().post("/v1/sessions/run-7/collection")
    allowed = Client().post(
        "/v1/sessions/run-7/collection",
        HTTP_AUTHORIZATION=f"Bearer {settings.RUNNER_SERVICE_TOKEN}",
    )

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed["Content-Type"] == "application/gzip"
    assert b"".join(allowed.streaming_content) == b"archive bytes"
    service.collect.assert_called_once_with("run-7")
