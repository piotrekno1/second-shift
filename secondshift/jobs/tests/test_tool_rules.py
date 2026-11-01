from pathlib import Path

import pytest

from secondshift.connections.models import Connection
from secondshift.jobs.grants import GrantError, decide_grant
from secondshift.jobs.models import CapabilityRequest, Job, JobGrant
from secondshift.jobs.planning import build_execution_plan
from secondshift.jobs.sync import sync_manifest
from secondshift.jobs.tests.test_grants import decide
from secondshift.jobs.tests.test_planning import create_job
from secondshift.jobs.tests.test_sync import write_manifest
from secondshift.jobs.tool_rules import (
    ToolRuleError,
    ToolRuleViolation,
    apply_rules,
    broken_rules,
    narrow_schema,
    validate_rules,
)
from secondshift.workspaces.services import workspace_owner

APPROVED = JobGrant.Status.APPROVED

SCHEMA = {
    "type": "object",
    "properties": {
        "channel": {"type": "string"},
        "text": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "state": {"type": "string", "enum": ["open", "closed"]},
        "url": {"type": "string"},
    },
    "required": ["channel", "text"],
}
TOOLS = {"post_message": {"description": "Post", "inputSchema": SCHEMA}}
RULES = {
    "post_message": {
        "channel": {"type": "allowed", "values": ["#alerts", "#ops"]},
        "limit": {"type": "maximum", "value": 50},
        "state": {"type": "fixed", "value": "open"},
        "url": {"type": "forbidden"},
    }
}


def rules_for(tool_rules: dict) -> dict:
    return tool_rules["post_message"]


def test_fixed_values_override_the_model() -> None:
    result = apply_rules(
        "post_message",
        rules_for(RULES),
        {"channel": "#ops", "text": "hi", "limit": 10, "state": "closed"},
    )

    assert result["state"] == "open"


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ({"channel": "#general", "limit": 10}, "channel"),
        ({"limit": 10}, "channel"),
        ({"channel": "#ops", "limit": 51}, "limit"),
        ({"channel": "#ops"}, "limit"),
        ({"channel": "#ops", "limit": True}, "limit"),
        ({"channel": "#ops", "limit": 10, "url": "https://x"}, "url"),
    ],
)
def test_broken_rules_are_rejected(arguments: dict, match: str) -> None:
    with pytest.raises(ToolRuleViolation, match=match):
        apply_rules("post_message", rules_for(RULES), {"text": "hi", **arguments})


def test_rejection_does_not_echo_the_value_the_model_sent() -> None:
    with pytest.raises(ToolRuleViolation) as error:
        apply_rules(
            "post_message", rules_for(RULES), {"channel": "#secret-room", "limit": 1}
        )

    assert "#secret-room" not in str(error.value)


def test_schema_is_narrowed_for_the_model() -> None:
    narrowed = narrow_schema(SCHEMA, rules_for(RULES))

    assert set(narrowed["properties"]) == {"channel", "text", "limit"}
    assert narrowed["properties"]["channel"]["enum"] == ["#alerts", "#ops"]
    assert narrowed["properties"]["limit"]["maximum"] == 50
    assert set(narrowed["required"]) == {"channel", "text", "limit"}
    assert SCHEMA["properties"]["limit"]["maximum"] == 200


def test_narrowing_keeps_a_lower_provider_maximum() -> None:
    narrowed = narrow_schema(SCHEMA, {"limit": {"type": "maximum", "value": 500}})

    assert narrowed["properties"]["limit"]["maximum"] == 200


@pytest.mark.parametrize(
    ("rules", "match"),
    [
        ({"post_message": {"missing": {"type": "forbidden"}}}, "not an argument"),
        ({"post_message": {"text": {"type": "forbidden"}}}, "required"),
        ({"post_message": {"text": {"type": "maximum", "value": 5}}}, "not a number"),
        ({"post_message": {"limit": {"type": "maximum", "value": 999}}}, "outside"),
        ({"post_message": {"limit": {"type": "fixed", "value": "ten"}}}, "type"),
        ({"post_message": {"state": {"type": "allowed", "values": ["x"]}}}, "type"),
        ({"post_message": {"state": {"type": "allowed", "values": []}}}, "at least"),
        ({"other_tool": {"url": {"type": "forbidden"}}}, "not an approved tool"),
    ],
)
def test_rules_that_do_not_fit_are_refused(rules: dict, match: str) -> None:
    with pytest.raises(ToolRuleError, match=match):
        validate_rules(rules, TOOLS, ["post_message"])


def test_a_fixed_value_for_a_required_property_is_allowed() -> None:
    validate_rules(
        {"post_message": {"channel": {"type": "fixed", "value": "#alerts"}}},
        TOOLS,
        ["post_message"],
    )


