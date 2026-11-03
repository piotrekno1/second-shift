import httpx
import pytest

from secondshift.config.constants import GITHUB_API_URL, GITHUB_OAUTH_URL
from secondshift.github import rest
from secondshift.github.errors import GitHubError


def serve(monkeypatch: pytest.MonkeyPatch, handle: object) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handle(request)

    monkeypatch.setattr(
        rest, "client", lambda: httpx.Client(transport=httpx.MockTransport(record))
    )
    return seen


def test_github_transport_errors_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private-secret")

    serve(monkeypatch, fail)
    with pytest.raises(GitHubError) as caught:
        rest.request_json("GET", f"{GITHUB_API_URL}/user", "private-secret")
    assert "private-secret" not in str(caught.value)


def test_oauth_exchange_does_not_send_an_empty_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = serve(
        monkeypatch,
        lambda request: httpx.Response(200, json={"access_token": "user-secret"}),
    )
    rest.request_json("POST", f"{GITHUB_OAUTH_URL}/access_token", json={"code": "c"})
    assert "Authorization" not in seen[0].headers


def test_every_request_names_the_api_version(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = serve(monkeypatch, lambda request: httpx.Response(200, json={}))
    rest.request_json("GET", f"{GITHUB_API_URL}/user", "token")
    assert seen[0].headers["X-GitHub-Api-Version"]
    assert seen[0].headers["Authorization"] == "Bearer token"


def test_listings_follow_pages_until_a_short_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [[{"n": index} for index in range(100)], [{"n": 100}]]
    seen = serve(monkeypatch, lambda request: httpx.Response(200, json=pages.pop(0)))
    items = rest.paginated(f"{GITHUB_API_URL}/things", "token", None, "Too many.")
    assert len(items) == 101
    assert seen[1].url.params["page"] == "2"
