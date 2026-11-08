import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from encrypted_model_fields.fields import EncryptedTextField

from secondshift.workspaces.models import Workspace


class Connection(models.Model):
    class Provider(models.TextChoices):
        GITHUB = "github", "GitHub"
        SENTRY = "sentry", "Sentry"
        MCP = "mcp", "MCP server"
        API = "api", "HTTP API"
        WEBHOOK = "webhook", "Webhook"
        ANTHROPIC = "anthropic", "Anthropic"
        OPENAI = "openai", "OpenAI"
        OPENROUTER = "openrouter", "OpenRouter"
        OPENAI_COMPATIBLE = "openai_compatible", "OpenAI-compatible"

    class Kind(models.TextChoices):
        SOURCE = "source", "Repository source"
        MCP = "mcp", "MCP"
        API = "api", "API"
        NOTIFICATION = "notification", "Notification"
        MODEL = "model", "Model provider"

    class AuthStrategy(models.TextChoices):
        OAUTH = "oauth", "OAuth"
        API_KEY = "api_key", "Encrypted API key"
        MANAGED = "managed", "Managed by service"
        NONE = "none", "None"

    class Status(models.TextChoices):
        CONNECTED = "connected", "Connected"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="connections",
    )
    provider = models.CharField(max_length=24, choices=Provider.choices)
    kind = models.CharField(max_length=24, choices=Kind.choices)
    binding_key = models.SlugField(
        max_length=120,
        blank=True,
        help_text="Logical name used by repository manifests, for example observability.",
    )
    display_name = models.CharField(max_length=200)
    endpoint_url = models.URLField(blank=True)
    external_account_id = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.CONNECTED,
    )
    scopes = models.JSONField(default=list, blank=True)
    available_tools = models.JSONField(default=list, blank=True)
    # Model providers only. Runs may call just these models; empty allows any.
    allowed_models = models.JSONField(default=list, blank=True)
    default_model = models.CharField(max_length=200, blank=True)
    auth_strategy = models.CharField(
        max_length=24,
        choices=AuthStrategy.choices,
        default=AuthStrategy.OAUTH,
    )
    # Webhook URLs usually embed their own token, so they are stored encrypted
    # and only a redacted target label is ever returned.
    delivery_url = EncryptedTextField(blank=True, default="")
    access_token = EncryptedTextField(blank=True, default="")
    refresh_token = EncryptedTextField(blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    # Capability connections only. When off, a job that asks only for allowed
    # tools is approved without an owner, so merging the manifest is the review.
    require_approval = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "provider", "external_account_id"],
                condition=~models.Q(external_account_id=""),
                name="unique_workspace_provider_account",
            ),
            models.UniqueConstraint(
                fields=["workspace", "binding_key"],
                condition=~models.Q(binding_key=""),
                name="unique_workspace_connection_binding",
            ),
        ]
        ordering = ["provider", "display_name"]

    def __str__(self) -> str:
        binding = f" [{self.binding_key}]" if self.binding_key else ""
        return f"{self.get_provider_display()}: {self.display_name}{binding}"

    @property
    def token_expired(self) -> bool:
        """The saved token is past its expiry. It is never sent."""
        return bool(self.expires_at and self.expires_at <= timezone.now())

    @property
    def current_status(self) -> str:
        """The saved status, or expired once a token that cannot refresh runs out.

        An OAuth token past its expiry still counts as connected. It refreshes
        when used, and a refused refresh saves the expired status.
        """
        if (
            self.status == self.Status.CONNECTED
            and self.auth_strategy != self.AuthStrategy.OAUTH
            and self.token_expired
        ):
            return self.Status.EXPIRED
        return self.status

    def clean(self) -> None:
        # Repositories never name model providers, so they need no binding.
        if self.kind != self.Kind.MODEL and not self.binding_key:
            raise ValidationError(
                {"binding_key": "Capability connections require a logical binding."}
            )


class OAuthAuthorization(models.Model):
    """One browser sign-in in progress. Single use, and short-lived."""

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="oauth_authorizations",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="oauth_authorizations",
    )
    # Set when reconnecting an existing connection.
    connection = models.ForeignKey(
        Connection,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="oauth_authorizations",
    )
    state = models.CharField(max_length=64, unique=True)
    code_verifier = EncryptedTextField()
    provider = models.CharField(max_length=24, choices=Connection.Provider.choices)
    binding_key = models.SlugField(max_length=120, blank=True)
    display_name = models.CharField(max_length=200, blank=True)
    connection_settings = models.JSONField(default=dict, blank=True)
    scopes = models.JSONField(default=list, blank=True)
    # The new connection's setting. A reconnect keeps the connection's own.
    require_approval = models.BooleanField(default=False)
    # Issuer, token endpoint, client ID, and resource. None of these are secret.
    oauth = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"OAuth sign-in for {self.binding_key or self.provider}"


class AgentConnection(models.Model):
    """A person's agent login. Authentication is never part of a run plan."""

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    backend = models.CharField(max_length=24)
    display_name = models.CharField(max_length=200)
    auth_data = EncryptedTextField()
    credential_key = models.UUIDField(default=uuid.uuid4, editable=False)
    connected = models.BooleanField(default=True)
    # Empty means no cap. Runs past it in a rolling 24 hours are refused.
    max_runs_per_day = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)]
    )
    # The last login check: ok, reason, message and checked_at. Never CLI output.
    last_check = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class AgentCredentialCleanup(models.Model):
    """A runner's cached copy of a login that is still to be removed.

    The row is saved with the disconnect or reconnect that replaced the login.
    It is deleted once the runner confirms the copy is gone. The worker
    retries the rows that remain.
    """

    credential_key = models.UUIDField(unique=True)
    # Empty once the login itself is gone. The copy is still removed.
    connection = models.ForeignKey(
        AgentConnection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="credential_cleanups",
    )
    attempts = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Runner cleanup for {self.credential_key.hex}"
