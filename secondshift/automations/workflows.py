import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from dbos import DBOS
from django.conf import settings
from django.utils import timezone

from secondshift.automations.artifacts import job_input_snapshot, read_output_excerpt
from secondshift.automations.execution import execute_agent_run
from secondshift.automations.failures import failure_metadata, failure_summary
from secondshift.automations.models import Job, Run, RunEvent
from secondshift.automations.notifications import (
    notification_targets,
    run_report_payload,
)
from secondshift.automations.planning import build_execution_plan
from secondshift.automations.reconciliation import mark_run_terminal
from secondshift.automations.runner_client import get_runner_client
from secondshift.automations.runner_protocol import RunnerSessionSpec
from secondshift.automations.scheduling import next_occurrence
from secondshift.gateway.tokens import revoke_run_tokens
from secondshift.integrations.models import Connection
from secondshift.integrations.webhooks import deliver_webhook
from secondshift.repositories.materialization import materialize_local_repository
from secondshift.repositories.models import Repository

logger = logging.getLogger(__name__)

EXECUTE_RUN_WORKFLOW = "secondshift.execute_run"
SCHEDULED_JOB_WORKFLOW = "secondshift.scheduled_job"


@DBOS.step(name="secondshift.create_scheduled_run")
def create_scheduled_run_step(scheduled_time: datetime, context: Any) -> int | None:
    job_id = int(context["job_id"])
    try:
        job = Job.objects.select_related("repository").get(
            pk=job_id,
            enabled=True,
            repository__enabled=True,
        )
    except Job.DoesNotExist:
        logger.info("skipping disabled or deleted scheduled job %s", job_id)
        return None

    run, _ = Run.objects.get_or_create(
        job=job,
        scheduled_for=scheduled_time,
        defaults={
            "commit_sha": job.repository.config_sha,
            "input_snapshot": job_input_snapshot(job),
            "execution_plan": build_execution_plan(job),
        },
    )
    job.next_run_at = next_occurrence(job, after=scheduled_time)
    job.save(update_fields=["next_run_at", "updated_at"])
    return run.pk


@DBOS.step(name="secondshift.mark_run_started")
def mark_run_started_step(run_id: int) -> None:
    started = Run.objects.filter(pk=run_id, status=Run.Status.QUEUED).update(
        status=Run.Status.RUNNING,
        started_at=timezone.now(),
        sandbox_runtime="pending-runner-service",
        dbos_workflow_id=DBOS.workflow_id,
    )
    if started:
        RunEvent.objects.create(run_id=run_id, message="Run started.")


@DBOS.step(name="secondshift.record_run_event")
def record_run_event_step(run_id: int, message: str) -> None:
    RunEvent.objects.create(run_id=run_id, message=message)


@DBOS.step(
    name="secondshift.materialize_local_repository",
    retries_allowed=True,
    max_attempts=3,
)
def materialize_local_repository_step(run_id: int) -> None:
    run = Run.objects.select_related("job__repository").get(pk=run_id)
    repository = run.job.repository
    if repository.source_type != Repository.SourceType.LOCAL:
        return
    if not settings.LOCAL_REPOSITORIES_ENABLED:
        raise RuntimeError("Local repositories are available only on a single host.")
    materialize_local_repository(
        source=Path(repository.local_path),
        repository_name=str(run.execution_plan["repository"]["full_name"]),
        commit_sha=run.commit_sha,
        repository_root=settings.LOCAL_REPOSITORY_MIRROR_ROOT,
    )
    RunEvent.objects.create(
        run=run, message=f"Checked out the repository at {run.commit_sha[:12]}."
    )


@DBOS.step(
    name="secondshift.start_runner_session",
    retries_allowed=True,
    max_attempts=3,
)
def start_runner_session_step(run_id: int) -> str:
    run = Run.objects.select_related("job__repository").get(pk=run_id)
    plan = run.execution_plan
    if not plan.get("ready", False):
        raise RuntimeError("The frozen execution plan is not ready.")
    runner_plan = plan.get("runner", {})
    if runner_plan.get("network_policy") != "none":
        raise RuntimeError("The execution plan does not require an offline sandbox.")
    repository_plan = plan.get("repository", {})
    if repository_plan.get("config_sha") != run.commit_sha:
        raise RuntimeError("The run commit does not match its execution plan.")
    session_id = get_runner_client().start(
        RunnerSessionSpec(
            run_id=run.pk,
            repository=str(repository_plan["full_name"]),
            commit_sha=run.commit_sha,
            image=str(runner_plan["image"]),
        )
    )
    Run.objects.filter(pk=run.pk).update(sandbox_runtime="isolated-runner")
    RunEvent.objects.create(run=run, message="Sandbox started without network access.")
    return session_id


