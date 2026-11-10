import difflib
import logging
import re
from dataclasses import dataclass
from typing import Any

import anthropic
import httpx
from django.conf import settings
from django.db import transaction

from secondshift.config.constants import ModelFamily
from secondshift.connections.checks import record_check
from secondshift.connections.models import Connection
from secondshift.connections.outbound import (
    OutboundUrlError,
    host_label,
    outbound_sync_client,
    validate_outbound_url,
)
from secondshift.workspaces.models import Workspace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelProvider:
    family: ModelFamily
    # Empty when the owner gives the URL. Such providers may also skip the key.
    # Anthropic base URLs exclude /v1, as its SDK expects. OpenAI ones include it.
    default_base_url: str = ""
    # Serves other vendors' models. Runs use Chat Completions, since not every
    # router offers Responses, and prices are looked up by the router's URL.
    router: bool = False

    @property
    def custom_endpoint(self) -> bool:
        return not self.default_base_url


MODEL_PROVIDERS = {
    Connection.Provider.ANTHROPIC: ModelProvider(
        ModelFamily.ANTHROPIC, "https://api.anthropic.com"
    ),
    Connection.Provider.OPENAI: ModelProvider(
        ModelFamily.OPENAI, "https://api.openai.com/v1"
    ),
    Connection.Provider.OPENROUTER: ModelProvider(
        ModelFamily.OPENAI, "https://openrouter.ai/api/v1", router=True
    ),
    Connection.Provider.OPENAI_COMPATIBLE: ModelProvider(
        ModelFamily.OPENAI, router=True
    ),
}


class ModelProviderError(ValueError):
    pass


@dataclass(frozen=True)
class ModelProviderCheck:
    ok: bool
    message: str
    models: list[str]


def model_provider(provider: str) -> ModelProvider:
    try:
        return MODEL_PROVIDERS[Connection.Provider(provider)]
    except (KeyError, ValueError) as error:
        raise ModelProviderError(
            "Choose Anthropic, OpenAI, OpenRouter or an OpenAI-compatible provider."
        ) from error


def provider_family(connection: Connection) -> ModelFamily:
    return model_provider(connection.provider).family


def base_url(connection: Connection) -> str:
    default = model_provider(connection.provider).default_base_url
    return (connection.endpoint_url or default).rstrip("/")


def api_root(connection: Connection) -> str:
    """The URL that model API paths such as `messages` are relative to."""
    if provider_family(connection) == ModelFamily.ANTHROPIC:
        return f"{base_url(connection)}/v1"
    return base_url(connection)


def checked_api_key(provider: str, api_key: str) -> str:
    api_key = api_key.strip()
    if not api_key and not model_provider(provider).custom_endpoint:
        raise ModelProviderError("Enter an API key.")
    return api_key


def clean_models(values: list[str]) -> list[str]:
    cleaned = sorted({value.strip() for value in values if value.strip()})
    if any(len(value) > 200 for value in cleaned):
        raise ModelProviderError("Model names are at most 200 characters.")
    return cleaned


def validated_endpoint(provider: str, endpoint_url: str) -> str:
    if not model_provider(provider).custom_endpoint:
        # First-party providers always use their official endpoint.
        return ""
    if not endpoint_url.strip():
        raise ModelProviderError("OpenAI-compatible providers need a base URL.")
    try:
        return validate_outbound_url(endpoint_url, "base URL").rstrip("/")
    except OutboundUrlError as error:
        raise ModelProviderError(str(error)) from error


@transaction.atomic
def create_model_connection(
    workspace: Workspace,
    provider: str,
    display_name: str,
    api_key: str,
    endpoint_url: str = "",
    allowed_models: list[str] | None = None,
    default_model: str = "",
) -> Connection:
    api_key = checked_api_key(provider, api_key)
    connection = Connection(
        workspace=workspace,
        provider=provider,
        kind=Connection.Kind.MODEL,
        display_name=display_name.strip() or Connection.Provider(provider).label,
        auth_strategy=(
            Connection.AuthStrategy.API_KEY if api_key else Connection.AuthStrategy.NONE
        ),
        endpoint_url=validated_endpoint(provider, endpoint_url),
        access_token=api_key,
        allowed_models=clean_models(allowed_models or []),
        default_model=default_model.strip(),
    )
    connection.metadata = {"target": host_label(base_url(connection))}
    check_default_model(connection)
    connection.full_clean(validate_constraints=False)
    connection.save()
    return connection


