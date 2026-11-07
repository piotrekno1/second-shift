"""What a repository's sync state is, from what its last syncs saved.

A sync saves `checked_sha`, the commit it read, and `last_sync_error` when that
commit's configuration was broken. A good sync also saves `config_sha`, the
commit jobs run from. New commits on the branch are reported apart from the
state, because runs keep using the last good sync.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from secondshift.repositories.models import Repository

Status = Literal["valid", "warning", "error"]


class SyncState(StrEnum):
    NEVER_SYNCED = "never_synced"
    # A configuration problem before any good sync. Setup is not finished.
    SETUP_PROBLEM = "setup_problem"
    # A configuration problem after a good sync. Jobs keep the last good one.
    BROKEN = "broken"
    SYNCED = "synced"


# How each state reads. A state with no message shows the problem itself.
STATE_STATUS: dict[SyncState, tuple[Status, str]] = {
    SyncState.NEVER_SYNCED: (
        "warning",
        "Sync the repository before enabling schedules.",
    ),
    SyncState.SETUP_PROBLEM: ("warning", ""),
    SyncState.BROKEN: ("error", ""),
    SyncState.SYNCED: ("valid", "Repository synchronized."),
}


@dataclass(frozen=True)
class SourceStatus:
    status: Status
    message: str
    needs_sync: bool


def sync_state(repository: Repository, problem: str) -> SyncState:
    """`problem` is the configuration problem at the checked commit, if any."""
    if problem:
        return SyncState.BROKEN if repository.config_sha else SyncState.SETUP_PROBLEM
    return SyncState.SYNCED if repository.config_sha else SyncState.NEVER_SYNCED


def source_status(
    repository: Repository, head: str | None, problem: str
) -> SourceStatus:
    """The status of a repository whose branch is at `head`, None when unknown."""
    status, message = STATE_STATUS[sync_state(repository, problem)]
    return SourceStatus(
        status=status,
        message=message or problem,
        # Before the first sync there is nothing to be behind.
        needs_sync=bool(head and repository.checked_sha)
        and head != repository.checked_sha,
    )
