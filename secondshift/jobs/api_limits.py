from django.http import HttpRequest

from secondshift.jobs.limits import LimitError, LimitView
from secondshift.jobs.schemas import LimitSchema, RunLimitsUpdateSchema
from secondshift.workspaces.access import (
    is_operator,
)


def serialize_limits(views: list[LimitView]) -> list[LimitSchema]:
    return [LimitSchema(**vars(view)) for view in views]


def limit_error_detail(request: HttpRequest, error: LimitError) -> str:
    """Only people who run the server can raise a maximum, so only they get the hint."""
    if error.ceiling_env and is_operator(request):
        return f"{error} Set {error.ceiling_env} to allow more."
    return str(error)


def limit_updates(payload: RunLimitsUpdateSchema) -> dict[str, int | None]:
    return payload.model_dump(exclude_unset=True)