@transaction.atomic
def update_model_connection(
    connection: Connection,
    display_name: str | None = None,
    api_key: str | None = None,
    endpoint_url: str | None = None,
    allowed_models: list[str] | None = None,
    default_model: str | None = None,
) -> Connection:
    if connection.kind != Connection.Kind.MODEL:
        raise ModelProviderError("This is not a model provider connection.")
    if display_name is not None:
        connection.display_name = (
            display_name.strip() or connection.get_provider_display()
        )
    if endpoint_url is not None:
        connection.endpoint_url = validated_endpoint(connection.provider, endpoint_url)
        connection.metadata = {
            **connection.metadata,
            "target": host_label(base_url(connection)),
        }
    if allowed_models is not None:
        connection.allowed_models = clean_models(allowed_models)
    if default_model is not None:
        connection.default_model = default_model.strip()
    if api_key and api_key.strip():
        connection.access_token = api_key.strip()
        connection.auth_strategy = Connection.AuthStrategy.API_KEY
        connection.status = Connection.Status.CONNECTED
    elif connection.status == Connection.Status.REVOKED:
        raise ModelProviderError("Enter a new API key to reconnect this provider.")
    check_default_model(connection)
    # A saved check result described the old settings; drop it.
    connection.metadata = {
        key: value for key, value in connection.metadata.items() if key != "last_check"
    }
    connection.full_clean(validate_constraints=False)
    connection.save()
    return connection


def check_default_model(connection: Connection) -> None:
    if (
        connection.default_model
        and connection.allowed_models
        and connection.default_model not in connection.allowed_models
    ):
        raise ModelProviderError("The default model must be one of the allowed models.")


def list_anthropic_models(connection: Connection) -> list[str]:
    client = anthropic.Anthropic(
        api_key=connection.access_token,
        base_url=base_url(connection),
        max_retries=0,
        timeout=settings.OUTBOUND_TIMEOUT_SECONDS,
        http_client=outbound_sync_client(),
    )
    return [model.id for model in client.models.list()]


def list_openai_models(connection: Connection) -> list[str]:
    url = validate_outbound_url(f"{base_url(connection)}/models", "base URL")
    headers = {"Accept": "application/json"}
    if connection.access_token:
        headers["Authorization"] = f"Bearer {connection.access_token}"
    with outbound_sync_client() as client:
        response = client.get(url, headers=headers)
    if response.status_code in {401, 403}:
        raise ModelProviderError("The provider rejected the API key.")
    if response.status_code != 200:
        raise ModelProviderError(f"The provider answered HTTP {response.status_code}.")
    try:
        return [str(item["id"]) for item in response.json().get("data", [])]
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ModelProviderError(
            "The provider returned an unexpected model list."
        ) from error


# One short request is enough to prove the key can run the model.
TEST_PROMPT = "Reply with OK."
TEST_MAX_TOKENS = 16
MAX_PROVIDER_MESSAGE = 200


def shorten(text: str) -> str:
    line = " ".join(text.split())
    return (
        line
        if len(line) <= MAX_PROVIDER_MESSAGE
        else line[: MAX_PROVIDER_MESSAGE - 1] + "…"
    )


def provider_message(response: httpx.Response) -> str:
    try:
        body: Any = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    nested = body.get("error")
    if isinstance(nested, dict) and isinstance(nested.get("message"), str):
        return shorten(nested["message"])
    return shorten(body["message"]) if isinstance(body.get("message"), str) else ""


def call_failure(status_code: int, model: str, detail: str) -> str:
    if status_code in {401, 403}:
        reason = "The provider rejected the API key for model calls"
    elif status_code == 402:
        reason = "The account has no credit for model calls"
    elif status_code == 404:
        reason = f"The provider does not offer {model}"
    else:
        reason = f"A test call to {model} failed with HTTP {status_code}"
    return f"{reason}: {detail}" if detail else f"{reason}."


def call_anthropic(connection: Connection, model: str) -> str | None:
    client = anthropic.Anthropic(
        api_key=connection.access_token,
        base_url=base_url(connection),
        max_retries=0,
        timeout=settings.OUTBOUND_TIMEOUT_SECONDS,
        http_client=outbound_sync_client(),
    )
    try:
        client.messages.create(
            model=model,
            max_tokens=TEST_MAX_TOKENS,
            messages=[{"role": "user", "content": TEST_PROMPT}],
        )
    except anthropic.APIStatusError as error:
        return call_failure(error.status_code, model, shorten(error.message))
    return None


