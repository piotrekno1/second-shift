import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from dbos import DBOS
from django.db import transaction
from django.utils import timezone

from secondshift.config.constants import (
    EXECUTE_RUN_WORKFLOW,
    RETRY_ISSUES_WORKFLOW,
    RETRY_NOTIFICATION_WORKFLOW,
    RETRY_PULL_REQUEST_WORKFLOW,
    SCHEDULED_JOB_WORKFLOW,
    RunSandbox,
)
from secondshift.connections.models import Connection
from secondshift.connections.webhooks import deliver_webhook
from secondshift.jobs.dao import scheduled_job
from secondshift.jobs.readiness import execution_problems
from secondshift.jobs.scheduling import next_occurrence
from secondshift.repositories.dao import lock_repository
from secondshift.repositories.github_access import github_access
from secondshift.repositories.models import Repository
from secondshift.repositories.sources import source_snapshot
from secondshift.runner.client import get_runner_client
from secondshift.runner.protocol import RunnerSessionSpec
from secondshift.runs.artifacts import read_output_excerpt, store_collection
from secondshift.runs.execution.budgets import Budget, budget_reached_message
from secondshift.runs.execution.deadlines import (
    run_deadline,
    run_timeout_seconds,
    timeout_summary,
)
from secondshift.runs.execution.issues import issue_limit
from secondshift.runs.execution.run import execute_agent_run
from secondshift.runs.history import pack_earlier_files
from secondshift.runs.issue_delivery import open_run_issues
from secondshift.runs.models import Run, RunDelivery, RunEvent
from secondshift.runs.orchestration.admission import task_admission_problem
from secondshift.runs.orchestration.reconciliation import mark_run_terminal
from secondshift.runs.publishing import publish_run
from secondshift.runs.reporting.collection import collection_summary
from secondshift.runs.reporting.failures import failure_metadata, failure_summary
from secondshift.runs.reporting.notifications import (
    notification_targets,
    run_report_payload,
)
from secondshift.runs.services import create_scheduled_run
from secondshift.runs.transitions import finish_run, start_run

logger = logging.getLogger(__name__)


@DBOS.step(name="secondshift.create_scheduled_run")
def create_scheduled_run_step(scheduled_time: datetime, context: Any) -> int | None:
    job_id = int(context["job_id"])
    job = scheduled_job(job_id)
    if job is None:
        logger.info("skipping disabled or deleted scheduled job %s", job_id)
        return None

    run = create_scheduled_run(job, scheduled_time)
    job.next_run_at = next_occurrence(job, after=scheduled_time)
    job.save(update_fields=["next_run_at", "updated_at"])
    if run is None:
        logger.info("skipped scheduled job %s: a run has not finished", job_id)
        return None
    return run.pk


class RunNotReadyError(RuntimeError):
    """The run's access plan was not ready when it was created.

    A RuntimeError, so its message, which SecondShift writes, shows in the summary.
    """


@DBOS.step(name="secondshift.mark_run_started")
@transaction.atomic
def mark_run_started_step(run_id: int) -> bool:
    """Start a queued run. Raises RunNotReadyError before any work is done."""
    run = Run.objects.select_related("task").get(pk=run_id)
    if run.job_id is None:
        repository = lock_repository(run.task.repository_id)
        run.refresh_from_db()
        if problem := task_admission_problem(run, repository):
            mark_run_terminal(
                run.pk, Run.Status.FAILED, problem, workflow_status="NOT_ADMITTED"
            )
            return False
    if run.status == Run.Status.RUNNING and run.dbos_workflow_id == DBOS.workflow_id:
        # The application commit may precede DBOS persisting this step's result.
        return True
    plan = run.execution_plan
    if run.status == Run.Status.QUEUED and not plan.get("ready", False):
        # A scheduled tick creates the run even when the job lost its access.
        problems = execution_problems(run.repository, plan)
        raise RunNotReadyError(
            " ".join(problems) or "The frozen execution plan is not ready."
        )
    started = start_run(run_id, DBOS.workflow_id or run.dbos_workflow_id)
    if started:
        RunEvent.objects.create(run_id=run_id, message="Run started.")
    return bool(started)


@DBOS.step(name="secondshift.record_run_event")
def record_run_event_step(run_id: int, message: str) -> None:
    RunEvent.objects.create(run_id=run_id, message=message)


