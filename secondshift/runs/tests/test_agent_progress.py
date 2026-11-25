import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import RequestFactory

from secondshift.agent.image.progress import PROGRESS_METHOD
from secondshift.runs.dao import with_model_usage
from secondshift.runs.execution.agent_broker import agent_mcp
from secondshift.runs.execution.cli_agent import execute_cli_step
from secondshift.runs.models import AgentLease, Run, RunEvent
from secondshift.runs.schemas import RunSchema
from secondshift.runs.serialization import serialize_run, serialize_run_detail
from secondshift.runs.tests.test_cli_execution import fake_runner

pytestmark = pytest.mark.django_db

USAGE = {
    "input_tokens": 1_000,
    "output_tokens": 2_000,
    "cache_read_input_tokens": 50_000,
}


@pytest.fixture(autouse=True)
def agent_auth(settings: Any, tmp_path: Path) -> None:
    settings.STORAGES = {
        **settings.STORAGES,
        "run_artifacts": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    }
    settings.AGENT_AUTH_ROOT = tmp_path / "agent-auth"


@pytest.fixture
def leased_run(agent_run: tuple[Run, User]) -> Run:
    run, _ = agent_run
    AgentLease.objects.create(
        run=run,
        session_id="run-1",
        token_hash=hashlib.sha256(b"broker-token").hexdigest(),
    )
    return run


def send(params: dict[str, Any]) -> dict[str, Any]:
    response = agent_mcp(
        RequestFactory().post(
            "/",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "p",
                    "method": PROGRESS_METHOD,
                    "params": params,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer broker-token",
        )
    )
    return json.loads(response.content)


def shown(run: Run) -> RunSchema:
    return serialize_run(with_model_usage(Run.objects.all()).get(pk=run.pk))


def test_progress_shows_usage_turns_and_models_while_the_run_works(
    leased_run: Run,
) -> None:
    assert send({"turns": 3, "usage": USAGE, "models": ["claude-opus-5-5"]}) == {
        "jsonrpc": "2.0",
        "id": "p",
        "result": {},
    }
    send({"turns": 5, "usage": {**USAGE, "output_tokens": 4_000}, "models": []})

    run = shown(leased_run)
    assert run.model_tokens == 1_000 + 4_000 + 50_000
    assert run.model_cache_read_tokens == 50_000
    assert run.model_requests == 5
    assert run.max_turns == leased_run.plan.agent.limits.max_turns
    # One event, kept up to date, and left out of the timeline.
    assert (
        RunEvent.objects.filter(
            run=leased_run, metadata__has_key="agent_progress"
        ).count()
        == 1
    )
    detail = serialize_run_detail(
        with_model_usage(Run.objects.select_related("job", "task__repository")).get(
            pk=leased_run.pk
        )
    )
    assert not any("progress" in event.message.lower() for event in detail.events)


@pytest.mark.parametrize(
    "params",
    [
        {"turns": -1},
        {"usage": {"input_tokens": -5}},
        {"usage": {"Not A Key": 1}},
        {"models": ["--model=evil value"]},
        {"usage": {f"key_{index}": 1 for index in range(21)}},
    ],
)
def test_progress_is_checked_like_the_final_result(
    leased_run: Run, params: dict[str, Any]
) -> None:
    assert "error" in send(params)
    assert not RunEvent.objects.filter(run=leased_run).exists()


def test_progress_needs_the_runs_broker_token(leased_run: Run) -> None:
    response = agent_mcp(
        RequestFactory().post(
            "/",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": PROGRESS_METHOD}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer other-token",
        )
    )
    assert response.status_code == 401


def test_a_stopped_agent_keeps_its_last_progress(
    leased_run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    send({"turns": 4, "usage": USAGE, "models": ["claude-sonnet-5-5"]})
    AgentLease.objects.filter(run=leased_run).delete()
    # The container died before it could report anything.
    fake_runner(monkeypatch, {"reason": "agent_stopped"})

    execute_cli_step(leased_run.pk, "run-1", "Read the skill")

    run = shown(leased_run)
    assert run.model_tokens == 1_000 + 2_000 + 50_000
    assert run.models == ["claude-sonnet-5-5"]
    assert run.model_requests == 4


def test_the_final_result_replaces_progress(
    leased_run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    send({"turns": 4, "usage": USAGE, "models": ["claude-sonnet-5-5"]})
    AgentLease.objects.filter(run=leased_run).delete()
    fake_runner(
        monkeypatch,
        {
            "report": "Done",
            "succeeded": True,
            "reason": "completed",
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "models": ["claude-opus-5-5"],
            "turns": 9,
        },
    )

    execute_cli_step(leased_run.pk, "run-1", "Read the skill")

    run = shown(leased_run)
    assert run.model_tokens == 30
    assert run.models == ["claude-opus-5-5"]
    assert run.model_requests == 9
