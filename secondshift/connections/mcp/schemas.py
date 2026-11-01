"""Bounded, document-local JSON Schema references for MCP tool arguments."""

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from secondshift.connections.mcp.base import MCPError

MCP_SCHEMA_MAX_DEPTH = 30
MCP_SCHEMA_MAX_NODES = 10_000


def reference_target(root: Any, reference: Any) -> dict[str, Any] | bool:
    """Resolve only JSON Pointers in this document, without a network resolver."""
    if not isinstance(reference, str) or not reference.startswith("#/"):
        raise MCPError("Only local JSON Pointer MCP schema references are supported.")
    try:
        pointer = unquote(reference[2:], errors="strict")
        target = root
        for token in pointer.split("/"):
            if re.search(r"~(?![01])", token):
                raise ValueError
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(target, list) and re.fullmatch(r"0|[1-9][0-9]*", token):
                target = target[int(token)]
            elif isinstance(target, dict):
                target = target[token]
            else:
                raise ValueError
    except (KeyError, IndexError, ValueError):
        raise MCPError("The MCP schema contains an invalid local reference.") from None
    if not isinstance(target, dict | bool):
        raise MCPError("The MCP schema reference must point to a schema.")
    return target


@dataclass
class ToolSchemaValidator:
    root: Any
    remaining: int = MCP_SCHEMA_MAX_NODES

    def visit(self, value: Any, depth: int, ancestors: frozenset[int]) -> None:
        self.remaining -= 1
        if self.remaining < 0:
            raise MCPError("The MCP schema is too complex.")
        if depth > MCP_SCHEMA_MAX_DEPTH:
            raise MCPError("The MCP schema is too deeply nested.")
        if not isinstance(value, dict | list):
            return
        if id(value) in ancestors:
            raise MCPError("The MCP schema contains recursive references.")
        ancestors = ancestors | {id(value)}
        if isinstance(value, dict):
            self.check_references(value, depth, ancestors)
            children = value.values()
        else:
            children = value
        for child in children:
            self.visit(child, depth + 1, ancestors)

    def check_references(
        self, value: dict[str, Any], depth: int, ancestors: frozenset[int]
    ) -> None:
        if "$dynamicRef" in value or "$recursiveRef" in value:
            raise MCPError("Dynamic MCP schema references are not supported.")
        # Embedded resources change what a local pointer means downstream.
        # Keep all references relative to the single tool schema document.
        if depth and "$id" in value:
            raise MCPError("Nested MCP schema identifiers are not supported.")
        if "$ref" in value:
            self.visit(reference_target(self.root, value["$ref"]), depth + 1, ancestors)


def validate_tool_schema(value: Any, depth: int = 0) -> None:
    """Validate the reference graph while retaining schemas and sibling constraints."""
    ToolSchemaValidator(value).visit(value, depth, frozenset())
