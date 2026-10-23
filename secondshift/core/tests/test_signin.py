from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from secondshift.core.management.commands.up import is_loopback
from secondshift.core.owners import (
    LOCAL_OWNER_USERNAME,
    ensure_owner,
    get_owner,
    set_owner_credentials,
)
from secondshift.core.signin import SIGN_IN_LINK_SECONDS, sign_in_url

BASE_URL = "http://testserver"


def signed_in_user(client: Client) -> int | None:
    return client.session.get("_auth_user_id")


@pytest.mark.django_db
def test_owner_without_password_cannot_use_password_sign_in() -> None:
    ensure_owner()

    owner = get_owner()
    assert owner.username == LOCAL_OWNER_USERNAME
    assert owner.email == ""
    assert not owner.has_usable_password()


@pytest.mark.django_db
def test_link_signs_in_the_owner_once(client: Client) -> None:
    ensure_owner()
    url = sign_in_url(BASE_URL, get_owner()).removeprefix(BASE_URL)

    first = client.get(url)
    assert first.status_code == 302
    assert first["Location"] == "/"
    assert signed_in_user(client) == str(get_owner().pk)

    second = Client().get(url)
    assert second.status_code == 400


@pytest.mark.django_db
def test_link_expires(client: Client) -> None:
    ensure_owner()
    url = sign_in_url(BASE_URL, get_owner()).removeprefix(BASE_URL)
    later = timezone.now() + timedelta(seconds=SIGN_IN_LINK_SECONDS + 1)

    with mock.patch("time.time", return_value=later.timestamp()):
        response = client.get(url)

    assert response.status_code == 400
    assert signed_in_user(client) is None


@pytest.mark.django_db
def test_tampered_link_is_refused(client: Client) -> None:
    ensure_owner()
    other = get_user_model().objects.create(username="other")
    url = sign_in_url(BASE_URL, get_owner()).removeprefix(BASE_URL)
    payload, rest = url.removeprefix("/-/sign-in/").split(":", 1)

    response = client.get(f"/-/sign-in/{payload}x:{rest}")

    assert response.status_code == 400
    assert signed_in_user(client) != str(other.pk)


@pytest.mark.django_db
def test_link_refuses_post(client: Client) -> None:
    ensure_owner()
    url = sign_in_url(BASE_URL, get_owner()).removeprefix(BASE_URL)

    assert client.post(url).status_code == 405


@pytest.mark.django_db
def test_login_command_prints_a_working_link(
    client: Client, capsys: pytest.CaptureFixture[str]
) -> None:
    ensure_owner()

    call_command("login", "--url", BASE_URL)

    link = capsys.readouterr().out.strip().splitlines()[-1]
    assert client.get(link.removeprefix(BASE_URL)).status_code == 302


@pytest.mark.django_db
def test_setting_credentials_enables_password_sign_in() -> None:
    ensure_owner()

    set_owner_credentials("me@example.com", "a long password")

    owner = get_owner()
    assert owner.email == "me@example.com"
    assert owner.check_password("a long password")


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("0.0.0.0", False),
        ("192.168.1.5", False),
        ("myhost.lan", False),
    ],
)
def test_only_loopback_hosts_skip_the_password(host: str, expected: bool) -> None:
    assert is_loopback(host) is expected
