import pytest
from django.contrib.auth import get_user_model
from django.test import Client, override_settings


@pytest.mark.django_db
@override_settings(SIGNUP_ENABLED=True)
@pytest.mark.parametrize("path", ["/accounts/login/", "/accounts/signup/"])
def test_account_pages_use_the_secondshift_layout(client: Client, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    page = response.content.decode()
    assert "core/account.css" in page
    assert "account-card" in page
    assert "Menu:" not in page


@pytest.mark.django_db
def test_signing_in_leaves_no_message_for_a_later_page(client: Client) -> None:
    get_user_model().objects.create_user(
        username="person", email="person@example.test", password="Zq8-long-enough"
    )
    client.post(
        "/accounts/login/",
        {"login": "person@example.test", "password": "Zq8-long-enough"},
    )
    client.post("/accounts/logout/")

    page = client.get("/accounts/login/").content.decode()

    assert "account-messages" not in page
    assert "signed in" not in page.lower()


@pytest.mark.django_db
@pytest.mark.parametrize("delivered", [True, False])
def test_password_resets_are_offered_only_when_email_arrives(
    client: Client, delivered: bool
) -> None:
    with override_settings(EMAIL_DELIVERED=delivered):
        login = client.get("/accounts/login/").content.decode()
        reset = client.get("/accounts/password/reset/")

    assert ("Forgot your password?" in login) is delivered
    assert reset.status_code == (200 if delivered else 404)
