import pytest
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