# The name stays from when this step copied local repositories, so runs that
# were in flight during an upgrade still replay.
@DBOS.step(
    name="secondshift.materialize_local_repository",
    retries_allowed=True,
    max_attempts=3,
)
def prepare_source_step(run_id: int) -> None:
    """Build the snapshot of the run's commit, fetching it if needed."""
    run = Run.objects.select_related("job__repository", "task__repository").get(
        pk=run_id
    )
    repository = run.repository
    if repository.source_type != Repository.SourceType.LOCAL:
        # Lost access stops the run here, even if the snapshot is cached.
        github_access(repository)
    snapshot = source_snapshot(repository, run.commit_sha)
    RunEvent.objects.create(
        run=run,
        message=(
            f"Checked out the repository at {run.commit_sha[:12]} "
            f"({snapshot.size_bytes / (1024 * 1024):.1f} MiB compressed)."
        ),
    )


@DBOS.step(
    name="secondshift.start_runner_session",
    retries_allowed=True,
    max_attempts=3,
)
def start_runner_session_step(run_id: int) -> str:
    run = Run.objects.select_related("job__repository", "task__repository").get(
        pk=run_id
    )
    plan = run.execution_plan
    runner_plan = plan.get("runner", {})
    if runner_plan.get("network_policy") != "none":
        raise RuntimeError("The execution plan does not require an offline sandbox.")
    repository_plan = plan.get("repository", {})
    if repository_plan.get("config_sha") != run.commit_sha:
        raise RuntimeError("The run commit does not match its execution plan.")
    snapshot = source_snapshot(run.repository, run.commit_sha)
    client = get_runner_client()
    client.upload_source(snapshot.sha256, snapshot.path)
    with tempfile.TemporaryDirectory(prefix="secondshift-history-") as folder:
        archive = Path(folder) / "history.tar.gz"
        history = pack_earlier_files(run, archive)
        if history is not None:
            client.upload_source(history.sha256, archive)
            run.input_snapshot["history_files"] = history.record
            Run.objects.filter(pk=run.pk).update(input_snapshot=run.input_snapshot)
    session_id = client.start(
        RunnerSessionSpec(
            run_id=run.pk,
            session_key=run.runner_session_key.hex,
            repository=str(repository_plan["full_name"]),
            commit_sha=run.commit_sha,
            image=str(runner_plan["image"]),
            source_sha256=snapshot.sha256,
            collect_changes=bool(runner_plan.get("collect_changes", False)),
            history_sha256=history.sha256 if history is not None else "",
        )
    )
    Run.objects.filter(pk=run.pk).update(sandbox_runtime=RunSandbox.ISOLATED_RUNNER)
    RunEvent.objects.create(run=run, message="Sandbox started without network access.")
    return session_id


@DBOS.step(
    name="secondshift.collect_run_outputs",
    retries_allowed=True,
    max_attempts=3,
)
def collect_outputs_step(run_id: int, session_id: str) -> None:
    """Keep the agent's file changes and saved files before the sandbox goes."""
    run = Run.objects.select_related("job", "task__repository").get(pk=run_id)
    with tempfile.TemporaryDirectory(prefix="secondshift-collection-") as folder:
        archive = Path(folder) / "collection.tar.gz"
        get_runner_client().collect(session_id, archive)
        outputs = store_collection(run, archive)
    Run.objects.filter(pk=run_id).update(outputs=outputs)
    if message := collection_summary(outputs):
        RunEvent.objects.create(run_id=run_id, message=message)


@DBOS.step(name="secondshift.record_collection_failure")
def record_collection_failure_step(run_id: int) -> None:
    RunEvent.objects.create(
        run_id=run_id,
        level="warning",
        message="The run's file changes and saved files could not be collected.",
    )


def collect_outputs(run_id: int, session_id: str) -> None:
    # The report is already stored, so a failure here never changes the outcome.
    try:
        collect_outputs_step(run_id, session_id)
    except Exception:
        logger.warning("could not collect outputs for run %s", run_id)
        record_collection_failure_step(run_id)


@DBOS.step(
    name="secondshift.stop_runner_session",
    retries_allowed=True,
    max_attempts=3,
)
def stop_runner_session_step(session_id: str) -> None:
    get_runner_client().stop(session_id)


