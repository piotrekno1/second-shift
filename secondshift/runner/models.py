from django.db import models


class RunnerSession(models.Model):
    class Status(models.TextChoices):
        STARTING = "starting", "Starting"
        ACTIVE = "active", "Active"
        STOPPED = "stopped", "Stopped"
        ERROR = "error", "Error"

    session_id = models.CharField(primary_key=True, max_length=80)
    run_id = models.PositiveBigIntegerField(db_index=True)
    repository = models.CharField(max_length=300)
    commit_sha = models.CharField(max_length=64)
    image = models.CharField(max_length=300)
    container_name = models.CharField(max_length=120, unique=True)
    workspace_path = models.TextField()
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.STARTING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class RunnerCommand(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"

    session = models.ForeignKey(
        RunnerSession, on_delete=models.CASCADE, related_name="commands"
    )
    call_id = models.CharField(max_length=200)
    fingerprint = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.RUNNING
    )
    exit_code = models.IntegerField(null=True, blank=True)
    output = models.TextField(default="")
    timed_out = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("session", "call_id"), name="runner_unique_session_call"
            )
        ]
