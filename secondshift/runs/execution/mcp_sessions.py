"""The MCP sessions a run keeps, one per connection, until the run ends."""

from dataclasses import dataclass

from asgiref.sync import async_to_sync

from secondshift.connections.mcp.base import MCPSessionRef
from secondshift.connections.mcp.client import close_mcp_session
from secondshift.runs.dao import (
    add_event,
    create_run_mcp_session,
    delete_run_mcp_session,
    run_mcp_session,
    run_mcp_sessions,
)
from secondshift.runs.models import RunMCPSession


def session_ref(saved: RunMCPSession) -> MCPSessionRef:
    return MCPSessionRef(saved.session_id, saved.protocol_version)


@dataclass(frozen=True)
class RunMCPSessions:
    """Keeps a run's session with one connection. Sub-agents share it."""

    run_id: int
    connection_id: int
    binding: str

    def load(self) -> MCPSessionRef | None:
        saved = run_mcp_session(self.run_id, self.connection_id)
        return session_ref(saved) if saved is not None else None

    def keep(self, session: MCPSessionRef) -> MCPSessionRef:
        created = create_run_mcp_session(
            self.run_id, self.connection_id, session.id, session.protocol_version
        )
        if created is not None:
            return session
        saved = run_mcp_session(self.run_id, self.connection_id)
        return session_ref(saved) if saved is not None else session

    def forget(self, session: MCPSessionRef) -> None:
        delete_run_mcp_session(self.run_id, self.connection_id, session.id)
        add_event(
            self.run_id,
            f"The MCP session for {self.binding} expired. A new one started, "
            "and state the server kept was lost.",
            level="warning",
            metadata={"connection_id": self.connection_id},
        )


def close_run_mcp_sessions(run_id: int) -> None:
    """End every session the run kept. Best effort, never fails the run."""
    for saved in run_mcp_sessions(run_id):
        async_to_sync(close_mcp_session)(saved.connection, session_ref(saved))
        saved.delete()
