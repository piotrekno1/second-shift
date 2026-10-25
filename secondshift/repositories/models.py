from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import models

from secondshift.integrations.models import Connection
from secondshift.workspaces.models import Workspace


class Repository(models.Model):
    class SourceType(models.TextChoices):
        GITHUB = "github", "GitHub"
        LOCAL = "local", "Local"

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="repositories",
    )
    source_type = models.CharField(
        max_length=16,
        choices=SourceType.choices,
        default=SourceType.GITHUB,
    )
    local_path = models.CharField(max_length=1000, blank=True)
    github_connection = models.ForeignKey(
        Connection,
        on_delete=models.PROTECT,
        related_name="repositories",
        null=True,
        blank=True,
    )
    external_id = models.CharField(max_length=64, blank=True)
    full_name = models.CharField(max_length=255)
    clone_url = models.URLField(blank=True)
    default_branch = models.CharField(max_length=255, default="main")
    installation_id = models.CharField(max_length=64, blank=True)
    enabled = models.BooleanField(default=False)
    config_path = models.CharField(max_length=255, default=".secondshift.yaml")
    config_sha = models.CharField(max_length=64, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "full_name"],
                name="unique_workspace_repository",
            ),
            models.UniqueConstraint(
                fields=["workspace", "local_path"],
                condition=~models.Q(local_path=""),
                name="unique_workspace_local_repository",
            ),
        ]
        ordering = ["full_name"]
        verbose_name_plural = "repositories"

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        if self.source_type == self.SourceType.LOCAL and self.local_path:
            return Path(self.local_path).name
        return self.full_name

    def clean(self) -> None:
        if self.source_type == self.SourceType.LOCAL and not self.local_path:
            raise ValidationError(
                {"local_path": "Local repositories require a filesystem path."}
            )
        if self.source_type != self.SourceType.LOCAL and self.local_path:
            raise ValidationError(
                {"local_path": "Only local repositories may have a local path."}
            )
