import pytest
from django.contrib.auth import get_user_model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel

from secondshift.automations.model_gateway import (
    ModelGatewayAccess,
    ModelGatewayConfigurationError,
    issue_model_gateway_access,
    pydantic_model,
)
from secondshift.automations.models import Job, Run
from secondshift.automations.planning import build_execution_plan
from secondshift.core.services import create_workspace
from secondshift.gateway.tokens import authenticate
from secondshift.integrations.models import Connection
from secondshift.repositories.models import Repository

PROVIDER_KEY = "sk-ant-provider-secret"


@pytest.fixture
def model_run(db, settings) -> Run:
    settings.MODEL_GATEWAY_URL = "http://gateway:8001/"
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace, full_name="example/project"
    )
    job = Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="review",
        name="Review",
        skill_path=".agents/skills/review/SKILL.md",
        cron="0 9 * * 1",
        config_hash="abc",
    )
    Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.ANTHROPIC,
        kind=Connection.Kind.MODEL,
        display_name="Anthropic",
        auth_strategy=Connection.AuthStrategy.API_KEY,
        access_token=PROVIDER_KEY,
        default_model="claude-opus-5-5",
        is_default=True,
    )
    return Run.objects.create(
        job=job,
        status=Run.Status.RUNNING,
        commit_sha="abc",
        scheduled_for="2026-10-01T00:00:00Z",
        execution_plan=build_execution_plan(job),
    )


def test_plan_freezes_model_while_the_sandbox_remains_offline(model_run: Run) -> None:
    plan = model_run.execution_plan

    assert plan["model"]["family"] == "anthropic"
    assert plan["model"]["default_model"] == "claude-opus-5-5"
    assert plan["runner"]["network_policy"] == "none"
    assert PROVIDER_KEY not in str(plan)


def test_orchestrator_gets_run_scoped_gateway_access(model_run: Run) -> None:
    first = issue_model_gateway_access(model_run)
    access = issue_model_gateway_access(model_run)

    assert access.family == "anthropic"
    assert access.provider == Connection.Provider.ANTHROPIC
    assert access.model == "claude-opus-5-5"
    assert access.base_url == "http://gateway:8001/gateway/anthropic"
    assert access.token.startswith("ssrt_") and access.token != PROVIDER_KEY
    assert authenticate(access.token) is not None
    assert authenticate(first.token) is None


@pytest.mark.parametrize(
    ("provider", "expected_type"),
    [
        (Connection.Provider.ANTHROPIC, AnthropicModel),
        (Connection.Provider.OPENAI, OpenAIResponsesModel),
        (Connection.Provider.OPENAI_COMPATIBLE, OpenAIChatModel),
    ],
)
def test_pydantic_model_uses_only_gateway_access(
    provider: str, expected_type: type
) -> None:
    access = ModelGatewayAccess(
        family="anthropic" if provider == Connection.Provider.ANTHROPIC else "openai",
        provider=provider,
        model="model-1",
        base_url="http://gateway.internal/v1",
        token="ssrt_test-token",
    )

    model = pydantic_model(access)

    assert isinstance(model, expected_type)
    assert "gateway.internal" in model._provider.base_url  # type: ignore[attr-defined]
    assert "ssrt_test-token" not in repr(model)


def test_gateway_access_fails_closed_without_url(model_run: Run, settings) -> None:
    settings.MODEL_GATEWAY_URL = ""

    with pytest.raises(ModelGatewayConfigurationError, match="must be set"):
        issue_model_gateway_access(model_run)


def test_gateway_access_refuses_a_disconnected_provider(model_run: Run) -> None:
    Connection.objects.update(status=Connection.Status.REVOKED)

    with pytest.raises(ModelGatewayConfigurationError, match="no longer connected"):
        issue_model_gateway_access(model_run)
