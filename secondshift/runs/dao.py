"""Queries and writes on runs, tasks, run events, deliveries, issues and leases."""

from collections.abc import Collection, Iterable
from datetime import date, datetime
from typing import Any

from django.db import transaction
from django.db.models import Count, Exists, F, OuterRef, Q, QuerySet, Subquery, Sum
from django.db.models.functions import Coalesce, TruncDate

from secondshift.config.constants import AgentBackend
from secondshift.connections.models import Connection
from secondshift.jobs.dao import active_jobs
from secondshift.jobs.models import Job
from secondshift.jobs.plan import ExecutionPlan
from secondshift.runner.agent_image.outcomes import CACHE_READ_DIVISOR
from secondshift.runs.models import (
    RUN_ENQUEUE_FAILED,
    AgentLease,
    Run,
    RunDelivery,
    RunEvent,
    RunIssue,
    Task,
)

# Runs the queue could not hand off. They never started, so they do not count
# as earlier or recent runs.
ENQUEUE_FAILED = Q(status=Run.Status.FAILED, summary=RUN_ENQUEUE_FAILED)
NEWEST_FIRST = ("-created_at", "-pk")


# Runs


def get_run(run_id: int, *related: str) -> Run:
    """The run with these relations loaded. Raises Run.DoesNotExist when missing."""
    return Run.objects.select_related(*related).get(pk=run_id)


def lock_run(run_id: int, *related: str) -> Run:
    """Lock the run for this transaction, with these relations loaded."""
    return Run.objects.select_for_update().select_related(*related).get(pk=run_id)


def lock_run_in(run_id: int, statuses: Collection[str]) -> Run | None:
    """Lock the run while it is in one of these statuses. None otherwise."""
    return (
        Run.objects.select_for_update().filter(pk=run_id, status__in=statuses).first()
    )


def create_run_record(**fields: Any) -> Run:
    """A run counts against its agent login only when its plan was ready."""
    plan = fields.get("execution_plan")
    if plan:
        typed = ExecutionPlan.model_validate(plan)
        if typed.ready:
            fields["agent_connection_id"] = typed.execution.connection_id
    return Run.objects.create(**fields)


def update_run(run_id: int, **fields: Any) -> None:
    """Save these fields on the run. Its status changes only in runs.transitions."""
    if "status" in fields:
        raise ValueError("Change a run's status through runs.transitions.")
    Run.objects.filter(pk=run_id).update(**fields)


def with_model_usage(runs: QuerySet[Run]) -> QuerySet[Run]:
    """Annotate each run with its model usage totals and agent login usage."""
    agent_usage = (
        RunEvent.objects.filter(
            run_id=OuterRef("pk"),
            metadata__backend__in=[
                backend for backend in AgentBackend if not backend.uses_model_api
            ],
            metadata__has_key="usage",
        )
        .order_by("-pk")
        .values("metadata__usage")[:1]
    )
    return runs.annotate(
        agent_usage=Subquery(agent_usage),
        model_tokens=Sum(
            F("model_usage__input_tokens")
            + F("model_usage__output_tokens")
            + F("model_usage__cache_read_tokens")
            + F("model_usage__cache_write_tokens")
        ),
        # Cached prompt tokens are still input the model read.
        model_input_tokens=Sum(
            F("model_usage__input_tokens")
            + F("model_usage__cache_read_tokens")
            + F("model_usage__cache_write_tokens")
        ),
        model_output_tokens=Sum("model_usage__output_tokens"),
        model_cache_read_tokens=Sum("model_usage__cache_read_tokens"),
        # The gateway's count, request by request: cache reads as a tenth,
        # rounded up. Integer division matches it exactly.
        model_budget_tokens=Sum(
            F("model_usage__input_tokens")
            + F("model_usage__output_tokens")
            + F("model_usage__cache_write_tokens")
            + (F("model_usage__cache_read_tokens") + CACHE_READ_DIVISOR - 1)
            / CACHE_READ_DIVISOR
        ),
        model_requests=Count("model_usage"),
        model_cost_usd=Sum("model_usage__cost_usd"),
        unpriced_requests=Count(
            "model_usage", filter=Q(model_usage__cost_usd__isnull=True)
        ),
    )


