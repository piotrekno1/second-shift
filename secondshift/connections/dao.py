"""Queries and writes on connections, sign-ins and agent logins."""

from collections import Counter
from collections.abc import Collection
from datetime import datetime
from typing import Any

from django.contrib.auth.models import AbstractBaseUser
from django.db.models import Count, Exists, F, OuterRef, Q, QuerySet
from django.utils import timezone

from secondshift.connections.models import (
    AgentConnection,
    AgentCredentialCleanup,
    Connection,
    OAuthAuthorization,
)
from secondshift.workspaces.models import Workspace


def bound_connections(
    workspace_id: int, bindings: Collection[str] | None = None
) -> QuerySet[Connection]:
    """The workspace's connections that have a binding key.

    Pass `bindings` to keep only connections with those keys.
    """
    connections = Connection.objects.filter(workspace_id=workspace_id).exclude(
        binding_key=""
    )
    if bindings is not None:
        connections = connections.filter(binding_key__in=bindings)
    return connections


def connections_by_binding(
    workspace_id: int, bindings: Collection[str] | None = None
) -> dict[str, Connection]:
    """The workspace's bound connections, keyed by binding key."""
    return {
        connection.binding_key: connection
        for connection in bound_connections(workspace_id, bindings)
    }


def connection_for_binding(workspace_id: int, binding: str) -> Connection | None:
    """The workspace's connection with this binding key, if there is one."""
    return bound_connections(workspace_id, [binding]).first()


def auto_approved_connections(
    workspace_ids: Collection[int], bindings: Collection[str]
) -> QuerySet[Connection]:
    """Connections with these keys that grant access without an owner's review."""
    return Connection.objects.filter(
        workspace_id__in=workspace_ids,
        binding_key__in=bindings,
        require_approval=False,
    ).exclude(binding_key="")


def create_connection(**fields: Any) -> Connection:
    return Connection.objects.create(**fields)


def get_connection(connection_id: int) -> Connection:
    """The connection. Raises Connection.DoesNotExist when there is none."""
    return Connection.objects.get(pk=connection_id)


def lock_connection(connection_id: int) -> Connection:
    """Lock the connection for this transaction."""
    return Connection.objects.select_for_update().get(pk=connection_id)


def connection_binding(connection_id: int) -> str | None:
    """The connection's binding key, or None when it was deleted."""
    return (
        Connection.objects.filter(pk=connection_id)
        .values_list("binding_key", flat=True)
        .first()
    )


def workspace_connections(
    workspace_id: int, kind: str | None = None
) -> QuerySet[Connection]:
    connections = Connection.objects.filter(workspace_id=workspace_id)
    return connections if kind is None else connections.filter(kind=kind)


def github_token_sources(workspace_id: int) -> QuerySet[Connection]:
    """The workspace's GitHub sources that use a personal access token."""
    return Connection.objects.filter(
        workspace_id=workspace_id,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.SOURCE,
        auth_strategy=Connection.AuthStrategy.API_KEY,
    )


def connected_count(workspace_id: int) -> int:
    return Connection.objects.filter(
        workspace_id=workspace_id, status=Connection.Status.CONNECTED
    ).count()


def binding_taken(workspace_id: int, binding_key: str) -> bool:
    """Whether a connection in the workspace already uses this binding key."""
    return Connection.objects.filter(
        workspace_id=workspace_id, binding_key=binding_key
    ).exists()


def catalog_connections(
    workspace: Workspace, kinds: Collection[str]
) -> list[Connection]:
    """The workspace's bound connections of these kinds, by binding key."""
    return list(
        Connection.objects.filter(workspace=workspace, kind__in=kinds)
        .exclude(binding_key="")
        .order_by("binding_key")
    )


def connected_github_sources(workspace_id: int | None = None) -> QuerySet[Connection]:
    """Connected GitHub sources, in one workspace or in all of them."""
    sources = Connection.objects.filter(
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.SOURCE,
        status=Connection.Status.CONNECTED,
    )
    if workspace_id is not None:
        sources = sources.filter(workspace_id=workspace_id)
    return sources


def has_github_token_sources() -> bool:
    """Whether any connected GitHub source signs in with a personal token."""
    return (
        connected_github_sources()
        .filter(auth_strategy=Connection.AuthStrategy.API_KEY)
        .exists()
    )


def save_github_source(
    workspace: Workspace, external_account_id: str, fields: dict[str, Any]
) -> Connection:
    """Create or update the workspace's GitHub source for this account."""
    connection, _ = Connection.objects.update_or_create(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        external_account_id=external_account_id,
        defaults=fields,
    )
    return connection


