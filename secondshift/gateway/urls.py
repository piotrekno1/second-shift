"""Internal gateway routes: model traffic, run-scoped agent tools, and health."""

from django.urls import path
from health_check.views import HealthCheckView

from secondshift.gateway.views import model_gateway
from secondshift.runs.execution.agent_broker import agent_mcp

urlpatterns = [
    path("gateway/agent/mcp", agent_mcp),
    path("gateway/<str:family>/v1/<path:path>", model_gateway),
    path(
        "-/health/",
        HealthCheckView.as_view(checks=["health_check.checks.Database"]),
    ),
]
