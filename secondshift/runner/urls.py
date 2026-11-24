"""The runner deployment exposes only its narrow machine-to-machine API."""

from django.urls import path

from secondshift.runner.api import api
from secondshift.runner.views import agent_relay
from secondshift.runner_protocol.agents import AGENT_BROKER_PATH

# Agents call the relay at the broker's own path, so their configuration is the
# same whichever one answers.
urlpatterns = [path(AGENT_BROKER_PATH, agent_relay), path("", api.urls)]
