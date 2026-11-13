from typing import Any

import httpx
import pytest

from secondshift.github import rest
from secondshift.github.authors import GitAuthor, app_author, token_author
from secondshift.github.errors import GitHubError


def answer(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> list:
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    monkeypatch.setattr(
        rest, "client", lambda: httpx.Client(transport=httpx.MockTransport(handle))
    )
    return seen


def test_the_app_commits_as_its_bot_account(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    settings.GITHUB_APP_SLUG = "secondshift-acme"
    seen = answer(
        monkeypatch,
        httpx.Response(200, json={"id": 4242, "login": "secondshift-acme[bot]"}),
    )

    assert app_author("installation-token") == GitAuthor(
        "secondshift-acme[bot]",
        "4242+secondshift-acme[bot]@users.noreply.github.com",
    )
    assert seen[0].url.raw_path == b"/users/secondshift-acme%5Bbot%5D"
    assert seen[0].headers["Authorization"] == "Bearer installation-token"


def test_a_token_commits_as_its_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = answer(
        monkeypatch,
        httpx.Response(200, json={"id": 173, "login": "ada", "name": "Ada L"}),
    )

    assert token_author("personal-token") == GitAuthor(
        "Ada L", "173+ada@users.noreply.github.com"
    )
    assert seen[0].url.path == "/user"


def test_an_owner_without_a_name_uses_the_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer(monkeypatch, httpx.Response(200, json={"id": 173, "login": "ada"}))

    assert token_author("personal-token").name == "ada"


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(404, json={"message": "Not Found"}),
        httpx.Response(200, json={"login": "ada"}),
        httpx.Response(200, json={"id": "173", "login": "ada"}),
    ],
)
def test_an_account_github_cannot_name_is_refused(
    monkeypatch: pytest.MonkeyPatch, reply: httpx.Response
) -> None:
    answer(monkeypatch, reply)

    with pytest.raises(GitHubError):
        token_author("personal-token")
