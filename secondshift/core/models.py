from django.conf import settings
from django.db import models


class RunLimitOverrides(models.Model):
    """Run limits an owner chose. Empty means the next level up decides.

    A job falls back to its workspace, and a workspace to the server defaults.
    The server maximums cap every value (see secondshift.automations.limits).
    """

    max_turns = models.PositiveIntegerField(null=True, blank=True)
    max_tool_calls = models.PositiveIntegerField(null=True, blank=True)
    token_budget = models.PositiveBigIntegerField(null=True, blank=True)
    timeout_seconds = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        abstract = True


class Workspace(RunLimitOverrides):
    name = models.CharField(max_length=160)
    slug = models.SlugField(unique=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_workspaces",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(RunLimitOverrides.Meta):
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class WorkspaceMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workspace_memberships",
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.MEMBER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "user"],
                name="unique_workspace_membership",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} in {self.workspace}"
