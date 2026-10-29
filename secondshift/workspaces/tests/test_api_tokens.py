from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.http import HttpRequest
from django.test import RequestFactory, override_settings
from django.utils import timezone

from secondshift.workspaces.access import (
    AccessDenied,
    Action,
    current_workspace,
    require,
)
from secondshift.workspaces.models import ApiToken, Workspace, WorkspaceMembership
from secondshift.workspaces.services import create_workspace
from secondshift.workspaces.tokens import ApiTokenAuth, create_api_token

Role = WorkspaceMembership.Role


def user(name: str) -> AbstractUser:
    return get_user_model().objects.create_user(username=name, email=f"{name}@x.dev")


def join(workspace: Workspace, person: AbstractUser, role: str) -> WorkspaceMembership:
    return WorkspaceMembership.objects.create(
        workspace=workspace, user=person, role=role
    )


def signed(secret: str) -> HttpRequest:
    """A request carrying the token, after bearer auth has run."""
    request = RequestFactory().post("/mcp", HTTP_AUTHORIZATION=f"Bearer {secret}")
    request.user = get_user_model()()
    request.auth = ApiTokenAuth()(request)
    return request


@pytest.fixture
def owner(db: object) -> AbstractUser:
    person = user("owner")
    create_workspace("Acme", "acme", person)
    return person


@pytest.fixture
def acme(owner: AbstractUser) -> Workspace:
    return Workspace.objects.get(slug="acme")


def membership(workspace: Workspace, person: AbstractUser) -> WorkspaceMembership:
    return WorkspaceMembership.objects.get(workspace=workspace, user=person)


def test_lowering_the_maximum_keeps_existing_expiry(
    acme: Workspace, owner: AbstractUser
) -> None:
    new = create_api_token(membership(acme, owner), "Laptop", "read", 90)

    with override_settings(API_TOKEN_MAX_DAYS=7):
        assert signed(new.secret).auth == new.token


def test_a_token_signs_in_as_its_creator_and_records_use(
    acme: Workspace, owner: AbstractUser
) -> None:
    new = create_api_token(membership(acme, owner), "Laptop", "read", 30)

    request = signed(new.secret)

    assert request.auth == new.token
    assert request.user == owner
    assert ApiToken.objects.get().last_used_at is not None


def test_unknown_expired_and_revoked_tokens_do_not_sign_in(
    acme: Workspace, owner: AbstractUser
) -> None:
    expired = create_api_token(membership(acme, owner), "Old", "read", 30)
    ApiToken.objects.filter(pk=expired.token.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    revoked = create_api_token(membership(acme, owner), "Gone", "read", 30)
    ApiToken.objects.filter(pk=revoked.token.pk).update(revoked_at=timezone.now())

    assert signed("ss_unknown").auth is None
    assert signed(expired.secret).auth is None
    assert signed(revoked.secret).auth is None


def test_a_creator_who_leaves_or_is_deactivated_loses_their_tokens(
    acme: Workspace,
) -> None:
    leaving, disabled = user("leaving"), user("disabled")
    join(acme, leaving, Role.ADMIN)
    join(acme, disabled, Role.ADMIN)
    left = create_api_token(membership(acme, leaving), "Laptop", "run", 30)
    off = create_api_token(membership(acme, disabled), "Laptop", "run", 30)

    membership(acme, leaving).delete()
    disabled.is_active = False
    disabled.save()

    assert signed(left.secret).auth is None
    assert signed(off.secret).auth is None


def test_a_read_token_cannot_change_anything(
    acme: Workspace, owner: AbstractUser
) -> None:
    new = create_api_token(membership(acme, owner), "Laptop", "read", 30)
    request = signed(new.secret)

    with pytest.raises(AccessDenied, match="This API token cannot run jobs"):
        require(request, Action.RUN_JOBS)


def test_a_run_token_only_runs_and_resyncs_even_for_an_owner(
    acme: Workspace, owner: AbstractUser
) -> None:
    new = create_api_token(membership(acme, owner), "Laptop", "run", 30)
    request = signed(new.secret)

    assert require(request, Action.RUN_JOBS) == acme
    assert require(request, Action.RESYNC_REPOSITORIES) == acme
    for action in (
        Action.ENABLE_JOBS,
        Action.CHANGE_RUN_LIMITS,
        Action.REVIEW_CAPABILITIES,
        Action.ADD_CONNECTIONS,
        Action.MANAGE_MEMBERS,
    ):
        with pytest.raises(AccessDenied, match="This API token cannot"):
            require(request, action)


def test_a_demoted_creator_s_run_token_can_only_read(acme: Workspace) -> None:
    person = user("admin")
    join(acme, person, Role.ADMIN)
    new = create_api_token(membership(acme, person), "Laptop", "run", 30)
    WorkspaceMembership.objects.filter(user=person).update(role=Role.MEMBER)

    request = signed(new.secret)

    assert request.auth == new.token
    with pytest.raises(AccessDenied, match="owners and admins can run jobs"):
        require(request, Action.RUN_JOBS)


def test_a_token_works_only_in_its_own_workspace(
    acme: Workspace, owner: AbstractUser
) -> None:
    other = create_workspace("Beta", "beta", owner)
    new = create_api_token(membership(other, owner), "Laptop", "read", 30)

    assert current_workspace(signed(new.secret)) == other