def find_workspace_run(workspace_id: int | None, run_id: int) -> Run | None:
    """The workspace's run with its job, task, repository and usage."""
    return (
        with_model_usage(
            Run.objects.select_related("job__repository", "task__repository")
        )
        .filter(task__repository__workspace_id=workspace_id, pk=run_id)
        .first()
    )


def workspace_runs(workspace_id: int | None) -> QuerySet[Run]:
    """The workspace's job runs and ad hoc tasks."""
    return Run.objects.filter(task__repository__workspace_id=workspace_id)


def runs_by_id(run_ids: Iterable[int]) -> dict[int, Run]:
    return Run.objects.in_bulk(list(run_ids))


def job_runs_by_id(job_id: int, run_ids: Iterable[int]) -> dict[int, Run]:
    """These runs, only where they belong to the job."""
    return Run.objects.filter(job_id=job_id).in_bulk(list(run_ids))


def newest_job_runs(job: Job) -> QuerySet[Run]:
    """The job's runs with their repository, newest first."""
    return job.runs.select_related("job__repository").order_by(*NEWEST_FIRST)


def succeeded_run_ids(job: Job, count: int) -> list[int]:
    """The job's newest successful runs, newest first."""
    runs = Run.objects.filter(job=job, status=Run.Status.SUCCEEDED)
    return list(runs.order_by("-created_at").values_list("pk", flat=True)[:count])


def job_run_for_slot(job: Job, scheduled_for: datetime) -> Run | None:
    """The run already created for this scheduled time, if any."""
    return job.runs.filter(scheduled_for=scheduled_for).first()


def active_runs() -> QuerySet[Run]:
    """Runs that have not finished yet."""
    return Run.objects.filter(status__in=Run.ACTIVE_STATUSES)


def latest_active_run(job: Job) -> Run | None:
    """The job's newest unfinished run, if it has one."""
    return active_runs().filter(job=job).order_by("-pk").first()


def active_tasks(repository_id: int) -> QuerySet[Run]:
    """Unfinished ad hoc task runs in the repository."""
    return active_runs().filter(job__isnull=True, task__repository_id=repository_id)


def active_task_count(repository_id: int) -> int:
    return active_tasks(repository_id).count()


def has_dispatched_task(repository_id: int) -> bool:
    """Whether an unfinished task in the repository is already with the queue."""
    return active_tasks(repository_id).exclude(dbos_workflow_id="").exists()


def repository_task_run(repository_id: int, run_id: int) -> Run:
    """The repository's ad hoc task run, with its task."""
    return (
        Run.objects.select_related("task")
        .filter(job__isnull=True, task__repository_id=repository_id)
        .get(pk=run_id)
    )


def first_due_task(repository_id: int, now: datetime) -> Run | None:
    """The repository's oldest queued task that is due and not yet dispatched."""
    return (
        Run.objects.filter(
            job__isnull=True,
            task__repository_id=repository_id,
            status=Run.Status.QUEUED,
            scheduled_for__lte=now,
            dbos_workflow_id="",
        )
        .order_by("scheduled_for", "created_at", "pk")
        .first()
    )


def waiting_tasks(now: datetime, limit: int) -> list[Run]:
    """Due tasks not yet dispatched, in repositories with nothing dispatched."""
    blocked = (
        active_runs()
        .filter(job__isnull=True, task__repository_id=OuterRef("task__repository_id"))
        .exclude(dbos_workflow_id="")
    )
    return list(
        Run.objects.filter(
            job__isnull=True,
            status=Run.Status.QUEUED,
            dbos_workflow_id="",
            scheduled_for__lte=now,
        )
        .filter(~Exists(blocked))
        .select_related("task")
        .order_by("scheduled_for", "created_at", "pk")[:limit]
    )


