"""Routes served by the gateway process: model traffic and a health check only."""

from django.urls import path
from health_check.views import HealthCheckView

from secondshift.gateway.views import model_gateway

urlpatterns = [
    path("gateway/<str:family>/v1/<path:path>", model_gateway),
    path(
        "-/health/",
        HealthCheckView.as_view(checks=["health_check.checks.Database"]),
    ),
]
