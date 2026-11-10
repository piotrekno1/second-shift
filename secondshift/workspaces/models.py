from django.conf import settings
from django.db import models


class RunLimitOverrides(models.Model):
    """Run limits an owner chose. Empty means the next level up decides.

    A job falls back to its workspace, and a workspace to the server defaults.
    The server maximums cap every value (see secondshift.workspaces.limits).
    """

    max_turns = models.PositiveIntegerField(null=True, blank=True)
    max_tool_calls = models.PositiveIntegerField(null=True, blank=True)
    token_budget = models.PositiveBigIntegerField(null=True, blank=True)
    timeout_seconds = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        abstract = True


class Workspace(RunLimitOverrides):
    """Who belongs and with which role is kept in WorkspaceMembership only."""

    # What runs the jobs: one model key or one subscription login, never both.
    # Neither set means nothing is chosen and runs cannot start.
    default_model_connection = models.ForeignKey(
        "connections.Connection",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={"kind": "model"},
        related_name="default_workspaces",
    )
    default_agent_connection = models.ForeignKey(
        "connections.AgentConnection",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="default_workspaces",
    )
    name = models.CharField(max_length=160)
    slug = models.SlugField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(RunLimitOverrides.Meta):
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(default_model_connection__isnull=True)
                | models.Q(default_agent_connection__isnull=True),
                name="workspace_one_execution_default",
            )
        ]

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


class WorkspaceInvite(models.Model):
    """A one-time link that lets someone join a workspace with a role.

    Only a hash of the link's token is stored, so the database alone cannot
    be used to join. See secondshift.workspaces.invites.
    """

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="invites",
    )
    role = models.CharField(
        max_length=16,
        choices=WorkspaceMembership.Role.choices,
        default=WorkspaceMembership.Role.MEMBER,
    )
    # Who the link is for, so owners can tell pending invites apart.
    note = models.CharField(max_length=160, blank=True)
    token_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.get_role_display()} invite to {self.workspace}"


class ApiToken(models.Model):
    """A bearer token for the MCP server, for one person in one workspace.

    Only a hash of the token is stored. See secondshift.workspaces.tokens.
    """

    class Scope(models.TextChoices):
        READ = "read", "Read"
        RUN = "run", "Read and run"

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="api_tokens",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_tokens",
    )
    name = models.CharField(max_length=160)
    scope = models.CharField(max_length=16, choices=Scope.choices, default=Scope.READ)
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_scope_display()})"
