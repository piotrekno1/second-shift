"""Errors shared by the connection services and external tools."""


class ExternalToolError(ValueError):
    """A policy or configuration failure. The message is safe to show."""


class ExternalRequestError(ExternalToolError):
    """A remote failure the agent may report as unavailable evidence."""


class ConnectionConflict(ValueError):
    """A request that clashes with an existing connection. Safe to show."""
