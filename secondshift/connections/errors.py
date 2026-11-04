"""Errors shared by every kind of external tool (MCP servers, HTTP APIs)."""


class ExternalToolError(ValueError):
    """A policy or configuration failure. The message is safe to show."""


class ExternalRequestError(ExternalToolError):
    """A remote failure the agent may report as unavailable evidence."""


class ToolArgumentError(ExternalToolError):
    """The model sent arguments the tool refuses. It may fix them and try again."""
