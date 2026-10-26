import uuid

from django.db import models

from secondshift.jobs.models import Job


class Run(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    job = models.ForeignKey(Job, on_delete=models.PROTECT, related_name="runs")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    commit_sha = models.CharField(max_length=64)
    scheduled_for = models.DateTimeField()
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
            )
        ]

    def __str__(self) -> str:
        return f"{self.job} at {self.scheduled_for:%Y-%m-%d %H:%M}"


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
