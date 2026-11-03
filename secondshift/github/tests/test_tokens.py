from datetime import UTC, datetime

import httpx
import pytest

from secondshift.github import rest
from secondshift.github.errors import GitHubError
from secondshift.github.tokens import EXPIRY_HEADER, expiry, inspect_token

TOKEN = "github_pat_" + "a" * 40


def answer(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> list:
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    monkeypatch.setattr(
        rest, "client", lambda: httpx.Client(transport=httpx.MockTransport(handle))
    )
    return seen


def test_classic_tokens_are_refused_before_calling_github(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = answer(monkeypatch, httpx.Response(200, json={"login": "someone"}))
    with pytest.raises(GitHubError, match="fine-grained"):
        inspect_token("ghp_" + "a" * 36)
    assert seen == []


def test_a_token_names_its_owner_and_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = answer(
        monkeypatch,
        httpx.Response(
            200,
            json={"login": "someone"},
            headers={EXPIRY_HEADER: "2026-12-01 08:00:00 UTC"},
        ),
    )
    owner = inspect_token(TOKEN)
    assert owner.login == "someone"
    assert owner.expires_at == datetime(2026, 12, 1, 8, tzinfo=UTC)
    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"


def test_a_refused_token_never_appears_in_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer(monkeypatch, httpx.Response(401, json={"message": "Bad credentials"}))
    with pytest.raises(GitHubError, match="refused") as caught:
        inspect_token(TOKEN)
    assert TOKEN not in str(caught.value)


def test_expiry_reads_offsets_and_allows_none() -> None:
    assert expiry("2026-12-01 00:00:00 -0800") == datetime(2026, 12, 1, 8, tzinfo=UTC)
    assert expiry("") is None
    assert expiry("soon") is None
