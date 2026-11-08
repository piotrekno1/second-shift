"""Queries and writes on runner sessions and the commands run in them."""

from typing import Any

from secondshift.runner.models import RunnerCommand, RunnerSession


def has_sessions() -> bool:
    """Whether any session exists. The health check uses it to reach the database."""
    return RunnerSession.objects.exists()


def get_session(session_id: str) -> RunnerSession:
    """The session. Raises RunnerSession.DoesNotExist when there is none."""
    return RunnerSession.objects.get(session_id=session_id)


def lock_session(session_id: str, active_only: bool = False) -> RunnerSession | None:
    """Lock the session for this transaction. None when it does not exist."""
    sessions = RunnerSession.objects.select_for_update().filter(session_id=session_id)
    if active_only:
        sessions = sessions.filter(status=RunnerSession.Status.ACTIVE)
    return sessions.first()


def is_session_active(session_id: str) -> bool:
    return RunnerSession.objects.filter(
        session_id=session_id, status=RunnerSession.Status.ACTIVE
    ).exists()


def create_session(**fields: Any) -> RunnerSession:
    """Record a new session. Raises IntegrityError when the run already has one."""
    return RunnerSession.objects.create(**fields)


def set_session_status(session_id: str, status: RunnerSession.Status) -> None:
    RunnerSession.objects.filter(session_id=session_id).update(status=status)


def session_command(session: RunnerSession, call_id: str) -> RunnerCommand | None:
    """The command already reserved for this call, if any."""
    return RunnerCommand.objects.filter(session=session, call_id=call_id).first()


def create_command(
    session: RunnerSession, call_id: str, fingerprint: str
) -> RunnerCommand:
    """Reserve a call before running it."""
    return RunnerCommand.objects.create(
        session=session, call_id=call_id, fingerprint=fingerprint
    )


def complete_command(
    session: RunnerSession, call_id: str, exit_code: int, output: str, timed_out: bool
) -> None:
    """Store the outcome of a reserved call."""
    RunnerCommand.objects.filter(session=session, call_id=call_id).update(
        status=RunnerCommand.Status.COMPLETED,
        exit_code=exit_code,
        output=output,
        timed_out=timed_out,
    )
