"""The settings page: who is in the workspace and what the server enforces.

Runner settings are server-owned (see AGENTS.md), so they are shown with the
environment variable that changes them rather than edited here. Run limits are
edited on the page within server maximums (see secondshift.automations.limits).
Nothing secret is included: no keys, connection URLs or filesystem paths.
"""

from dataclasses import dataclass

from django.conf import settings

from secondshift.config.constants import (
    DEFAULT_SANDBOX_IMAGE,
    Environment,
    SandboxRuntime,
)
from secondshift.workspaces.access import ROLE_RANK, Role
from secondshift.workspaces.models import Workspace


@dataclass(frozen=True)
class SettingValue:
    label: str
    value: str
    env: str
    help: str


@dataclass(frozen=True)
class SettingGroup:
    title: str
    items: list[SettingValue]


@dataclass(frozen=True)
class Member:
    email: str
    role: str


def size_label(value: int) -> str:
    for unit, factor in (("MB", 1024 * 1024), ("KB", 1024)):
        if value >= factor and value % factor == 0:
            return f"{value // factor} {unit}"
    return f"{value:,} bytes"


def duration_label(seconds: int) -> str:
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
    return f"{seconds} seconds"


def environment_label() -> str:
    return {Environment.DEV: "Development", Environment.LOCAL: "This computer"}.get(
        settings.ENVIRONMENT, "Production"
    )


def sandbox_label() -> str:
    """How command sandboxes are isolated, in words."""
    if settings.RUNNER_RUNTIME == SandboxRuntime.GVISOR:
        return "gVisor sandbox"
    return "Trusted Docker"


def image_label(image: str) -> str:
    """The image name with a short digest, e.g. python:3.13-slim @ 57b29a7c050c."""
    name, _, digest = image.partition("@sha256:")
    return f"{name} @ {digest[:12]}" if digest else image


def members(workspace: Workspace) -> list[Member]:
    """Owners first, then admins, then members, each in the order they joined."""
    memberships = sorted(
        workspace.memberships.select_related("user").order_by("created_at", "pk"),
        key=lambda membership: -ROLE_RANK[Role(membership.role)],
    )
    return [
        Member(user_email(membership.user), membership.get_role_display())
        for membership in memberships
    ]


def user_email(user: object) -> str:
    return getattr(user, "email", "") or user.get_username()  # type: ignore[attr-defined]


def setting_groups() -> list[SettingGroup]:
    retention = settings.RUN_RETENTION_DAYS
    return [
        SettingGroup(
            "Runs",
            [
                SettingValue(
                    "History kept for",
                    f"{retention} days" if retention > 0 else "Forever",
                    "SECONDSHIFT_RUN_RETENTION_DAYS",
                    "Finished runs and their files are deleted after this.",
                ),
                SettingValue(
                    "Largest stored output",
                    size_label(settings.RUN_OUTPUT_MAX_BYTES),
                    "SECONDSHIFT_RUN_OUTPUT_MAX_BYTES",
                    "Longer reports keep their start and end.",
                ),
                SettingValue(
                    "File storage",
                    "Object storage (S3)"
                    if settings.ARTIFACT_STORAGE == "s3"
                    else "Server filesystem",
                    "SECONDSHIFT_ARTIFACT_STORAGE",
                    "Where run input and output files are kept.",
                ),
            ],
        ),
        SettingGroup(
            "Sandbox",
            [
                SettingValue(
                    "Isolation",
                    sandbox_label(),
                    "SECONDSHIFT_RUNNER_RUNTIME",
                    "Production requires gVisor and refuses to start without it.",
                ),
                SettingValue(
                    "Network",
                    "None",
                    "",
                    "Skills cannot reach the network. Model and approved MCP "
                    "calls go through SecondShift.",
                ),
                SettingValue(
                    "Image",
                    image_label(settings.RUNNER_IMAGE),
                    "SECONDSHIFT_RUNNER_IMAGE",
                    "Repositories cannot choose the image."
                    if settings.RUNNER_IMAGE != DEFAULT_SANDBOX_IMAGE
                    else "Docker's official Python image, pinned. "
                    "Repositories cannot choose the image.",
                ),
            ],
        ),
    ]
