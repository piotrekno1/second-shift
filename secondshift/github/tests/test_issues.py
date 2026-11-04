from unittest.mock import Mock

import httpx
import pytest

from secondshift.github import issues
from secondshift.github.errors import GitHubError

URL = "https://github.com/owner/project/issues/12"


def reply(status: int, payload: object) -> httpx.Response:
    return httpx.Response(status, json=payload)


def test_an_issue_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    send = Mock(return_value=reply(201, {"number": 12, "html_url": URL}))
    monkeypatch.setattr(issues, "send", send)
    opened = issues.open_issue("token", "owner/project", "Flaky test", "Details", [])
    assert opened == issues.Issue(12, URL)
    assert send.call_args.args[:2] == (
        "POST",
        "https://api.github.com/repos/owner/project/issues",
    )
    assert send.call_args.kwargs["json"] == {"title": "Flaky test", "body": "Details"}


def test_a_refused_issue_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        issues,
        "send",
        Mock(return_value=reply(403, {"message": "Resource not accessible"})),
    )
    with pytest.raises(GitHubError, match="Resource not accessible"):
        issues.open_issue("token", "owner/project", "Flaky test", "Details", [])


def test_an_open_issue_with_the_same_title_is_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = Mock(
        return_value=reply(
            200,
            {
                "items": [
                    {"number": 3, "html_url": "x", "title": "Flaky test in CI"},
                    {"number": 4, "html_url": URL, "title": "flaky  TEST"},
                ]
            },
        )
    )
    monkeypatch.setattr(issues, "send", send)
    found = issues.open_issue_titled("token", "owner/project", "Flaky test")
    assert found == issues.Issue(4, URL)
    assert send.call_args.kwargs["params"]["q"] == (
        'repo:owner/project is:issue is:open in:title "Flaky test"'
    )


def test_quotes_cannot_end_the_title_phrase(monkeypatch: pytest.MonkeyPatch) -> None:
    send = Mock(return_value=reply(200, {"items": []}))
    monkeypatch.setattr(issues, "send", send)
    issues.open_issue_titled("token", "owner/project", 'Fix "repo:other/x" usage')
    assert send.call_args.kwargs["params"]["q"].count('"') == 2


def test_only_the_exact_title_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        issues,
        "send",
        Mock(
            return_value=reply(
                200, {"items": [{"number": 3, "html_url": URL, "title": "Flaky test"}]}
            )
        ),
    )
    assert issues.open_issue_titled("token", "owner/project", "flaky  TEST") == (
        issues.Issue(3, URL)
    )
    assert issues.open_issue_titled("token", "owner/project", "Flaky") is None


def test_labels_are_sent_when_given(monkeypatch: pytest.MonkeyPatch) -> None:
    send = Mock(return_value=reply(201, {"number": 12, "html_url": URL}))
    monkeypatch.setattr(issues, "send", send)
    issues.open_issue("token", "owner/project", "Flaky test", "", ["triage", "ci"])
    assert send.call_args.kwargs["json"]["labels"] == ["triage", "ci"]