def test_schema_drift_breaks_rules() -> None:
    changed = {
        "post_message": {
            "inputSchema": {**SCHEMA, "properties": {"channel": {"type": "string"}}}
        }
    }

    assert broken_rules(RULES, TOOLS) == []
    assert broken_rules(RULES, changed) == ["post_message"]


@pytest.fixture
def job(db: object) -> Job:
    return create_job(db)


@pytest.fixture
def capability(job: Job) -> CapabilityRequest:
    return CapabilityRequest.objects.create(
        job=job,
        binding="chat",
        kind=CapabilityRequest.Kind.MCP,
        requested_tools=["post_message"],
        required=True,
    )


def chat(job: Job) -> Connection:
    return Connection.objects.create(
        workspace=job.workspace,
        provider=Connection.Provider.MCP,
        kind=Connection.Kind.MCP,
        binding_key="chat",
        display_name="Chat",
        status=Connection.Status.CONNECTED,
        available_tools=["post_message"],
        metadata={
            "settings": {"url": "https://mcp.example.com/mcp"},
            "tools": TOOLS,
        },
    )


def test_approving_with_rules_stores_them(capability: CapabilityRequest) -> None:
    grant = decide_with_rules(capability, RULES)

    assert grant.tool_rules == RULES


def test_approving_again_without_rules_keeps_them(
    capability: CapabilityRequest,
) -> None:
    connection = decide_with_rules(capability, RULES).connection

    grant = decide(capability, APPROVED, connection)

    assert grant.tool_rules == RULES


def test_denying_drops_rules(capability: CapabilityRequest) -> None:
    connection = decide_with_rules(capability, RULES).connection

    grant = decide(capability, JobGrant.Status.DENIED, connection)

    assert grant.tool_rules == {}


def test_rules_that_do_not_fit_are_not_saved(capability: CapabilityRequest) -> None:
    with pytest.raises(GrantError, match="not an argument"):
        decide_with_rules(capability, {"post_message": {"nope": {"type": "forbidden"}}})

    assert not JobGrant.objects.filter(capability_request=capability).exists()


def decide_with_rules(capability: CapabilityRequest, rules: dict) -> JobGrant:
    connection = Connection.objects.filter(binding_key="chat").first() or chat(
        capability.job
    )
    return decide_grant(
        capability,
        APPROVED,
        connection,
        None,
        workspace_owner(capability.job.workspace),
        rules,
    )


def test_rules_are_frozen_into_the_plan(capability: CapabilityRequest) -> None:
    decide_with_rules(capability, RULES)

    plan = build_execution_plan(capability.job)

    assert plan["ready"] is True
    assert plan["capabilities"][0]["tool_rules"] == RULES


def test_schema_drift_blocks_the_job(capability: CapabilityRequest) -> None:
    connection = decide_with_rules(capability, RULES).connection
    connection.metadata = {
        **connection.metadata,
        "tools": {"post_message": {"inputSchema": {"type": "object"}}},
    }
    connection.save()

    plan = build_execution_plan(capability.job)

    assert plan["ready"] is False
    assert "tool_rules_broken" in plan["capabilities"][0]["reasons"]
    assert "tool_rules" not in plan["capabilities"][0]


def test_sync_keeps_rules_for_tools_still_requested(db, tmp_path: Path) -> None:
    job = create_job(db)
    connection = Connection.objects.create(
        workspace=job.workspace,
        provider=Connection.Provider.MCP,
        kind=Connection.Kind.MCP,
        binding_key="observability",
        display_name="Grafana",
    )
    path = tmp_path / ".secondshift.yaml"
    write_manifest(path, ["query_metrics", "list_alerts"])
    synced = sync_manifest(job.repository, path)[0]
    grant = JobGrant.objects.get(capability_request__job=synced, connection=connection)
    rule = {"limit": {"type": "maximum", "value": 5}}
    grant.status = APPROVED
    grant.tool_rules = {"query_metrics": rule, "list_alerts": rule}
    grant.save()

    write_manifest(path, ["query_metrics", "search_dashboards"])
    sync_manifest(job.repository, path)
    grant.refresh_from_db()
    assert grant.status == JobGrant.Status.PENDING
    assert grant.tool_rules == {"query_metrics": rule}

    write_manifest(path, ["search_dashboards"])
    sync_manifest(job.repository, path)
    grant.refresh_from_db()
    assert grant.tool_rules == {}


def test_manifest_cannot_carry_rules(db, tmp_path: Path) -> None:
    job = create_job(db)
    path = tmp_path / ".secondshift.yaml"
    write_manifest(path, ["query_metrics"])
    path.write_text(
        path.read_text()
        + "\n          rules: {query_metrics: {limit: {type: maximum, value: 1}}}"
    )

    with pytest.raises(Exception, match="rules"):
        sync_manifest(job.repository, path)
