import pytest
from django.contrib.auth import get_user_model

from secondshift.workspaces.dao import create_workspace
from secondshift.workspaces.models import Workspace


@pytest.fixture
def workspace(db: object) -> Workspace:
    user = get_user_model().objects.create_user(username="owner")
    return create_workspace(name="Test", slug="test", owner=user)