@DBOS.step(name="secondshift.finish_run")
@transaction.atomic
def finish_run_step(run_id: int, result: dict[str, Any]) -> None:
    status = Run.Status.SUCCEEDED if result["succeeded"] else Run.Status.FAILED
    fields: dict[str, object] = {"exit_code": result["exit_code"]}
    if output := result.get("output"):
        fields |= {
            "output_path": output["path"],
            "output_size_bytes": output["size_bytes"],
            "output_sha256": output["sha256"],
            "output_truncated": output["truncated"],
            "output_excerpt": read_output_excerpt(output["path"]),
        }
    run = finish_run(
        run_id,
        status,
        str(result["summary"]),
        only_from={Run.Status.RUNNING},
        fields=fields,
    )
    if run is None:
        # A replay, or the run was canceled while it ran. Its result stands.
        logger.info("run %s had already finished", run_id)
        return
    details = result.get("details") or {}
    if reached := details.get("budget_reached"):
        RunEvent.objects.create(
            run=run,
            level="warning",
            message=budget_reached_message(Budget(reached)),
            metadata=details,
        )
    RunEvent.objects.create(
        run=run,
        level="info" if result["succeeded"] else "error",
        metadata=details,
        # The report itself is the run's output file; the timeline stays short.
        message=(
            "Run succeeded. The report is stored as the run's output."
            if result["succeeded"]
            else str(result["summary"])
        ),
    )


class RetryableDeliveryError(Exception):
    pass


@DBOS.step(name="secondshift.notification_targets")
def notification_targets_step(run_id: int) -> list[int]:
    return notification_targets(Run.objects.select_related("job").get(pk=run_id))


@DBOS.step(
    name="secondshift.deliver_run_notification",
    retries_allowed=True,
    max_attempts=3,
)
def deliver_run_notification_step(run_id: int, connection_id: int) -> None:
    # One step per target, so a retry never re-sends to targets that succeeded.
    run = Run.objects.select_related("job__repository", "task__repository").get(
        pk=run_id
    )
    connection = Connection.objects.get(pk=connection_id)
    delivery, _ = RunDelivery.objects.get_or_create(
        run=run,
        kind=RunDelivery.Kind.NOTIFICATION,
        connection=connection,
        defaults={"target": connection.binding_key or connection.display_name},
    )
    if delivery.status == RunDelivery.Status.DELIVERED:
        return
    # A grant revoked or a connection disconnected since planning stops delivery,
    # including when a failed delivery is retried later.
    if connection_id not in notification_targets(run):
        record_delivery(delivery, RunDelivery.Status.SKIPPED, "No longer approved.")
        return
    result = deliver_webhook(connection, run_report_payload(run))
    delivery.attempts += 1
    delivery.status_code = result.status_code
    if result.retryable:
        record_delivery(delivery, RunDelivery.Status.RETRYING, result.message)
        raise RetryableDeliveryError(result.message)
    record_delivery(
        delivery,
        RunDelivery.Status.DELIVERED if result.ok else RunDelivery.Status.FAILED,
        result.message,
    )
    RunEvent.objects.create(
        run=run,
        level="info" if result.ok else "warning",
        message=f"Notification to {connection.binding_key}: {result.message}",
        metadata={"connection_id": connection_id, "status_code": result.status_code},
    )


def record_delivery(delivery: RunDelivery, status: str, message: str) -> None:
    delivery.status = status
    delivery.message = message
    delivery.save(
        update_fields=["status", "message", "attempts", "status_code", "updated_at"]
    )


@DBOS.step(name="secondshift.record_notification_failure")
def record_notification_failure_step(run_id: int, connection_id: int) -> None:
    binding = (
        Connection.objects.filter(pk=connection_id)
        .values_list("binding_key", flat=True)
        .first()
    )
    # The last attempt's message stays, since it says why delivery failed.
    RunDelivery.objects.filter(
        run_id=run_id,
        kind=RunDelivery.Kind.NOTIFICATION,
        connection_id=connection_id,
    ).exclude(status=RunDelivery.Status.DELIVERED).update(
        status=RunDelivery.Status.FAILED, updated_at=timezone.now()
    )
    RunEvent.objects.create(
        run_id=run_id,
        level="warning",
        message=f"Notification to {binding or 'a connection'} failed after retries.",
        metadata={"connection_id": connection_id},
    )


