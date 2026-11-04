import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from secondshift.config.constants import RunStart, TaskOrigin
from secondshift.connections.models import Connection
from secondshift.jobs.models import Job
from secondshift.repositories.models import Repository


class Task(models.Model):
    """One assignment; runs retain the inputs and policy of each attempt.

    Job-backed assignments retain their existing source relationship. Ad hoc
    assignments own a repository directly. Inputs are frozen at submission.
    """

    job = models.ForeignKey(
        Job, on_delete=models.CASCADE, related_name="tasks", null=True, blank=True
    )
    repository = models.ForeignKey(
        Repository,
        on_delete=models.PROTECT,
        related_name="tasks",
        null=True,
        blank=True,
    )
    submission_key = models.CharField(
        max_length=120, blank=True, default="", db_default=""
    )
    origin = models.CharField(
        max_length=16,
        choices=[(item.value, item.value.title()) for item in TaskOrigin],
        blank=True,
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="secondshift_tasks",
        null=True,
        blank=True,
    )
    commit_sha = models.CharField(max_length=64)
    input_snapshot = models.JSONField(default=dict, blank=True)
    scheduled_for = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(job__isnull=False, repository__isnull=True)
                    | models.Q(job__isnull=True, repository__isnull=False)
                ),
                name="task_has_one_source",
            ),
            models.UniqueConstraint(
                fields=["repository", "submission_key"],
                condition=~models.Q(submission_key=""),
                name="unique_repository_submission",
            ),
        ]


def run_workspace_filter(workspace_id: int | None, prefix: str = "") -> models.Q:
    """Include ad hoc runs without changing the ownership of legacy job runs."""
    if workspace_id is None:
        return models.Q(**{f"{prefix}pk__in": []})
    return models.Q(**{f"{prefix}job__workspace_id": workspace_id}) | models.Q(
        **{
            f"{prefix}job__isnull": True,
            f"{prefix}task__repository__workspace_id": workspace_id,
        }
    )


class Run(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    # A run in one of these has not finished yet.
    ACTIVE_STATUSES = (Status.QUEUED, Status.RUNNING)

    job = models.ForeignKey(
        Job, on_delete=models.PROTECT, related_name="runs", null=True, blank=True
    )
    task = models.ForeignKey(
        Task,
        on_delete=models.PROTECT,
        related_name="runs",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    commit_sha = models.CharField(max_length=64)
    scheduled_for = models.DateTimeField()
    # Blank for runs created before this was recorded.
    started_by = models.CharField(
        max_length=16,
        choices=[(item.value, item.value.title()) for item in RunStart],
        blank=True,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    sandbox_runtime = models.CharField(max_length=32, blank=True)
    exit_code = models.IntegerField(null=True, blank=True)
    summary = models.TextField(blank=True)
    # What the run was asked to do, frozen at creation (see job_input_snapshot).
    input_snapshot = models.JSONField(default=dict, blank=True)
    # Immutable, redacted record of bindings, tools and policy decisions.
    execution_plan = models.JSONField(default=dict, blank=True)
    # The full, redacted output is in run_artifacts storage at output_path;
    # only its metadata and the tail are kept here.
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
            models.CheckConstraint(
                condition=models.Q(job__isnull=False) | models.Q(task__isnull=False),
                name="run_has_source",
            ),
            models.UniqueConstraint(
                fields=["job", "scheduled_for"],
                name="unique_job_scheduled_run",
            ),
        ]

    @property
    def repository(self) -> Repository:
        if self.job is not None:
            return self.job.repository
        if self.task_id is not None and self.task.repository_id is not None:
            return self.task.repository
        raise ValueError("The run has no repository source.")

    @property
    def workspace_id(self) -> int:
        return (
            self.job.workspace_id
            if self.job is not None
            else self.repository.workspace_id
        )

    @property
    def display_name(self) -> str:
        return (
            self.job.name
            if self.job is not None
            else str(self.input_snapshot.get("name", "Ad hoc skill"))
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
        PENDING = "pending", "Pending"
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


class AgentLease(models.Model):
    """One active agent process, with a revocable run-scoped broker token."""

    run = models.OneToOneField(Run, on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    session_id = models.CharField(max_length=100)
    active = models.BooleanField(default=True)
    tool_calls = models.PositiveIntegerField(default=0)
