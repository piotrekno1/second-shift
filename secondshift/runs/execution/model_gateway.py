from dataclasses import dataclass

from django.conf import settings
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from secondshift.config.constants import ModelFamily
from secondshift.connections.dao import connected_model
from secondshift.connections.model_providers import model_provider, provider_family
from secondshift.connections.models import Connection
from secondshift.gateway.dao import revoke_run_tokens
from secondshift.gateway.tokens import issue_token
from secondshift.runs.execution.deadlines import run_deadline
from secondshift.runs.models import Run
from secondshift.workspaces.limits import RunLimit


class ModelGatewayConfigurationError(RuntimeError):
    pass


def issue_run_token(run: Run, connection: Connection) -> str:
    """A gateway token that ends with the run's time limit and token budget."""
    return issue_token(
        run.pk,
        run.workspace_id,
        connection,
        expires_at=run_deadline(run),
        budget_tokens=run.plan.limit(RunLimit.TOKEN_BUDGET),
    )


@dataclass(frozen=True)
class ModelGatewayAccess:
    """Run-scoped access used by the orchestrator, never by command sandboxes."""

    family: ModelFamily
    provider: str
    model: str
    base_url: str
    token: str


def issue_model_gateway_access(run: Run) -> ModelGatewayAccess:
    """Issue access to the plan's provider through the internal model gateway."""
    model_plan = run.plan.model
    if not model_plan:
        raise ModelGatewayConfigurationError("This run has no model provider.")
    gateway_url = settings.MODEL_GATEWAY_URL.rstrip("/")
    if not gateway_url:
        raise ModelGatewayConfigurationError(
            "SECONDSHIFT_MODEL_GATEWAY_URL must be set for agent runs."
        )
    connection = connected_model(run.workspace_id, model_plan.connection_id)
    if connection is None:
        raise ModelGatewayConfigurationError(
            "The run's model provider is no longer connected."
        )

    family = provider_family(connection)
    revoke_run_tokens(run.pk)
    token = issue_run_token(run, connection)
    # The Anthropic SDK adds /v1 itself.
    path = family if family == ModelFamily.ANTHROPIC else f"{family}/v1"
    return ModelGatewayAccess(
        family=family,
        provider=connection.provider,
        model=model_plan.default_model,
        base_url=f"{gateway_url}/gateway/{path}",
        token=token,
    )


def pydantic_model(access: ModelGatewayAccess) -> Model:
    """Build a Pydantic AI model that can reach only SecondShift's gateway."""
    if access.family == ModelFamily.ANTHROPIC:
        return AnthropicModel(
            access.model,
            provider=AnthropicProvider(
                api_key=access.token,
                base_url=access.base_url,
            ),
        )
    provider = OpenAIProvider(api_key=access.token, base_url=access.base_url)
    if model_provider(access.provider).router:
        return OpenAIChatModel(access.model, provider=provider)
    return OpenAIResponsesModel(access.model, provider=provider)
