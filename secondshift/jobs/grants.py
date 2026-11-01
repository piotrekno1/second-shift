"""An owner's decision on the access a job requests."""

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.utils import timezone

from secondshift.connections.external import external_access
from secondshift.connections.models import Connection
from secondshift.jobs.models import CapabilityRequest, JobGrant
from secondshift.jobs.tool_rules import (
    ToolRuleError,
    ToolRules,
    kept_rules,
    validate_rules,
)


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
    tool_rules: ToolRules | None = None,
) -> JobGrant:
    """`tool_rules` None keeps the rules for tools that stay approved."""
    approving = decision == JobGrant.Status.APPROVED
    if connection is not None:
        check_connection(capability, connection, approving)
    elif approving:
        raise GrantError(422, "Choose a connection before approving the capability.")
    allowed = granted_tools(capability, decision, connection, chosen_tools)

    grant = JobGrant.objects.filter(capability_request=capability).first() or JobGrant(
        capability_request=capability
    )
    rules = kept_rules(grant.tool_rules if tool_rules is None else tool_rules, allowed)
    if tool_rules is not None and set(tool_rules) - allowed:
        raise GrantError(422, "Rules can only apply to approved tools.")
    if rules and connection is not None:
        try:
            validate_rules(rules, connection.metadata.get("tools", {}), sorted(allowed))
        except ToolRuleError as error:
            raise GrantError(422, str(error)) from error
    grant.connection = connection
    grant.status = decision
    grant.allowed_tools = sorted(allowed)
    grant.tool_rules = rules
    grant.reviewed_by = reviewer
    grant.reviewed_at = timezone.now()
    grant.full_clean()
    grant.save()
    return grant
