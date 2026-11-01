from datetime import datetime

from ninja import Schema

from secondshift.config.constants import TriggerKind


class TriggerEventSchema(Schema):
    id: int
    outcome: str
    reason: str
    run_id: int | None
    received_at: datetime


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


class TriggerChangeSchema(Schema):
    trigger: TriggerSchema
    # Only right after it was created or rotated. Never returned again.
    secret: str | None = None


class TriggerEnabledSchema(Schema):
    enabled: bool


class TriggerReceivedSchema(Schema):
    detail: str
    run_id: int | None = None
