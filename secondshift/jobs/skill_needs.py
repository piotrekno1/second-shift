"""What a skill says it needs, before anyone runs it.

Skill files are mostly prose. Some list tools in their frontmatter's
`allowed-tools`, and many name MCP tools as `mcp__<server>__<tool>`. This
reads both and matches each server to a binding in the workspace. It is a hint
for people deciding whether to try a skill. It never grants anything, and a
run still gets only what its plan allows.
"""

import re
from dataclasses import dataclass
from typing import Any, Literal

import yaml

from secondshift.connections.dao import connections_by_binding
from secondshift.connections.models import Connection
from secondshift.jobs.automatic_grants import approves_automatically
from secondshift.jobs.capabilities import RequestedCapability
from secondshift.jobs.models import CapabilityRequest
from secondshift.jobs.needs import NeedStatus, connection_problem
from secondshift.repositories.external_skills import (
    ExternalSkill,
    resolve_external_skill,
)
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace

# mcp__<server>__<tool>, or mcp__<server> for every tool. A server name may
# hold single underscores, as in claude_ai_Linear.
MCP_TOOL = re.compile(
    r"\bmcp__((?:[A-Za-z0-9-]|_(?!_))+)(?:__([A-Za-z0-9-]+(?:_[A-Za-z0-9-]+)*))?"
)
# Hosted MCP servers that Claude apps add put this in front of the name.
SERVER_PREFIXES = ("claude-ai-",)
FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
MAX_NEEDS = 30
# Enough to read a skill before trying it.
MAX_INSPECTED_CHARS = 50_000

Origin = Literal["declared", "mentioned"]
TASK_APPROVAL_DETAIL = (
    "Connected, but it needs an owner's approval. Tasks use only connections "
    "that skip approval."
)
READY_DETAIL = "Connected. A task can use it."


@dataclass(frozen=True)
class SkillDetails:
    name: str
    description: str


@dataclass(frozen=True)
class SkillNeed:
    # The workspace binding it would use, guessed from the server name.
    binding: str
    # As the skill names it.
    server: str
    tools: list[str]
    # declared: in the frontmatter's allowed-tools. mentioned: in the text.
    origin: Origin
    status: NeedStatus
    detail: str


def frontmatter(text: str) -> dict[str, Any]:
    match = FRONTMATTER.match(text)
    if not match:
        return {}
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def skill_details(text: str) -> SkillDetails:
    data = frontmatter(text)
    return SkillDetails(
        name=str(data.get("name") or "")[:200],
        description=str(data.get("description") or "")[:1000],
    )


def declared_tools(data: dict[str, Any]) -> str:
    value = data.get("allowed-tools") or data.get("allowed_tools") or ""
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def binding_name(server: str) -> str:
    name = server.lower().replace("_", "-")
    for prefix in SERVER_PREFIXES:
        name = name.removeprefix(prefix)
    return name.strip("-")


def mcp_mentions(text: str) -> dict[str, tuple[str, set[str]]]:
    """Each binding with the server name the skill used and the tools named."""
    found: dict[str, tuple[str, set[str]]] = {}
    for server, tool in MCP_TOOL.findall(text):
        binding = binding_name(server)
        if not binding:
            continue
        _, tools = found.setdefault(binding, (server, set()))
        if tool:
            tools.add(tool)
    return found


def requested_mcps(text: str) -> list[tuple[str, str, list[str], Origin]]:
    """Binding, server name, tools and origin for each MCP server named."""
    declared = mcp_mentions(declared_tools(frontmatter(text)))
    mentioned = mcp_mentions(text)
    needs = []
    for binding, (server, tools) in declared.items():
        tools |= mentioned.get(binding, ("", set()))[1]
        needs.append((binding, server, sorted(tools), "declared"))
    for binding, (server, tools) in mentioned.items():
        if binding not in declared:
            needs.append((binding, server, sorted(tools), "mentioned"))
    return needs[:MAX_NEEDS]


def need_status(
    binding: str,
    tools: list[str],
    connection: Connection | None,
    repository: Repository | None,
) -> tuple[NeedStatus, str]:
    kind = CapabilityRequest.Kind.MCP
    repositories = [repository] if repository else []
    problem = connection_problem(kind, connection, set(tools), repositories)
    if problem or connection is None:
        return problem or ("missing", "")
    request = RequestedCapability(binding, kind, tuple(tools), False)
    if repository is not None:
        allowed = approves_automatically(request, connection, repository)
    else:
        allowed = not connection.require_approval
    if not allowed:
        return "needs_approval", TASK_APPROVAL_DETAIL
    return "ready", READY_DETAIL


def skill_needs(
    workspace: Workspace, text: str, repository: Repository | None = None
) -> list[SkillNeed]:
    """What the skill names, and whether a task in this workspace could use it."""
    requested = requested_mcps(text)
    connections = connections_by_binding(
        workspace.pk, [binding for binding, *_ in requested]
    )
    needs = []
    for binding, server, tools, origin in requested:
        status, detail = need_status(
            binding, tools, connections.get(binding), repository
        )
        needs.append(SkillNeed(binding, server, tools, origin, status, detail))
    return needs


@dataclass(frozen=True)
class SkillInspection:
    skill: ExternalSkill
    details: SkillDetails
    needs: list[SkillNeed]


def inspect_external_skill(
    workspace: Workspace,
    repository_name: str,
    path: str,
    ref: str | None,
    target: Repository | None = None,
) -> SkillInspection:
    """Fetch a skill from another repository and read what it needs.

    Raises ExternalSkillError, worded for the person who named it.
    """
    skill = resolve_external_skill(repository_name, path, ref)
    return SkillInspection(
        skill=skill,
        details=skill_details(skill.text),
        needs=skill_needs(workspace, skill.text, target),
    )
