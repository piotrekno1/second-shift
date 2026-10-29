"""The one tool registry, and the decorator every tool registers with.

A tool handler gets the signed-in request through `call_request`, so it can
use the same access checks as the HTTP views. Access errors and missing
objects become tool errors the client can read, never server errors.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps

from django.http import Http404, HttpRequest
from pydantic import BaseModel
from thin_mcp import (
    ToolAnnotations,
    ToolContext,
    ToolExecutionError,
    ToolPermissionDenied,
    ToolRegistry,
)

from secondshift.workspaces.access import AccessDenied

registry = ToolRegistry()

NOT_FOUND_MESSAGE = "Not found in this token's workspace."


@dataclass(frozen=True)
class McpCall:
    """The signed-in request behind a tool call."""

    request: HttpRequest


def call_request(context: ToolContext) -> HttpRequest:
    return context.require_resource(McpCall).request


type ToolHandler[ArgumentsT: BaseModel, ResultT: BaseModel] = Callable[
    [ToolContext, ArgumentsT], ResultT
]


def register_tool[ArgumentsT: BaseModel, ResultT: BaseModel](
    *,
    name: str,
    description: str,
    arguments_model: type[ArgumentsT],
    result_model: type[ResultT],
    read_only: bool,
) -> Callable[[ToolHandler[ArgumentsT, ResultT]], ToolHandler[ArgumentsT, ResultT]]:
    """Register a tool. Every tool says whether it only reads."""
    register = registry.register(
        name=name,
        description=description,
        arguments_model=arguments_model,
        result_model=result_model,
        annotations=ToolAnnotations(
            read_only_hint=read_only,
            destructive_hint=False,
            open_world_hint=False,
        ),
    )

    def decorator(
        handler: ToolHandler[ArgumentsT, ResultT],
    ) -> ToolHandler[ArgumentsT, ResultT]:
        @wraps(handler)
        def checked(context: ToolContext, arguments: ArgumentsT) -> ResultT:
            try:
                return handler(context, arguments)
            except AccessDenied as error:
                raise ToolPermissionDenied(str(error)) from error
            except Http404 as error:
                raise ToolExecutionError(NOT_FOUND_MESSAGE) from error

        register(checked)
        return handler

    return decorator
