import secrets

from django.db import models
from encrypted_model_fields.fields import EncryptedTextField

from secondshift.config.constants import TriggerKind
from secondshift.jobs.models import Job
from secondshift.runs.models import Run


def new_public_id() -> str:
    return secrets.token_urlsafe(24)


class JobTrigger(models.Model):
    """One trigger kind for one job, as an owner or admin set it up.

    The repository only names the kind. The URL, the secret and whether it is
    on live here and are changed only in the dashboard.
    """

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="job_triggers")
    kind = models.CharField(
        max_length=16,
        choices=[(item.value, item.value.title()) for item in TriggerKind],
    )
    # The last part of the trigger's URL. Random, so it reveals nothing.
    public_id = models.CharField(max_length=64, unique=True, default=new_public_id)
    # Encrypted, not hashed: checking an HMAC signature needs the secret itself.
    secret = EncryptedTextField()
    enabled = models.BooleanField(default=False)
    # The owner's cap on runs this trigger starts per hour. Empty uses the
    # server default.
    max_runs_per_hour = models.PositiveIntegerField(null=True, blank=True)
    secret_rotated_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job", "kind"], name="unique_job_trigger_kind"
            )
        ]

    def __str__(self) -> str:
        return f"{self.job}: {self.kind}"


class TriggerEvent(models.Model):
    """One request a trigger received, and what SecondShift did with it."""

    class Outcome(models.TextChoices):
        STARTED = "started", "Started a run"
        # Not a run, by the job's own settings: a skipped or repeated event.
        IGNORED = "ignored", "Ignored"
        REFUSED = "refused", "Refused"

    trigger = models.ForeignKey(
        JobTrigger, on_delete=models.CASCADE, related_name="events"
    )
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    # Why it was ignored or refused, in words for the person who set it up.
    reason = models.CharField(max_length=500, blank=True)
    run = models.ForeignKey(
        Run,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trigger_events",
    )
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at", "-pk"]
        indexes = [
            models.Index(
                fields=["trigger", "received_at"], name="trigger_event_received_idx"
            )
        ]

    def __str__(self) -> str:
        return f"{self.trigger} at {self.received_at:%Y-%m-%d %H:%M}: {self.outcome}"