def mark_expired(connection_id: int) -> None:
    Connection.objects.filter(pk=connection_id).update(
        status=Connection.Status.EXPIRED, updated_at=timezone.now()
    )


def usable_models(workspace: Workspace) -> QuerySet[Connection]:
    """The workspace's model providers that were not revoked."""
    return Connection.objects.filter(
        workspace=workspace, kind=Connection.Kind.MODEL
    ).exclude(status=Connection.Status.REVOKED)


def connected_model(workspace_id: int, connection_id: int) -> Connection | None:
    """The workspace's model provider while it is connected."""
    return Connection.objects.filter(
        pk=connection_id,
        workspace_id=workspace_id,
        kind=Connection.Kind.MODEL,
        status=Connection.Status.CONNECTED,
    ).first()


def has_models_or_logins(workspace_id: int) -> bool:
    """Whether a model provider or an agent login is saved. One query for both."""
    return Workspace.objects.filter(
        Exists(
            Connection.objects.filter(
                workspace=OuterRef("pk"), kind=Connection.Kind.MODEL
            )
        )
        | Exists(AgentConnection.objects.filter(workspace=OuterRef("pk"))),
        pk=workspace_id,
    ).exists()


def auto_approved_connection(
    workspace_id: int, connection_id: int | None, kind: str | None, binding: str | None
) -> Connection | None:
    """The connection while it is connected and needs no owner's review."""
    return Connection.objects.filter(
        pk=connection_id,
        workspace_id=workspace_id,
        kind=kind,
        binding_key=binding,
        status=Connection.Status.CONNECTED,
        require_approval=False,
    ).first()


# Sign-ins


def delete_stale_authorizations(user: AbstractBaseUser, before: datetime) -> None:
    OAuthAuthorization.objects.filter(user=user, created_at__lt=before).delete()


def create_authorization(**fields: Any) -> OAuthAuthorization:
    return OAuthAuthorization.objects.create(**fields)


def lock_unused_authorization(
    state: str, user: AbstractBaseUser
) -> OAuthAuthorization | None:
    """Lock the person's unused sign-in with this state, with its connection."""
    return (
        # Lock only this row. Postgres cannot lock the optional connection join.
        OAuthAuthorization.objects.select_for_update(of=("self",))
        .select_related("connection")
        .filter(state=state, user=user, used_at__isnull=True)
        .first()
    )


# Agent logins


def workspace_agent_connections(workspace: Workspace) -> list[AgentConnection]:
    return list(AgentConnection.objects.filter(workspace=workspace))


def own_agent_connections(
    workspace: Workspace, user: AbstractBaseUser
) -> QuerySet[AgentConnection]:
    return AgentConnection.objects.filter(workspace=workspace, owner=user)


def connected_logins(
    workspace: Workspace, user: AbstractBaseUser
) -> QuerySet[AgentConnection]:
    """The person's own agent logins in the workspace that are signed in."""
    return AgentConnection.objects.filter(
        workspace=workspace, owner=user, connected=True
    )


def find_agent_connection(
    connection_id: int,
    workspace_id: int,
    owner_id: int,
    credential_key: str,
    backend: str,
) -> AgentConnection | None:
    """The login a run planned to use, if it still matches the plan."""
    return AgentConnection.objects.filter(
        pk=connection_id,
        workspace_id=workspace_id,
        owner_id=owner_id,
        credential_key=credential_key,
        backend=backend,
    ).first()


def queue_cleanup(connection: AgentConnection) -> AgentCredentialCleanup:
    """Ask the runner to forget the login's cached credentials."""
    cleanup, _ = AgentCredentialCleanup.objects.get_or_create(
        credential_key=connection.credential_key,
        defaults={"connection": connection},
    )
    return cleanup


def count_cleanup_attempt(cleanup: AgentCredentialCleanup) -> None:
    AgentCredentialCleanup.objects.filter(pk=cleanup.pk).update(
        attempts=F("attempts") + 1, updated_at=timezone.now()
    )


def pending_cleanups(limit: int) -> list[AgentCredentialCleanup]:
    """The oldest cleanups the runner has not done yet."""
    return list(AgentCredentialCleanup.objects.order_by("pk")[:limit])


def has_pending_cleanup(connection: AgentConnection) -> bool:
    return connection.credential_cleanups.exists()


def login_run_counts(workspace_id: int, since: datetime) -> Counter[int]:
    """Runs per agent login of the workspace since then."""
    return Counter(
        dict(
            AgentConnection.objects.filter(workspace_id=workspace_id)
            .annotate(recent=Count("runs", filter=Q(runs__created_at__gte=since)))
            .values_list("pk", "recent")
        )
    )