def deliver_notification(run_id: int, connection_id: int) -> None:
    try:
        deliver_run_notification_step(run_id, connection_id)
    except Exception:
        logger.warning(
            "notification for run %s to connection %s failed",
            run_id,
            connection_id,
        )
        record_notification_failure_step(run_id, connection_id)


@DBOS.step(name="secondshift.publish_pull_request")
def publish_pull_request_step(run_id: int) -> None:
    publish_run(run_id)


@DBOS.workflow(name=RETRY_PULL_REQUEST_WORKFLOW)
def retry_pull_request_workflow(run_id: int) -> None:
    publish_pull_request_step(run_id)


@DBOS.step(name="secondshift.open_issues")
def open_issues_step(run_id: int) -> None:
    open_run_issues(run_id)


@DBOS.workflow(name=RETRY_ISSUES_WORKFLOW)
def retry_issues_workflow(run_id: int) -> None:
    open_issues_step(run_id)


def deliver(run_id: int) -> None:
    """Publish first, so the notification can link to the pull request and issues."""
    run = Run.objects.only("execution_plan").get(pk=run_id)
    # Only plans that ask for a pull request or issues add the step, so
    # workflows started before these steps existed replay the same steps.
    if run.execution_plan.get("pull_request"):
        try:
            publish_pull_request_step(run_id)
        except Exception:
            logger.exception("could not publish run %s", run_id)
    if issue_limit(run.execution_plan):
        try:
            open_issues_step(run_id)
        except Exception:
            logger.exception("could not open issues for run %s", run_id)
    notify(run_id)


def notify(run_id: int) -> None:
    # Delivery problems are recorded on the run but never change its outcome.
    for connection_id in notification_targets_step(run_id):
        deliver_notification(run_id, connection_id)


@DBOS.workflow(name=RETRY_NOTIFICATION_WORKFLOW)
def retry_notification_workflow(run_id: int, connection_id: int) -> None:
    deliver_notification(run_id, connection_id)


@DBOS.step(name="secondshift.fail_run")
def fail_run_step(run_id: int, summary: str, details: dict[str, object]) -> None:
    mark_run_terminal(
        run_id,
        Run.Status.FAILED,
        summary,
        workflow_status="ERROR",
        metadata=details,
    )


def execute_run(run_id: int) -> None:
    session_id: str | None = None
    deadline: datetime | None = None
    timeout_seconds = 0
    stage = "starting"
    try:
        # Older completed steps returned None; preserve their replay behavior.
        if mark_run_started_step(run_id) is False:
            return
        stage = "checkout"
        prepare_source_step(run_id)
        stage = "sandbox"
        session_id = start_runner_session_step(run_id)
        stage = "skill"
        run = Run.objects.select_related("job").get(pk=run_id)
        record_run_event_step(
            run_id,
            f"Running {run.input_snapshot.get('skill_path') or 'the submitted task'}.",
        )
        deadline = run_deadline(run)
        timeout_seconds = run_timeout_seconds(run)
        result = execute_agent_run(run, session_id)
        stage = "finishing"
        collect_outputs(run_id, session_id)
        stop_runner_session_step(session_id)
        session_id = None
        finish_run_step(run_id, result)
    except Exception as error:
        if session_id is not None:
            try:
                stop_runner_session_step(session_id)
            except Exception:
                logger.exception("failed to stop runner session %s", session_id)
        details = failure_metadata(stage, error)
        if stage == "skill" and deadline is not None and timezone.now() >= deadline:
            # The skill stopped after its time limit, so the error is the model
            # call or tool call that hit the deadline.
            summary = timeout_summary(timeout_seconds)
            details["timed_out"] = True
        else:
            summary = failure_summary(stage, error)
        fail_run_step(run_id, summary, details)
        # A failed run notifies too, so a scheduled job never fails silently.
        try:
            deliver(run_id)
        except Exception:
            logger.exception("could not notify about failed run %s", run_id)
        raise
    deliver(run_id)


@DBOS.workflow(name=EXECUTE_RUN_WORKFLOW)
def execute_run_workflow(run_id: int) -> None:
    execute_run(run_id)


@DBOS.workflow(name=SCHEDULED_JOB_WORKFLOW)
def scheduled_job_workflow(scheduled_time: datetime, context: Any) -> None:
    run_id = create_scheduled_run_step(scheduled_time, context)
    if run_id is not None:
        execute_run(run_id)