@DBOS.step(
    name="secondshift.stop_runner_session",
    retries_allowed=True,
    max_attempts=3,
)
def stop_runner_session_step(session_id: str) -> None:
    get_runner_client().stop(session_id)


@DBOS.step(name="secondshift.finish_run")
def finish_run_step(run_id: int, result: dict[str, Any]) -> None:
    revoke_run_tokens(run_id)
    status = Run.Status.SUCCEEDED if result["succeeded"] else Run.Status.FAILED
    run = Run.objects.get(pk=run_id)
    RunEvent.objects.create(
        run=run,
        level="info" if result["succeeded"] else "error",
        # The report itself is the run's output file; the timeline stays short.
        message=(
            "Run succeeded. The report is stored as the run's output."
            if result["succeeded"]
            else str(result["summary"])
        ),
    )
    run.status = status
    run.exit_code = result["exit_code"]
    run.summary = str(result["summary"])
    run.finished_at = timezone.now()
    update_fields = ["status", "exit_code", "summary", "finished_at"]
    if output := result.get("output"):
        run.output_path = output["path"]
        run.output_size_bytes = output["size_bytes"]
        run.output_sha256 = output["sha256"]
        run.output_truncated = output["truncated"]
        run.output_excerpt = read_output_excerpt(output["path"])
        update_fields += [
            "output_path",
            "output_size_bytes",
            "output_sha256",
            "output_truncated",
            "output_excerpt",
        ]
    run.save(update_fields=update_fields)


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
    run = Run.objects.select_related("job__repository").get(pk=run_id)
    connection = Connection.objects.get(pk=connection_id)
    delivery = deliver_webhook(connection, run_report_payload(run))
    if delivery.retryable:
        raise RetryableDeliveryError(delivery.message)
    RunEvent.objects.create(
        run=run,
        level="info" if delivery.ok else "warning",
        message=f"Notification to {connection.binding_key}: {delivery.message}",
        metadata={"connection_id": connection_id, "status_code": delivery.status_code},
    )


@DBOS.step(name="secondshift.record_notification_failure")
def record_notification_failure_step(run_id: int, connection_id: int) -> None:
    binding = (
        Connection.objects.filter(pk=connection_id)
        .values_list("binding_key", flat=True)
        .first()
    )
    RunEvent.objects.create(
        run_id=run_id,
        level="warning",
        message=f"Notification to {binding or 'a connection'} failed after retries.",
        metadata={"connection_id": connection_id},
    )


def notify(run_id: int) -> None:
    # Delivery problems are recorded on the run but never change its outcome.
    for connection_id in notification_targets_step(run_id):
        try:
            deliver_run_notification_step(run_id, connection_id)
        except Exception:
            logger.warning(
                "notification for run %s to connection %s failed",
                run_id,
                connection_id,
            )
            record_notification_failure_step(run_id, connection_id)


@DBOS.step(name="secondshift.fail_run")
def fail_run_step(run_id: int, summary: str, details: dict[str, object]) -> None:
    revoke_run_tokens(run_id)
    mark_run_terminal(
        run_id,
        Run.Status.FAILED,
        summary,
        workflow_status="ERROR",
        metadata=details,
    )


def execute_run(run_id: int) -> None:
    session_id: str | None = None
    stage = "starting"
    try:
        mark_run_started_step(run_id)
        stage = "checkout"
        materialize_local_repository_step(run_id)
        stage = "sandbox"
        session_id = start_runner_session_step(run_id)
        stage = "skill"
        run = Run.objects.select_related("job").get(pk=run_id)
        record_run_event_step(
            run_id, f"Running {run.input_snapshot.get('skill_path') or 'the skill'}."
        )
        result = execute_agent_run(run, session_id)
        stage = "finishing"
        stop_runner_session_step(session_id)
        session_id = None
        finish_run_step(run_id, result)
    except Exception as error:
        if session_id is not None:
            try:
                stop_runner_session_step(session_id)
            except Exception:
                logger.exception("failed to stop runner session %s", session_id)
        fail_run_step(
            run_id, failure_summary(stage, error), failure_metadata(stage, error)
        )
        raise
    notify(run_id)


@DBOS.workflow(name=EXECUTE_RUN_WORKFLOW)
def execute_run_workflow(run_id: int) -> None:
    execute_run(run_id)


@DBOS.workflow(name=SCHEDULED_JOB_WORKFLOW)
def scheduled_job_workflow(scheduled_time: datetime, context: Any) -> None:
    run_id = create_scheduled_run_step(scheduled_time, context)
    if run_id is not None:
        execute_run(run_id)
