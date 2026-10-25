import pytest
from django.contrib.auth import get_user_model
from django.db import connection

from secondshift.connections.models import Secret
from secondshift.workspaces.services import create_workspace


@pytest.mark.django_db
def test_secret_value_is_encrypted_at_rest() -> None:
    user = get_user_model().objects.create_user(username="owner", password="password")
    workspace = create_workspace(name="Acme", slug="acme", owner=user)
    secret = Secret.objects.create(
        workspace=workspace,
        name="GRAFANA_TOKEN",
        value="super-sensitive-value",
    )

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT value FROM integrations_secret WHERE id = %s", [secret.pk]
        )
        stored = cursor.fetchone()[0]

    assert "super-sensitive-value" not in str(stored)
    assert Secret.objects.get(pk=secret.pk).value == "super-sensitive-value"
