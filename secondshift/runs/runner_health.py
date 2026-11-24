"""Checking each runner, and what happens to runs on one that stops answering.

The worker checks every registered runner each minute, except those an
operator marked down. A runner that answers has its environments refreshed
and, at first contact, its provisioning key pinned. A runner that doesn't is
failing from that moment until it answers again:

- After `runners.UNREACHABLE_SECONDS` it takes no new runs, and logins whose
  home it is move.
- After `LOST_SECONDS` its active runs fail. Their sandbox is gone or out of
  reach, and a run never moves mid-way. Their sessions are kept to stop when
  it answers again, since the runner keeps them until told.

Health never changes the operator's `state`, so a drained runner that comes
back stays drained.
"""

import logging

from django.db import transaction

from secondshift.runner_protocol.sessions import RunnerProtocolError, session_id_for
from secondshift.runs import dao
from secondshift.runs.models import Run, RunFailure, Runner
from secondshift.runs.runners import pin_first_key, runner_client
from secondshift.runs.transitions import finish_run

logger = logging.getLogger(__name__)

# A runner that hangs must not hold up the worker's other tasks.
CHECK_TIMEOUT_SECONDS = 3
# After its checks have failed for this long, a runner's active runs fail. Long
# enough that a restart or a short network outage does not end them.
LOST_SECONDS = 10 * 60
LOST_SUMMARY = "Runner: it stopped answering, so the run could not finish."
LOST_REASON = "The runner stopped answering for 10 minutes."


def refresh_runners() -> int:
    """Check each runner, then fail runs on lost ones. Returns how many answered.

    A runner that does not answer keeps the environments it last offered.
    """
    answered = 0
    for runner in dao.all_runners():
        if runner.state == Runner.State.DOWN:
            continue
        if check_runner(runner):
            answered += 1
    fail_runs_on_lost_runners()
    return answered


def check_runner(runner: Runner) -> bool:
    """Ask the runner what it offers, and record whether it answered."""
    try:
        with runner_client(runner) as client:
            offered = client.environments(timeout=CHECK_TIMEOUT_SECONDS)
            key = runner.provisioning_key or client.current_provisioning_key(
                timeout=CHECK_TIMEOUT_SECONDS
            )
    except RunnerProtocolError as error:
        logger.warning("runner %s did not answer: %s", runner.name, error)
        dao.mark_runner_failing(runner.pk)
        return False
    dao.set_runner_environments(runner.pk, offered.images, offered.default)
    if not runner.provisioning_key:
        pin_first_key(runner, key)
    if dao.mark_runner_answering(runner.pk):
        logger.info("runner %s answers again", runner.name)
    stop_lost_sessions(runner)
    return True


def fail_runs_on_lost_runners() -> int:
    """Fail the active runs on each runner that has stopped answering.

    Returns how many runs failed. The run's workflow may still be waiting on
    the runner. When that call ends, the run is already finished and the
    workflow changes nothing.
    """
    failed = 0
    for runner in dao.runners_failing_for(LOST_SECONDS):
        for run_id in dao.active_run_ids_on(runner.pk):
            failed += int(fail_lost_run(run_id, runner))
    return failed


@transaction.atomic
def fail_lost_run(run_id: int, runner: Runner) -> bool:
    run = finish_run(
        run_id,
        Run.Status.FAILED,
        LOST_SUMMARY,
        failure=RunFailure.RUNNER,
        reason=LOST_REASON,
    )
    if run is None:
        return False
    dao.add_event(
        run.pk,
        f"Runner {runner.name} stopped answering, so the run failed. Its "
        "session is stopped when the runner answers again.",
        level="error",
        metadata={"runner": runner.name},
    )
    session_id = session_id_for(run.pk, run.runner_session_key.hex)
    dao.add_session_cleanup(run.pk, runner.pk, session_id)
    return True


def stop_lost_sessions(runner: Runner) -> int:
    """Stop the sessions of runs that failed while this runner was lost.

    Returns how many it stopped. Stopping a session the runner never started
    or already stopped is fine.
    """
    stopped = 0
    for cleanup in dao.session_cleanups_on(runner.pk):
        try:
            with runner_client(runner) as client:
                client.stop(cleanup.session_id)
        except RunnerProtocolError:
            logger.warning(
                "runner %s did not stop session %s", runner.name, cleanup.session_id
            )
            return stopped
        cleanup.delete()
        stopped += 1
    return stopped
