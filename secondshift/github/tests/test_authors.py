import httpx
import pytest

from secondshift.github import app, rest
from secondshift.github.authors import GitAuthor, app_author, token_author
from secondshift.github.errors import GitHubError


def answer(monkeypatch: pytest.MonkeyPatch, *responses: httpx.Response) -> list:
    seen = []
    queue = list(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    monkeypatch.setattr(
        rest, "client", lambda: httpx.Client(transport=httpx.MockTransport(handle))
    )
    monkeypatch.setattr(app, "app_token", lambda: "app-jwt")
    return seen


def test_the_app_commits_as_its_bot_account(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = answer(
        monkeypatch,
        httpx.Response(200, json={"id": 7, "slug": "secondshift-acme"}),
        httpx.Response(200, json={"id": 4242, "login": "secondshift-acme[bot]"}),
    )

    assert app_author("installation-token") == GitAuthor(
        "secondshift-acme[bot]",
        "4242+secondshift-acme[bot]@users.noreply.github.com",
    )
    # GitHub names the App, so a renamed App never leaves a stale slug.
    assert seen[0].url.path == "/app"
    assert seen[0].headers["Authorization"] == "Bearer app-jwt"
    assert seen[1].url.raw_path == b"/users/secondshift-acme%5Bbot%5D"
    assert seen[1].headers["Authorization"] == "Bearer installation-token"


def test_an_app_without_a_slug_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = answer(monkeypatch, httpx.Response(200, json={"id": 7}))

    with pytest.raises(GitHubError, match="which account publishes"):
        app_author("installation-token")
    assert len(seen) == 1


def test_a_token_commits_as_its_owners_login(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = answer(
        monkeypatch,
        httpx.Response(200, json={"id": 173, "login": "ada", "name": "Ada L"}),
    )

    # The login, not the profile name, so a retry builds the same commit.
    assert token_author("personal-token") == GitAuthor(
        "ada", "173+ada@users.noreply.github.com"
    )
    assert seen[0].url.path == "/user"


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
