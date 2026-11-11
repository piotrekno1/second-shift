from pathlib import Path
from typing import Any

import pytest
from django.test import Client

from secondshift.connections.models import Connection
from secondshift.jobs.skill_needs import requested_mcps, skill_details, skill_needs
from secondshift.repositories.tests.conftest import (
    workspace as workspace_fixture,
)
from secondshift.repositories.tests.test_external_skills import (
    PATH,
    REPOSITORY,
)
from secondshift.repositories.tests.test_external_skills import (
    vendor as vendor_fixture,
)
from secondshift.workspaces.models import Workspace

workspace = workspace_fixture
vendor = vendor_fixture

SKILL = """---
name: triage
description: Sort new bugs.
allowed-tools: Read, Grep, mcp__claude_ai_Linear__create_issue mcp__sentry
---
# Triage

Look up errors with mcp__sentry__search_issues. Check open work with
mcp__claude_ai_Linear__list_issues and mcp__github__get_file_contents.
"""


def test_declared_and_mentioned_servers_are_read() -> None:
    assert requested_mcps(SKILL) == [
        ("linear", "claude_ai_Linear", ["create_issue", "list_issues"], "declared"),
        ("sentry", "sentry", ["search_issues"], "declared"),
        ("github", "github", ["get_file_contents"], "mentioned"),
    ]
    assert skill_details(SKILL).name == "triage"
    assert skill_details(SKILL).description == "Sort new bugs."


@pytest.mark.parametrize(
    "text",
    ["", "# No frontmatter", "---\n: [\n---\nmcp__x", "---\n- a list\n---\n"],
)
def test_odd_skills_do_not_break_the_reader(text: str) -> None:
    skill_details(text)
    requested_mcps(text)


def mcp(workspace: Workspace, binding: str, **fields: Any) -> Connection:
    return Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.MCP,
        kind=Connection.Kind.MCP,
        binding_key=binding,
        display_name=binding.title(),
        status=Connection.Status.CONNECTED,
        **fields,
    )


def test_each_need_says_whether_a_task_could_use_it(workspace: Workspace) -> None:
    mcp(workspace, "linear", available_tools=["create_issue", "list_issues"])
    mcp(
        workspace,
        "sentry",
        available_tools=["search_issues"],
        require_approval=True,
    )

    needs = {need.binding: need for need in skill_needs(workspace, SKILL)}

    assert needs["linear"].status == "ready"
    assert needs["sentry"].status == "needs_approval"
    assert needs["github"].status == "missing"


def test_a_tool_the_connection_lacks_is_unavailable(workspace: Workspace) -> None:
    mcp(workspace, "linear", available_tools=["list_issues"])

    needs = {need.binding: need for need in skill_needs(workspace, SKILL)}

    assert needs["linear"].status == "unavailable"
    assert "create_issue" in needs["linear"].detail


def test_the_inspect_endpoint_pins_and_reads_the_skill(
    workspace: Workspace, vendor: Path
) -> None:
    client = Client()
    client.force_login(workspace.memberships.get().user)

    response = client.post(
        "/api/skills/inspect",
        {"repository": REPOSITORY, "path": PATH},
        content_type="application/json",
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["name"] == "triage"
    assert len(body["commit_sha"]) == 40
    assert body["file_count"] == 2
    assert [need["binding"] for need in body["needs"]] == ["linear"]
    assert body["needs"][0]["status"] == "missing"

    missing = client.post(
        "/api/skills/inspect",
        {"repository": REPOSITORY, "path": "nope/SKILL.md"},
        content_type="application/json",
    )
    assert missing.status_code == 422
    assert "is not a file" in missing.json()["detail"]
