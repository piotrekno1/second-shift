"""Logical capability requests, independent of a saved job or its grants."""

from dataclasses import dataclass

from secondshift.jobs.manifest import JobManifest
from secondshift.jobs.models import CapabilityRequest


@dataclass(frozen=True)
class RequestedCapability:
    binding: str
    kind: str
    requested_tools: tuple[str, ...]
    required: bool = True


def manifest_capabilities(definition: JobManifest) -> list[RequestedCapability]:
    return [
        *(
            RequestedCapability(
                item.binding,
                CapabilityRequest.Kind.MCP,
                tuple(item.tools),
                item.required,
            )
            for item in definition.requires.mcps
        ),
        *(
            RequestedCapability(
                item.binding,
                CapabilityRequest.Kind.API,
                tuple(item.tools),
                item.required,
            )
            for item in definition.requires.apis
        ),
        *(
            RequestedCapability(
                item.binding, CapabilityRequest.Kind.NOTIFICATION, (), item.required
            )
            for item in definition.notify
        ),
    ]
