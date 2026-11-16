from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User

from secondshift.connections.agents import save_agent_connection
from secondshift.connections.models import AgentConnection
from secondshift.workspaces.dao import create_workspace
from secondshift.workspaces.models import Workspace

TOKEN = "sk-ant-oat01-test-subscription-token"
IMAGE = "example/ss-agent@sha256:" + "a" * 64


@pytest.fixture
def agent_login(settings: Any) -> tuple[Workspace, AgentConnection, User]:
    settings.AGENT_IMAGE = IMAGE
    settings.AGENT_BROKER_URL = "http://gateway:8001/gateway/agent/mcp"
    owner = get_user_model().objects.create_user(username="agent-owner")
    workspace = create_workspace(name="Agents", slug="agents", owner=owner)
    login = save_agent_connection(workspace, owner, "claude_code", "Personal", TOKEN)
    return workspace, login, owner
