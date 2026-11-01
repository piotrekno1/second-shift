import djclick as click
import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client

from secondshift.workspaces.models import Workspace


def link_from(output: str) -> str:
    return next(line.strip() for line in output.splitlines() if "/invite/" in line)


@pytest.mark.django_db
def test_the_printed_link_makes_the_first_owner(
    client: Client, capsys: pytest.CaptureFixture[str]
) -> None:
    call_command("create_workspace", "Acme Labs", "--url", "https://ss.example.com/")
    link = link_from(capsys.readouterr().out)

    assert link.startswith("https://ss.example.com/invite/")
    workspace = Workspace.objects.get(slug="acme-labs")
    assert workspace.memberships.count() == 0

    # Sign-up is closed on this server, but the link opens it.
    client.get(link.removeprefix("https://ss.example.com"))
    person = get_user_model().objects.create_user(username="first")
    client.force_login(person)
    joined = client.post("/api/invites/pending/accept")

    assert joined.json()["role"] == "owner"
    assert workspace.memberships.get().user == person


@pytest.mark.django_db
def test_running_again_replaces_an_unused_link_but_not_an_owner(
    capsys: pytest.CaptureFixture[str],
) -> None:
    call_command("create_workspace", "Acme", "--slug", "acme")
    first = link_from(capsys.readouterr().out)
    call_command("create_workspace", "Acme", "--slug", "acme")
    second = link_from(capsys.readouterr().out)

    assert first != second
    assert Workspace.objects.filter(slug="acme").count() == 1

    workspace = Workspace.objects.get(slug="acme")
    owner = get_user_model().objects.create_user(username="owner")
    workspace.memberships.create(user=owner, role="owner")
    with pytest.raises(click.ClickException, match="already has an owner"):
        call_command("create_workspace", "Acme", "--slug", "acme")
