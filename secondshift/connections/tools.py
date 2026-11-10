"""What a connection's server offers, and which of those tools jobs may use."""

from typing import Any

from secondshift.connections.models import Connection


class UnknownTools(ValueError):
    pass


def store_tools(
    connection: Connection,
    offered: dict[str, Any],
    selected: list[str] | None,
    *,
    owner_selects: bool = True,
) -> None:
    """Save the offered tools and the ones jobs may use.

    When the owner chooses, tools marked read-only start chosen. Otherwise
    jobs may use every offered tool. Raises UnknownTools for a name the server
    does not offer.
    """
    if not owner_selects:
        chosen = set(offered)
    elif selected is None:
        chosen = {name for name, tool in offered.items() if tool.get("readOnly")}
    elif set(selected) - set(offered):
        raise UnknownTools
    else:
        chosen = set(selected)
    connection.available_tools = sorted(chosen)
    connection.metadata = {
        **connection.metadata,
        "tools": {name: offered[name] for name in sorted(chosen)},
        "offered_tools": offered if owner_selects else {},
    }
