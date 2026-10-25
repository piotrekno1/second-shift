import pytest
from django.contrib.auth import get_user_model

from secondshift.core.owners import ensure_owner, get_owner, has_owner


@pytest.mark.django_db
def test_has_owner_needs_the_local_workspace_not_just_any_user() -> None:
    get_user_model().objects.create_user(username="signed-up-first")

    assert has_owner() is False
    ensure_owner()
    assert has_owner() is True
    assert get_owner().get_username() == "owner"