def active_workflows(limit: int) -> list[tuple[int, str]]:
    """Unfinished runs that the queue accepted, as (run id, workflow id)."""
    return list(
        active_runs()
        .exclude(dbos_workflow_id="")
        .values_list("pk", "dbos_workflow_id")[:limit]
    )


def run_for_repeat_key(job: Job, key: str, finished_since: datetime) -> Run | None:
    """The job's newest run for this event that is active or finished since then."""
    return (
        Run.objects.filter(job=job, input_snapshot__repeat_key=key)
        .filter(Q(status__in=Run.ACTIVE_STATUSES) | Q(finished_at__gte=finished_since))
        .exclude(ENQUEUE_FAILED)
        .order_by("-pk")
        .first()
    )


def started_run_count(job: Job, started_by: str, since: datetime) -> int:
    """How many runs this source started for the job since then."""
    return (
        Run.objects.filter(job=job, started_by=started_by, created_at__gte=since)
        .exclude(ENQUEUE_FAILED)
        .count()
    )


def finished_runs_before(cutoff: datetime) -> list[Run]:
    """Finished runs that ended before the cutoff, with their jobs."""
    return list(
        Run.objects.filter(
            status__in=Run.FINISHED_STATUSES, finished_at__lt=cutoff
        ).select_related("job")
    )


@transaction.atomic
def delete_runs(runs: list[Run]) -> None:
    """Delete the runs, and their tasks once no other attempt is left."""
    task_ids = {run.task_id for run in runs if run.task_id is not None}
    Run.objects.filter(pk__in=[run.pk for run in runs]).delete()
    # Never sweep unrelated assignments here.
    Task.objects.filter(pk__in=task_ids, runs__isnull=True).delete()


# Reporting


def run_counts(workspace_id: int, start: datetime, previous_start: datetime) -> dict:
    """Runs since `start`, finished and succeeded among them, and runs before."""
    recent = Q(created_at__gte=start)
    return workspace_runs(workspace_id).aggregate(
        runs=Count("pk", filter=recent),
        previous=Count(
            "pk", filter=Q(created_at__gte=previous_start, created_at__lt=start)
        ),
        finished=Count("pk", filter=recent & Q(status__in=Run.FINISHED_STATUSES)),
        succeeded=Count("pk", filter=recent & Q(status=Run.Status.SUCCEEDED)),
    )


def run_counts_today(workspace_id: int, today: date) -> dict:
    """Runs created today, and failed runs at any time."""
    return workspace_runs(workspace_id).aggregate(
        today=Count("pk", filter=Q(created_at__date=today)),
        failed=Count("pk", filter=Q(status=Run.Status.FAILED)),
    )


def finished_runs_per_day(
    workspace_id: int, first: date, last: date
) -> list[dict[str, Any]]:
    """Finished runs per local day and status, as rows of day, status and count."""
    return list(
        workspace_runs(workspace_id)
        .filter(
            status__in=Run.FINISHED_STATUSES,
            created_at__date__gte=first,
            created_at__date__lte=last,
        )
        .annotate(day=TruncDate("created_at"))
        .values("day", "status")
        .annotate(count=Count("pk"))
        .order_by()
    )


def search_runs(runs: QuerySet[Run], q: str) -> QuerySet[Run]:
    """Runs whose job, repository, task name or summary contains the text."""
    q = q.strip()
    if not q:
        return runs
    match = (
        Q(job__name__icontains=q)
        | Q(job__key__icontains=q)
        | Q(task__repository__full_name__icontains=q)
        | Q(input_snapshot__name__icontains=q)
        | Q(summary__icontains=q)
    )
    # "#42" or "42" also finds run 42.
    if (number := q.removeprefix("#")).isdigit():
        match |= Q(pk=int(number))
    return runs.filter(match)


