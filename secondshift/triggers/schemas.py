from datetime import datetime

from ninja import Schema
from pydantic import Field

from secondshift.config.constants import TriggerKind


class TriggerEventSchema(Schema):
    id: int
    outcome: str
    reason: str
    run_id: int | None
    received_at: datetime
    # Back-to-back requests with the same outcome and reason share one row.
    # received_at is the newest of them.
    count: int = 1


class TriggerSchema(Schema):
    kind: TriggerKind
    # The repository's configuration names this trigger.
    declared: bool
    # Someone turned it on at least once, so it has a URL and a secret.
    set_up: bool
    enabled: bool
    url: str | None
    secret_rotated_at: datetime | None
    events: list[TriggerEventSchema]
    # The cap that applies, the owner's own value if set, and the server's.
    runs_per_hour: int
    custom_runs_per_hour: int | None
    default_runs_per_hour: int
    max_runs_per_hour: int
    # From the repository's webhook settings. repeat_window includes the default.
    skip_when: dict[str, str | int | float | bool]
    repeat_key: str | None
    repeat_window: str | None


class TriggerChangeSchema(Schema):
    trigger: TriggerSchema
    # Only right after it was created or rotated. Never returned again.
    secret: str | None = None


class TriggerEnabledSchema(Schema):
    enabled: bool


class TriggerLimitSchema(Schema):
    # Empty goes back to the server default.
    runs_per_hour: int | None = Field(default=None, ge=1)


class TriggerReceivedSchema(Schema):
    detail: str
    run_id: int | None = None
