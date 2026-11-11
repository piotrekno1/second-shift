import uuid
from functools import cached_property

from django.conf import settings
from django.db import models
from django.utils import timezone

from secondshift.config.constants import RunStart
from secondshift.connections.models import Connection
from secondshift.jobs.models import Job
from secondshift.jobs.plan import ExecutionPlan
from secondshift.repositories.models import Repository
from secondshift.runs.inputs import RunInput
from secondshift.runs.outputs import RunOutputs


class RunFailure(models.TextChoices):
    """Why a run failed. The label is what pages show beside the reason."""

    # The stage a workflow error stopped the run in.
    STARTING = "starting", "Start"
    CHECKOUT = "checkout", "Checkout"
    SANDBOX = "sandbox", "Sandbox"
    SKILL = "skill", "Skill"
    FINISHING = "finishing", "Recording"
    # A run limit stopped the agent.
    TIMEOUT = "timeout", "Timed out"
    BUDGET = "budget", "Budget used up"
    MODEL_PROVIDER = "model_provider", "Model provider"
    TOOL_RULE = "tool_rule", "Tool restriction"
    # The queue never took the run, so it does not count as started.
    NOT_QUEUED = "not_queued", "Run"
    OTHER = "other", "Run"


class Task(models.Model):
    """One assignment in a repository. Each run of it is an attempt.

    A job run and an ad hoc task each get one. Ad hoc tasks also keep how
    they were submitted, so a repeated submission finds its first run.
    """

    # Runs protect their task, so only a task without attempts goes with it.
    repository = models.ForeignKey(
        Repository, on_delete=models.CASCADE, related_name="tasks"
    )
    # The client's idempotency key for an ad hoc task. Empty for job runs.
    submission_key = models.CharField(max_length=120, blank=True, default="")
    # What was submitted with the key. Another submission may not reuse it.
    fingerprint = models.CharField(max_length=64, blank=True, default="")
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="secondshift_tasks",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["repository", "submission_key"],
                condition=~models.Q(submission_key=""),
                name="unique_repository_submission",
            ),
        ]


class Run(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    # A run in one of these has not finished yet.
    ACTIVE_STATUSES = (Status.QUEUED, Status.RUNNING)
    # A run in one of these is done and will not change status again.
    FINISHED_STATUSES = (Status.SUCCEEDED, Status.FAILED, Status.CANCELED)

    job = models.ForeignKey(
        Job, on_delete=models.PROTECT, related_name="runs", null=True, blank=True
    )
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="runs")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    commit_sha = models.CharField(max_length=64)
    scheduled_for = models.DateTimeField()
    started_by = models.CharField(
        max_length=16, choices=[(item.value, item.value.title()) for item in RunStart]
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    sandbox_runtime = models.CharField(max_length=32, blank=True)
    exit_code = models.IntegerField(null=True, blank=True)
    summary = models.TextField(blank=True)
    failure = models.CharField(max_length=16, choices=RunFailure.choices, blank=True)
    failure_reason = models.TextField(blank=True)
    # Inputs beyond the plan: an ad hoc task's definition and input, a
    # trigger's payload and repeat key, and the earlier runs it reads.
    input_snapshot = models.JSONField(default=dict, blank=True)
    # The frozen jobs.plan.ExecutionPlan. Read it through `plan`.
    execution_plan = models.JSONField()
    # The agent login the plan chose. Its runs per day count against its cap.
    agent_connection = models.ForeignKey(
        "connections.AgentConnection",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
    )
    # The full report is in run_artifacts storage at output_path. Only its
    # metadata and the tail are kept here.
    output_excerpt = models.TextField(blank=True)
    # The rendered input (instructions, prompt and the skill at its commit),
    # stored next to the output in run_artifacts.
    input_path = models.CharField(max_length=500, blank=True)
    input_size_bytes = models.BigIntegerField(null=True, blank=True)
    output_path = models.CharField(max_length=500, blank=True)
    output_size_bytes = models.BigIntegerField(null=True, blank=True)
    output_sha256 = models.CharField(max_length=64, blank=True)
    output_truncated = models.BooleanField(default=False)
    # What the sandbox left behind, stored in run_artifacts: the agent's file
    # changes and the files it saved. See artifacts.store_collection.
    outputs = models.JSONField(default=dict, blank=True, db_default={})
    dbos_workflow_id = models.CharField(max_length=200, blank=True, db_index=True)
    # Sent to the runner so its session can never match one from an older run
    # that had the same ID, for example after a database reset or restore.
    runner_session_key = models.UUIDField(default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["status", "created_at"], name="runs_status_created_at_idx"
            )
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "scheduled_for"],
                name="unique_job_scheduled_run",
            ),
        ]

    @property
    def repository(self) -> Repository:
        return self.task.repository

    @cached_property
    def inputs(self) -> RunInput:
        return RunInput.model_validate(self.input_snapshot)

    @cached_property
    def collected(self) -> RunOutputs | None:
        """What the sandbox left behind, or None when nothing was collected."""
        return RunOutputs.model_validate(self.outputs) if self.outputs else None

    @cached_property
    def plan(self) -> ExecutionPlan:
        return ExecutionPlan.model_validate(self.execution_plan)

    @property
    def workspace_id(self) -> int:
        return self.task.repository.workspace_id

    @property
    def job_key(self) -> str:
        """The job's key, or "" for an ad hoc task."""
        return self.plan.job.key if self.job_id is not None else ""

    @property
    def display_name(self) -> str:
        return (
            self.job.name
            if self.job is not None
            else self.inputs.name or "Ad hoc skill"
        )

    def __str__(self) -> str:
        return f"{self.display_name} at {self.scheduled_for:%Y-%m-%d %H:%M}"