def run_listing(
    workspace_id: int,
    *,
    q: str,
    statuses: Collection[str],
    job_id: int | None,
    repository_id: int | None,
    since: datetime | None,
    by_cost: bool,
    page: int,
    page_size: int,
) -> tuple[list[Run], int, int, dict[str, int]]:
    """One page of matching runs, with usage.

    Returns the runs, how many match, the page actually shown (the last one
    when `page` is past it), and counts per status before the status filter.
    """
    runs = workspace_runs(workspace_id)
    if job_id is not None:
        runs = runs.filter(job_id=job_id)
    if repository_id is not None:
        runs = runs.filter(task__repository_id=repository_id)
    if since is not None:
        runs = runs.filter(created_at__gte=since)
    runs = search_runs(runs, q)
    # Counts ignore the status filter, so each status chip shows what it would add.
    status_counts = {
        row["status"]: row["total"]
        for row in runs.order_by().values("status").annotate(total=Count("pk"))
    }
    if statuses:
        runs = runs.filter(status__in=statuses)
    count = runs.count()
    last_page = max((count + page_size - 1) // page_size, 1)
    page = min(page, last_page)
    start = (page - 1) * page_size
    page_slice = slice(start, start + page_size)
    related = ("job", "task__repository")
    if by_cost:
        # Cost comes from usage, so aggregate every run before taking the page.
        ordered = with_model_usage(runs.select_related(*related)).order_by(
            F("model_cost_usd").desc(nulls_last=True), *NEWEST_FIRST
        )[page_slice]
    else:
        # Take the page's run IDs first, then aggregate usage for those runs.
        page_ids = runs.order_by(*NEWEST_FIRST).values("pk")[page_slice]
        page_runs = Run.objects.filter(pk__in=Subquery(page_ids))
        ordered = with_model_usage(page_runs.select_related(*related)).order_by(
            *NEWEST_FIRST
        )
    return list(ordered), count, page, status_counts


# Tasks


def create_task(**fields: Any) -> Task:
    return Task.objects.create(**fields)


def find_submitted_task(repository_id: int, submission_key: str) -> Task | None:
    """The repository's task submitted with this idempotency key, if any."""
    return Task.objects.filter(
        repository_id=repository_id, submission_key=submission_key
    ).first()


def first_attempt(task: Task) -> Run | None:
    return task.runs.order_by("created_at", "pk").first()


# Run events


def add_event(
    run_id: int,
    message: str,
    *,
    level: str = "info",
    metadata: dict[str, Any] | None = None,
) -> RunEvent:
    """Add a line to the run's timeline."""
    return RunEvent.objects.create(
        run_id=run_id, message=message, level=level, metadata=metadata or {}
    )


def add_event_once(run_id: int, message: str, level: str) -> None:
    """Add a line to the run's timeline unless it is already there."""
    RunEvent.objects.get_or_create(
        run_id=run_id, message=message, defaults={"level": level}
    )


def run_events(run: Run) -> QuerySet[RunEvent]:
    """The run's timeline, oldest first."""
    return run.events.order_by("created_at", "pk")


def subagent_start(run_id: int, marker: str, call_id: str) -> RunEvent | None:
    """The start already recorded for this sub-agent call, if any."""
    return RunEvent.objects.filter(
        run_id=run_id, metadata__subagent=marker, metadata__call_id=call_id
    ).first()


def subagent_event_count(run_id: int, marker: str) -> int:
    return RunEvent.objects.filter(run_id=run_id, metadata__subagent=marker).count()


def has_subagent_event(run_id: int, marker: str, call_id: str) -> bool:
    return RunEvent.objects.filter(
        run_id=run_id, metadata__subagent=marker, metadata__call_id=call_id
    ).exists()


# Deliveries


def create_delivery(run: Run, kind: str, target: str) -> RunDelivery:
    return RunDelivery.objects.create(run=run, kind=kind, target=target)


def run_delivery(run: Run, kind: str, delivered: bool = False) -> RunDelivery | None:
    """The run's delivery of this kind. With `delivered`, only a delivered one."""
    deliveries = run.deliveries.filter(kind=kind)
    if delivered:
        deliveries = deliveries.filter(status=RunDelivery.Status.DELIVERED)
    return deliveries.first()


def lock_delivery(run: Run, delivery_id: int) -> RunDelivery:
    """Lock the run's delivery, with its connection. Raises when missing."""
    return (
        RunDelivery.objects.select_for_update()
        .select_related("connection")
        .get(pk=delivery_id, run=run)
    )


def notification_delivery(run: Run, connection: Connection) -> RunDelivery:
    """The run's notification delivery to this connection, created when missing."""
    delivery, _ = RunDelivery.objects.get_or_create(
        run=run,
        kind=RunDelivery.Kind.NOTIFICATION,
        connection=connection,
        defaults={"target": connection.binding_key or connection.display_name},
    )
    return delivery


def fail_notification_delivery(run_id: int, connection_id: int, now: datetime) -> None:
    """Mark the notification failed, unless it was delivered."""
    RunDelivery.objects.filter(
        run_id=run_id,
        kind=RunDelivery.Kind.NOTIFICATION,
        connection_id=connection_id,
    ).exclude(status=RunDelivery.Status.DELIVERED).update(
        status=RunDelivery.Status.FAILED, updated_at=now
    )


# Issues


def has_proposed_issues(run: Run) -> bool:
    return run.issues.exists()


def unopened_issues(run: Run) -> QuerySet[RunIssue]:
    return run.issues.filter(number__isnull=True)


def opened_issues(run: Run) -> list[RunIssue]:
    return list(run.issues.exclude(number__isnull=True))


def proposed_issue(run_id: int, call_id: str) -> bool:
    """Whether this tool call already proposed an issue."""
    return RunIssue.objects.filter(run_id=run_id, call_id=call_id).exists()


def proposed_issue_titles(run_id: int) -> list[str]:
    return list(RunIssue.objects.filter(run_id=run_id).values_list("title", flat=True))


def proposed_issue_count(run_id: int) -> int:
    return RunIssue.objects.filter(run_id=run_id).count()


def create_issue_proposal(run_id: int, call_id: str, title: str, body: str) -> RunIssue:
    return RunIssue.objects.create(
        run_id=run_id, call_id=call_id, title=title, body=body
    )


# Agent leases


def run_has_lease(run: Run) -> bool:
    return AgentLease.objects.filter(run=run).exists()


def create_lease(run: Run, token_hash: str, session_id: str) -> AgentLease:
    return AgentLease.objects.create(
        run=run, token_hash=token_hash, session_id=session_id
    )


def end_lease(lease_id: int) -> None:
    AgentLease.objects.filter(pk=lease_id).update(active=False)


def lock_lease(lease_id: int) -> AgentLease:
    return AgentLease.objects.select_for_update().get(pk=lease_id)


def active_lease(token_hash: str) -> AgentLease | None:
    return AgentLease.objects.filter(token_hash=token_hash, active=True).first()


def with_last_run(jobs: QuerySet[Job]) -> QuerySet[Job]:
    """Annotate each job with its newest run, so a list needs no query per job."""
    newest = Run.objects.filter(job=OuterRef("pk")).order_by("-created_at", "-pk")
    return jobs.annotate(
        last_run_id=Subquery(newest.values("pk")[:1]),
        last_run_status=Subquery(newest.values("status")[:1]),
        last_run_at=Subquery(
            newest.annotate(at=Coalesce("finished_at", "created_at")).values("at")[:1]
        ),
    )


def job_with_last_run(job_id: int) -> Job:
    return with_last_run(Job.objects.filter(pk=job_id)).get()


def jobs_whose_last_run_failed(workspace_id: int) -> list[Job]:
    """Active jobs whose newest finished run failed, by name.

    Each job gets `latest_run_id`.
    """
    latest = Run.objects.filter(
        job=OuterRef("pk"), status__in=Run.FINISHED_STATUSES
    ).order_by("-created_at", "-pk")
    return list(
        active_jobs(workspace_id)
        .annotate(
            latest_run_id=Subquery(latest.values("pk")[:1]),
            latest_status=Subquery(latest.values("status")[:1]),
        )
        .filter(latest_status=Run.Status.FAILED)
        .order_by("name")
    )
