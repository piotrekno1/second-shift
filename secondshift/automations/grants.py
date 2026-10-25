"""An owner's decision on the access a job requests."""

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.utils import timezone

from secondshift.automations.models import CapabilityRequest, JobGrant
from secondshift.integrations.external import external_access
from secondshift.integrations.models import Connection


class GrantError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        # The HTTP status the API answers with.
        self.status = status


def check_connection(
    capability: CapabilityRequest, connection: Connection, approving: bool
) -> None:
    """The connection must fit the request, be connected, and reach the repository."""
    if (
        connection.kind != capability.kind
        or connection.binding_key != capability.binding
    ):
        raise GrantError(422, "Connection kind and binding must match capability.")
    if connection.status != Connection.Status.CONNECTED:
        raise GrantError(409, "The selected connection is not connected.")
    if not approving:
        return
    access = external_access(connection, capability.job.repository)
    if access and not access.ok:
        raise GrantError(409, access.text)


def granted_tools(
    capability: CapabilityRequest,
    decision: str,
    connection: Connection | None,
    chosen: list[str] | None,
) -> set[str]:
    """The tools the decision allows. Approving means every requested tool."""
    requested = set(capability.requested_tools)
    allowed = set(capability.requested_tools if chosen is None else chosen)
    if decision == JobGrant.Status.DENIED:
        return set()
    if allowed - requested:
        raise GrantError(422, "Allowed tools must be requested by the job.")
    if decision == JobGrant.Status.APPROVED:
        if allowed != requested:
            raise GrantError(
                422, "Approve every requested tool or deny the capability."
            )
        if connection and requested - set(connection.available_tools):
            raise GrantError(409, "The connection lacks a requested tool.")
    return allowed


@transaction.atomic
def decide_grant(
    capability: CapabilityRequest,
    decision: str,
    connection: Connection | None,
    chosen_tools: list[str] | None,
    reviewer: AbstractBaseUser,
) -> JobGrant:
    approving = decision == JobGrant.Status.APPROVED
    if connection is not None:
        check_connection(capability, connection, approving)
    elif approving:
        raise GrantError(422, "Choose a connection before approving the capability.")
    allowed = granted_tools(capability, decision, connection, chosen_tools)

    grant = JobGrant.objects.filter(capability_request=capability).first() or JobGrant(
        capability_request=capability
    )
    grant.connection = connection
    grant.status = decision
    grant.allowed_tools = sorted(allowed)
    grant.reviewed_by = reviewer
    grant.reviewed_at = timezone.now()
    grant.full_clean()
    grant.save()
    return grant
