from unittest.mock import Mock

import httpx
import pytest

from secondshift.github import permissions


def test_each_read_is_probed_on_the_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = Mock(
        side_effect=lambda method, url, token: httpx.Response(
            403 if "/actions/" in url else 200
        )
    )
    monkeypatch.setattr(permissions, "send", send)
    reads = permissions.token_reads(
        "token", "owner/project", "feature/x", permissions.SYNC + permissions.TOOLS
    )
    assert reads == {
        "contents": True,
        "pull_requests": True,
        "actions": False,
        "statuses": True,
    }
    urls = [call.args[1] for call in send.call_args_list]
    assert all(
        url.startswith("https://api.github.com/repos/owner/project/") for url in urls
    )
    assert urls[-1].endswith("/commits/feature%2Fx/status")


def test_granted_access_reads_the_installation() -> None:
    granted = {"contents": "write", "pull_requests": "read"}
    assert permissions.granted_reads(granted, ("contents", "actions")) == {
        "contents": True,
        "actions": False,
    }
    assert permissions.granted_writes(granted, permissions.DRAFT_PULL_REQUESTS) == {
        "contents": True,
        "pull_requests": False,
    }