def call_openai_family(connection: Connection, model: str) -> str | None:
    if not model_provider(connection.provider).router:
        # Runs use the Responses API for OpenAI itself.
        path = "responses"
        body: dict[str, Any] = {
            "model": model,
            "input": TEST_PROMPT,
            "max_output_tokens": TEST_MAX_TOKENS,
        }
    else:
        path = "chat/completions"
        body = {
            "model": model,
            "messages": [{"role": "user", "content": TEST_PROMPT}],
            "max_tokens": TEST_MAX_TOKENS,
        }
    url = validate_outbound_url(f"{base_url(connection)}/{path}", "base URL")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if connection.access_token:
        headers["Authorization"] = f"Bearer {connection.access_token}"
    with outbound_sync_client() as client:
        response = client.post(url, json=body, headers=headers)
    if response.status_code == 200:
        return None
    return call_failure(response.status_code, model, provider_message(response))


def try_model_call(connection: Connection) -> str | None:
    """Send one tiny request to the default model; return why it failed, if it did.

    Listing models can succeed with a key that cannot run any of them (an
    account without access, say), so a real call is the only reliable check.
    """
    model = connection.default_model
    if provider_family(connection) == ModelFamily.ANTHROPIC:
        return call_anthropic(connection, model)
    return call_openai_family(connection, model)


def comparable_model(name: str) -> str:
    """Drop a vendor prefix and punctuation, so close IDs compare equal."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower().rsplit("/", 1)[-1]).strip("-")


def closest_model(model: str, models: list[str]) -> str | None:
    """The listed model the owner most likely meant, if one is close."""
    by_key: dict[str, str] = {}
    for item in models:
        by_key.setdefault(comparable_model(item), item)
    matches = difflib.get_close_matches(
        comparable_model(model), list(by_key), n=1, cutoff=0.8
    )
    return by_key[matches[0]] if matches else None


def check_model_connection(connection: Connection) -> ModelProviderCheck:
    """Check the key lists models and can run the default one. Never echoes the key."""
    check = run_model_check(connection)
    record_check(connection, check.ok, check.message)
    return check


class CheckFailed(Exception):
    """A provider problem, worded for the person checking the connection."""


def run_model_check(connection: Connection) -> ModelProviderCheck:
    try:
        models = fetch_models(connection)
    except CheckFailed as failure:
        return ModelProviderCheck(False, str(failure), [])
    unavailable = sorted(set(connection.allowed_models) - set(models))
    if unavailable:
        return ModelProviderCheck(
            False,
            "Connected, but these allowed models are not offered: "
            + ", ".join(unavailable),
            models,
        )
    if not connection.default_model:
        return ModelProviderCheck(
            True,
            f"Connected. {len(models)} models available. "
            "Set a default model to test a model call.",
            models,
        )
    if failure := model_call_failure(connection):
        return ModelProviderCheck(
            False, failure + model_hint(connection.default_model, models), models
        )
    return ModelProviderCheck(
        True,
        f"Connected. {connection.default_model} answered a test request.",
        models,
    )


def fetch_models(connection: Connection) -> list[str]:
    """The provider's models, saved on the connection."""
    try:
        if provider_family(connection) == ModelFamily.ANTHROPIC:
            models = list_anthropic_models(connection)
        else:
            models = list_openai_models(connection)
    except anthropic.AuthenticationError as error:
        raise CheckFailed("The provider rejected the API key.") from error
    except anthropic.PermissionDeniedError as error:
        raise CheckFailed("The API key cannot list models.") from error
    except anthropic.APIStatusError as error:
        raise CheckFailed(f"The provider answered HTTP {error.status_code}.") from error
    except anthropic.APIConnectionError as error:
        raise CheckFailed("The provider could not be reached.") from error
    except (ModelProviderError, OutboundUrlError) as error:
        raise CheckFailed(str(error)) from error
    except httpx.HTTPError as error:
        logger.warning(
            "model provider check for connection %s failed: %s",
            connection.pk,
            type(error).__name__,
        )
        raise CheckFailed("The provider could not be reached.") from error
    models = sorted(set(models))
    connection.metadata = {**connection.metadata, "available_models": models}
    if connection.pk:
        connection.save(update_fields=["metadata", "updated_at"])
    return models


def model_call_failure(connection: Connection) -> str | None:
    """Why a small call to the default model failed, or None when it worked."""
    try:
        return try_model_call(connection)
    except anthropic.APIConnectionError:
        return "The provider could not be reached for a test call."
    except OutboundUrlError as error:
        return str(error)
    except httpx.HTTPError as error:
        logger.warning(
            "model call check for connection %s failed: %s",
            connection.pk,
            type(error).__name__,
        )
        return "The provider could not be reached for a test call."


def model_hint(model: str, models: list[str]) -> str:
    """When the default model is not listed, the listed name it probably means."""
    if model in models:
        return ""
    suggestion = closest_model(model, models)
    if suggestion:
        return f" The provider lists it as {suggestion}."
    return " It is not in the provider's model list."
