import pytest

from secondshift.repositories.models import Repository
from secondshift.repositories.status import SyncState, source_status, sync_state

GOOD = "a" * 40
NEWER = "b" * 40


def repository(config_sha: str = "", checked_sha: str = "") -> Repository:
    return Repository(config_sha=config_sha, checked_sha=checked_sha)


@pytest.mark.parametrize(
    ("config_sha", "problem", "state", "status"),
    [
        ("", "", SyncState.NEVER_SYNCED, "warning"),
        ("", "No committed .secondshift.yaml.", SyncState.SETUP_PROBLEM, "warning"),
        (GOOD, "Invalid .secondshift.yaml.", SyncState.BROKEN, "error"),
        (GOOD, "", SyncState.SYNCED, "valid"),
    ],
)
def test_each_sync_state_has_one_status(
    config_sha: str, problem: str, state: SyncState, status: str
) -> None:
    checked = repository(config_sha=config_sha, checked_sha=config_sha or NEWER)

    assert sync_state(checked, problem) == state
    assert source_status(checked, checked.checked_sha, problem).status == status


def test_a_problem_is_shown_as_the_message() -> None:
    broken = repository(GOOD, NEWER)

    assert source_status(broken, NEWER, "Invalid manifest.").message == (
        "Invalid manifest."
    )


def test_new_commits_need_a_sync_but_keep_a_synced_repository_valid() -> None:
    synced = repository(GOOD, GOOD)

    behind = source_status(synced, NEWER, "")

    assert (behind.status, behind.needs_sync) == ("valid", True)
    assert source_status(synced, GOOD, "").needs_sync is False


@pytest.mark.parametrize("head", [NEWER, None])
def test_nothing_is_behind_before_the_first_sync_or_an_unknown_head(
    head: str | None,
) -> None:
    assert source_status(repository(), head, "").needs_sync is False
    assert source_status(repository(GOOD, GOOD), None, "").needs_sync is False
