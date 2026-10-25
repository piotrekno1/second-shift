from dataclasses import dataclass

from django.conf import settings
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from secondshift.connections.model_providers import provider_family
from secondshift.connections.models import Connection
from secondshift.gateway.tokens import issue_run_token, revoke_run_tokens
from secondshift.runs.models import Run


class ModelGatewayConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelGatewayAccess:
    """Run-scoped access used by the orchestrator, never by command sandboxes."""

    family: str
    provider: str
    model: str
    base_url: str
    token: str


def issue_model_gateway_access(run: Run) -> ModelGatewayAccess:
    """Issue access to the plan's provider through the internal model gateway."""
    model_plan = run.execution_plan.get("model")
    if not model_plan:
        raise ModelGatewayConfigurationError("This run has no model provider.")
    gateway_url = settings.MODEL_GATEWAY_URL.rstrip("/")
    if not gateway_url:
        raise ModelGatewayConfigurationError(
            "SECONDSHIFT_MODEL_GATEWAY_URL must be set for agent runs."
        )
    try:
        connection = Connection.objects.get(
            pk=model_plan["connection_id"],
            workspace_id=run.job.workspace_id,
            kind=Connection.Kind.MODEL,
            status=Connection.Status.CONNECTED,
        )
    except Connection.DoesNotExist as error:
        raise ModelGatewayConfigurationError(
            "The run's model provider is no longer connected."
        ) from error

    family = provider_family(connection)
    revoke_run_tokens(run.pk)
    token = issue_run_token(run, connection)
    path = "anthropic" if family == "anthropic" else "openai/v1"
    return ModelGatewayAccess(
        family=family,
        provider=connection.provider,
        model=str(model_plan.get("default_model", "")),
        base_url=f"{gateway_url}/gateway/{path}",
        token=token,
    )


def pydantic_model(access: ModelGatewayAccess) -> Model:
    """Build a Pydantic AI model that can reach only SecondShift's gateway."""
    if access.family == "anthropic":
        return AnthropicModel(
            access.model,
            provider=AnthropicProvider(
                api_key=access.token,
                base_url=access.base_url,
            ),
        )
    provider = OpenAIProvider(api_key=access.token, base_url=access.base_url)
    if access.provider in {
        Connection.Provider.OPENAI_COMPATIBLE,
        Connection.Provider.OPENROUTER,
    }:
        # Responses is not universally implemented by compatible providers.
        return OpenAIChatModel(access.model, provider=provider)
    return OpenAIResponsesModel(access.model, provider=provider)
