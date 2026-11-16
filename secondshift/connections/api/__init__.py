"""The connections app's HTTP surface, one module per connection kind."""

from ninja import Router

from secondshift.connections.api import (
    agents,
    connections,
    execution,
    github,
    http_api,
    mcp,
    model_providers,
    webhooks,
)

router = Router()
# connections comes last. Its /connections/{connection_id} would otherwise
# match fixed paths such as /connections/mcp and answer 405.
for module in (
    github,
    mcp,
    http_api,
    webhooks,
    model_providers,
    agents,
    execution,
    connections,
):
    router.add_router("", module.router)
