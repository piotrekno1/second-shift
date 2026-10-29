from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from encrypted_model_fields.fields import EncryptedTextField

from secondshift.workspaces.models import Workspace


class Connection(models.Model):
    class Provider(models.TextChoices):
        GITHUB = "github", "GitHub"
        SENTRY = "sentry", "Sentry"
        CHATGPT = "chatgpt", "ChatGPT"
        SLACK = "slack", "Slack"
        MCP = "mcp", "MCP server"
        API = "api", "HTTP API"
        WEBHOOK = "webhook", "Webhook"
        ANTHROPIC = "anthropic", "Anthropic"
        OPENAI = "openai", "OpenAI"
        OPENROUTER = "openrouter", "OpenRouter"
        OPENAI_COMPATIBLE = "openai_compatible", "OpenAI-compatible"

    class Kind(models.TextChoices):
        IDENTITY = "identity", "Identity"
        SOURCE = "source", "Repository source"
        MCP = "mcp", "MCP"
        API = "api", "API"
        NOTIFICATION = "notification", "Notification"
        MODEL = "model", "Model provider"

    class AuthStrategy(models.TextChoices):
        OAUTH = "oauth", "OAuth"
        SECRET_REFERENCE = "secret_reference", "Secret reference"
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
    kind = models.CharField(
        max_length=24,
        choices=Kind.choices,
        default=Kind.IDENTITY,
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
    is_default = models.BooleanField(
        default=False,
        help_text="The workspace's default model provider for runs.",
    )
    auth_strategy = models.CharField(
        max_length=24,
        choices=AuthStrategy.choices,
        default=AuthStrategy.OAUTH,
    )
    credential_reference = models.CharField(
        max_length=255,
        blank=True,
        help_text="Opaque Vault/KMS/provider reference. Never a credential value.",
    )
    # Webhook URLs usually embed their own token, so they are stored encrypted
    # and only a redacted target label is ever returned.
    delivery_url = EncryptedTextField(blank=True, default="")
    access_token = EncryptedTextField(blank=True, default="")
    refresh_token = EncryptedTextField(blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
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
                fields=["workspace"],
                condition=models.Q(is_default=True),
                name="unique_workspace_default_model",
            ),
        ]
        ordering = ["provider", "display_name"]

    def __str__(self) -> str:
        return f"{self.get_provider_display()}: {self.display_name}"

    def binding_keys(self) -> list[str]:
        """The binding names this connection serves. Uses prefetched rows."""
        return sorted(binding.key for binding in self.bindings.all())

    def serves(self, binding: str) -> bool:
        return binding in self.binding_keys()

    def clean(self) -> None:
        if self.is_default and self.kind != self.Kind.MODEL:
            raise ValidationError(
                {"is_default": "Only model providers can be the workspace default."}
            )
        if (
            self.auth_strategy == self.AuthStrategy.SECRET_REFERENCE
            and not self.credential_reference
        ):
            raise ValidationError(
                {
                    "credential_reference": (
                        "A credential reference is required for this auth strategy."
                    )
                }
            )


class ConnectionBinding(models.Model):
    """A binding name that jobs ask for, and the connection it points to.

    Each name points to one connection in a workspace. One connection can
    serve several names, so a key or URL is changed in one place.
    """

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="connection_bindings",
    )
    connection = models.ForeignKey(
        Connection,
        on_delete=models.CASCADE,
        related_name="bindings",
    )
    key = models.SlugField(
        max_length=120,
        help_text="The name used in .secondshift.yaml, for example engineering-reports.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "key"],
                name="unique_workspace_binding",
            ),
        ]
        ordering = ["key"]

    def __str__(self) -> str:
        return self.key

    def clean(self) -> None:
        if self.connection.workspace_id != self.workspace_id:
            raise ValidationError(
                "Binding and connection must belong to one workspace."
            )


class Secret(models.Model):
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="secrets",
    )
    name = models.CharField(max_length=128)
    description = models.CharField(max_length=255, blank=True)
    value = EncryptedTextField()
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"],
                name="unique_workspace_secret_name",
            )
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


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
    # Issuer, token endpoint, client ID, and resource. None of these are secret.
    oauth = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"OAuth sign-in for {self.binding_key or self.provider}"
