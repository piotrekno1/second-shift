from django.core.exceptions import ValidationError
from django.db import models

from secondshift.connections.models import Connection
from secondshift.core.models import RunLimitOverrides
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace


class Job(RunLimitOverrides):
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="jobs",
    )
    repository = models.ForeignKey(
        Repository,
        on_delete=models.CASCADE,
        related_name="jobs",
    )
    key = models.SlugField(max_length=120)
    name = models.CharField(max_length=200)
    skill_path = models.CharField(max_length=500)
    # The skill file at the synced commit, for people to read before a run.
    # Runs read the file from the commit, never from here.
    skill_text = models.TextField(blank=True, default="")
    skill_truncated = models.BooleanField(default=False)
    # Empty for a job that runs only when started manually.
    cron = models.CharField(max_length=100, blank=True, default="")
    timezone = models.CharField(max_length=64, default="UTC")
    # How many of its own earlier reports each run starts with.
    history = models.PositiveSmallIntegerField(default=0)
    # Trigger kinds the repository names, such as "webhook". See JobTrigger.
    triggers = models.JSONField(default=list, blank=True)
    # The repository's webhook settings: skip_when, repeat_key, repeat_window.
    webhook = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=False)
    next_run_at = models.DateTimeField(null=True, blank=True)
    config_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(RunLimitOverrides.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["repository", "key"],
                name="unique_repository_job_key",
            )
        ]
        ordering = ["repository__full_name", "name"]

    def __str__(self) -> str:
        return f"{self.repository}: {self.name}"

    def capability_grant_status(self) -> str:
        """Summarize the required grants already loaded for this job."""
        requests = [
            request for request in self.capability_requests.all() if request.required
        ]
        statuses = [
            getattr(request, "grant", None).status
            if hasattr(request, "grant")
            else JobGrant.Status.PENDING
            for request in requests
        ]
        if JobGrant.Status.DENIED in statuses:
            return JobGrant.Status.DENIED
        if statuses and all(status == JobGrant.Status.APPROVED for status in statuses):
            return JobGrant.Status.APPROVED
        return JobGrant.Status.PENDING if requests else "not_required"


class CapabilityRequest(models.Model):
    class Kind(models.TextChoices):
        MCP = "mcp", "MCP"
        API = "api", "API"
        NOTIFICATION = "notification", "Notification"

    job = models.ForeignKey(
        Job,
        on_delete=models.CASCADE,
        related_name="capability_requests",
    )
    binding = models.SlugField(max_length=120)
    kind = models.CharField(max_length=24, choices=Kind.choices)
    requested_tools = models.JSONField(default=list, blank=True)
    required = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job", "binding"],
                name="unique_job_capability_binding",
            )
        ]
        ordering = ["job", "binding"]

    def __str__(self) -> str:
        return f"{self.job}: {self.binding}"


class JobGrant(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        DENIED = "denied", "Denied"

    capability_request = models.OneToOneField(
        CapabilityRequest,
        on_delete=models.CASCADE,
        related_name="grant",
    )
    connection = models.ForeignKey(
        Connection,
        on_delete=models.PROTECT,
        related_name="job_grants",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    allowed_tools = models.JSONField(default=list, blank=True)
    # The owner's argument rules per approved tool. See jobs.tool_rules.
    tool_rules = models.JSONField(default=dict, blank=True)
    reviewed_by = models.ForeignKey(
        "auth.User",
        on_delete=models.PROTECT,
        related_name="reviewed_job_grants",
        null=True,
        blank=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["capability_request__job", "capability_request__binding"]

    def __str__(self) -> str:
        return f"{self.capability_request}: {self.get_status_display()}"

    def clean(self) -> None:
        request = self.capability_request
        connection = self.connection
        if self.status == self.Status.APPROVED and connection is None:
            raise ValidationError("Approved grants require a connection.")
        if connection is None:
            return
        if request.job.workspace_id != connection.workspace_id:
            raise ValidationError("Grant and connection must belong to one workspace.")
        if request.kind != connection.kind:
            raise ValidationError(
                "Connection kind must match the requested capability."
            )
        if request.binding != connection.binding_key:
            raise ValidationError(
                "Connection binding must match the requested capability."
            )
        unknown_tools = set(self.allowed_tools) - set(request.requested_tools)
        if unknown_tools:
            raise ValidationError(
                {"allowed_tools": "Allowed tools must be requested by the job."}
            )
        if set(self.tool_rules) - set(self.allowed_tools):
            raise ValidationError(
                {"tool_rules": "Rules can only apply to approved tools."}
            )
