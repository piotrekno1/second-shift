"""Request cost from genai-prices' bundled price tables (no network lookups)."""

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

import genai_prices

from secondshift.integrations.model_providers import base_url
from secondshift.integrations.models import Connection

if TYPE_CHECKING:
    from secondshift.gateway.metering import Usage

logger = logging.getLogger(__name__)


def request_cost(connection: Connection, model: str, usage: "Usage") -> Decimal | None:
    """Price one request in USD, or None when the model has no known price."""
    if not usage.total:
        return Decimal(0)
    # genai-prices counts cache reads and writes inside input_tokens.
    priced = genai_prices.Usage(
        input_tokens=usage.input_tokens
        + usage.cache_read_tokens
        + usage.cache_write_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
    )
    if connection.provider in {
        Connection.Provider.OPENAI_COMPATIBLE,
        Connection.Provider.OPENROUTER,
    }:
        # Routers like OpenRouter are recognized by their API URL.
        lookup: dict[str, str] = {"provider_api_url": base_url(connection)}
    else:
        lookup = {"provider_id": connection.provider}
    try:
        return genai_prices.calc_price(priced, model, **lookup).total_price
    except (LookupError, ValueError):
        logger.info("no price for model %s on connection %s", model, connection.pk)
        return None