class RunEvent(models.Model):
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="events")
    level = models.CharField(max_length=16, default="info")
    message = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.run_id}: {self.message[:80]}"


class RunDelivery(models.Model):
    """One place a finished run's report is sent, kept so a failure can be retried.

    A retry sends the stored report again. It never runs the agent again.
    """

    class Kind(models.TextChoices):
        NOTIFICATION = "notification", "Notification"
        PULL_REQUEST = "pull_request", "Pull request"
        ISSUES = "issues", "Issues"

    class Status(models.TextChoices):
        # Not tried yet.
        PENDING = "pending", "Pending"
        # Tried and failed, and another attempt is coming.
        RETRYING = "retrying", "Retrying"
        DELIVERED = "delivered", "Delivered"
        SKIPPED = "skipped", "Skipped"
        FAILED = "failed", "Failed"

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="deliveries")
    kind = models.CharField(max_length=24, choices=Kind.choices)
    connection = models.ForeignKey(
        Connection,
        on_delete=models.SET_NULL,
        related_name="run_deliveries",
        null=True,
        blank=True,
    )
    # The connection's name when the delivery was made, kept if it is deleted.
    target = models.CharField(max_length=200)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    attempts = models.PositiveIntegerField(default=0)
    status_code = models.IntegerField(null=True, blank=True)
    message = models.TextField(blank=True)
    # Where the delivered result lives, such as the draft pull request.
    external_url = models.URLField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def event_name(self) -> str:
        """How the run's events name this delivery."""
        if self.kind == self.Kind.NOTIFICATION:
            return f"Notification to {self.target}"
        return self.get_kind_display()

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "kind", "connection"],
                name="unique_run_delivery_target",
            ),
            models.UniqueConstraint(
                fields=["run"],
                condition=models.Q(kind="pull_request"),
                name="unique_run_pull_request",
            ),
            models.UniqueConstraint(
                fields=["run"],
                condition=models.Q(kind="issues"),
                name="unique_run_issues",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.run_id}: {self.kind} to {self.target} {self.status}"


class RunIssue(models.Model):
    """An issue the agent proposed. SecondShift opens it after the run.

    Opened issues keep their number, so a retry opens only the rest.
    """

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="issues")
    # The model's tool call. A replayed call finds its proposal instead of
    # adding it again.
    call_id = models.CharField(max_length=128)
    title = models.CharField(max_length=256)
    body = models.TextField(blank=True)
    number = models.PositiveIntegerField(null=True, blank=True)
    url = models.URLField(max_length=500, blank=True)
    # An open issue already had this title, so none was opened.
    already_open = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "call_id"], name="unique_run_issue_call"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.run_id}: {self.title}"


class RunAccessNeed(models.Model):
    """Access the agent said it needed and this run did not have.

    Only a record for people to read. It never grants anything.
    """

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="access_needs")
    # The first tool call that named the service. A replayed call finds it.
    call_id = models.CharField(max_length=128)
    # As the agent named it, such as "Linear" or "linear".
    service = models.CharField(max_length=100)
    tools = models.JSONField(default=list)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "call_id"], name="unique_run_access_need_call"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.run_id}: {self.service}"


class AgentLease(models.Model):
    """One active agent process, with a revocable run-scoped broker token."""

    run = models.OneToOneField(Run, on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    session_id = models.CharField(max_length=100)
    active = models.BooleanField(default=True)
    tool_calls = models.PositiveIntegerField(default=0)
