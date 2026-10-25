import djclick as click
import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from secondshift.automations.models import Job
from secondshift.core.adapters import AccountAdapter, SocialAccountAdapter
from secondshift.core.models import Workspace
from secondshift.repositories.models import Repository


def test_admin_is_served_under_internal_prefix() -> None:
    assert reverse("admin:index") == "/-/admin/"


def test_liveness_skips_host_validation(client) -> None:
    response = client.get("/-/health/up/", HTTP_HOST="kamal-internal:8000")
    assert response.status_code == 200
    assert response.content == b"ok"


@pytest.mark.django_db
def test_health_check_reports_database_and_cache(client) -> None:
    response = client.get("/-/health/?format=json")
    assert response.status_code == 200
    assert set(response.json()) == {
        "Database(alias='default')",
        "Cache(alias='default')",
    }


@pytest.mark.parametrize("enabled", [True, False])
def test_signup_follows_setting(rf, enabled: bool) -> None:
    request = rf.get("/accounts/signup/")
    with override_settings(SIGNUP_ENABLED=enabled):
        assert AccountAdapter(request).is_open_for_signup(request) is enabled
        assert (
            SocialAccountAdapter(request).is_open_for_signup(request, None) is enabled
        )


@pytest.mark.django_db
def test_signup_page_is_closed_by_default(client) -> None:
    response = client.get("/accounts/signup/")
    assert b"Sign Up Closed" in response.content


@pytest.mark.django_db
def test_responses_carry_csp(client) -> None:
    response = client.get("/-/admin/login/")
    policy = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy


@pytest.mark.django_db
@override_settings(AXES_FAILURE_LIMIT=2)
def test_admin_login_locks_out_after_failures(client) -> None:
    for _ in range(2):
        client.post("/-/admin/login/", {"username": "owner", "password": "wrong"})
    response = client.post(
        "/-/admin/login/", {"username": "owner", "password": "wrong"}
    )
    assert response.status_code == 429


def test_bootstrap_dev_refuses_outside_debug() -> None:
    with pytest.raises(click.ClickException, match="DJANGO_ENV=dev"):
        call_command("bootstrap_dev")


@pytest.mark.django_db
@override_settings(DEV=True, ARTIFACT_STORAGE="filesystem")
def test_bootstrap_dev_creates_admin_and_empty_workspace_only() -> None:
    call_command("bootstrap_dev")

    admin = get_user_model().objects.get(username="admin@secondshift.local")
    assert admin.is_staff is True
    assert admin.is_superuser is True
    assert Workspace.objects.filter(
        slug="secondshift", memberships__user=admin, memberships__role="owner"
    ).exists()
    assert Repository.objects.count() == 0
    assert Job.objects.count() == 0
