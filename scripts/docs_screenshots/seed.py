"""Fill an empty database with the Acme workspace the docs screenshots show.

Run through `manage.py shell` so Django is set up first. run.sh does this.
"""

import secrets
import zoneinfo
from datetime import datetime, timedelta
from decimal import Decimal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.utils import timezone

from secondshift.agent.image.outcomes import AgentOutcome
from secondshift.connections.models import AgentConnection, Connection
from secondshift.gateway.models import ModelUsage
from secondshift.jobs.models import CapabilityRequest, Job, JobGrant
from secondshift.jobs.tests.plans import stored_plan
from secondshift.repositories.models import Repository
from secondshift.runs.constants import RunStart
from secondshift.runs.models import Run, RunEvent, Runner, Task
from secondshift.triggers.models import JobTrigger, TriggerEvent
from secondshift.workspaces.dao import create_workspace
from secondshift.workspaces.models import ApiToken, Workspace, WorkspaceMembership

User = get_user_model()
NOW = timezone.now()
COMMIT = "3f9c2a1" + "0" * 33
MODEL = "claude-sonnet-5-5"
IMAGES = {
    "python": "python:3.13-slim@sha256:57b29a7c050c"
    + "9d1e2f3a4b5c6d7e8f9" * 2
    + "a1b2c3d4e5f6a7",
    "python_node": "nikolaik/python-nodejs:python3.13-nodejs24@sha256:a1b2c3d4e5f6"
    + "a7b8c9d0e1f2a3b4c5d6" * 2
    + "e7f8a9b0c1d2e3f4",
}
SENTRY_TOOLS = {
    "search_issues": {
        "inputSchema": {
            "type": "object",
            "properties": {
                "organization": {"type": "string"},
                "project": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["organization", "query"],
        }
    },
    "get_issue_details": {
        "inputSchema": {
            "type": "object",
            "properties": {
                "organization": {"type": "string"},
                "issue_id": {"type": "string"},
            },
            "required": ["organization", "issue_id"],
        }
    },
    "get_trace": {
        "inputSchema": {
            "type": "object",
            "properties": {
                "organization": {"type": "string"},
                "trace_id": {"type": "string"},
            },
            "required": ["organization", "trace_id"],
        }
    },
}
ACME_ORG = {"organization": {"type": "fixed", "value": "acme"}}


def people() -> tuple[AbstractUser, AbstractUser, AbstractUser]:
    owner = User.objects.create_user(
        username="shots",
        email="dana@acme.example",
        first_name="Dana",
        last_name="Okafor",
        is_staff=True,
    )
    admin = User.objects.create_user(
        username="lee", email="lee@acme.example", first_name="Lee", last_name="Marsh"
    )
    member = User.objects.create_user(
        username="ari", email="ari@acme.example", first_name="Ari", last_name="Novak"
    )
    return owner, admin, member


def workspaces(
    owner: AbstractUser, admin: AbstractUser, member: AbstractUser
) -> Workspace:
    ws = create_workspace(name="Acme", slug="acme", owner=owner)
    WorkspaceMembership.objects.create(workspace=ws, user=admin, role="admin")
    WorkspaceMembership.objects.create(workspace=ws, user=member, role="member")
    ws.max_turns = 40
    ws.save()
    for name, slug, role in [
        ("Data platform", "data-platform", "admin"),
        ("Security", "security", "member"),
    ]:
        other = create_workspace(name=name, slug=slug, owner=admin)
        WorkspaceMembership.objects.create(workspace=other, user=owner, role=role)
    return ws


def connections(ws: Workspace, owner: AbstractUser, admin: AbstractUser) -> dict:
    model = Connection.objects.create(
        workspace=ws,
        provider="anthropic",
        kind="model",
        display_name="Anthropic",
        auth_strategy="api_key",
        default_model=MODEL,
        allowed_models=[MODEL, "claude-haiku-5-5"],
    )
    ws.default_model_connection = model
    ws.save()
    checked = {
        "ok": True,
        "reason": AgentOutcome.COMPLETED,
        "checked_at": (NOW - timedelta(hours=3)).isoformat(),
    }
    AgentConnection.objects.create(
        workspace=ws,
        owner=owner,
        backend="claude_code",
        display_name="Dana's Claude Code",
        auth_data="{}",
        max_runs_per_day=20,
        last_check={
            **checked,
            "message": "Signed in. Claude Code answered a test message.",
        },
    )
    AgentConnection.objects.create(
        workspace=ws,
        owner=admin,
        backend="codex",
        display_name="Lee's Codex",
        auth_data="{}",
        last_check={**checked, "message": "Signed in. Codex answered a test message."},
    )
    mcp = {"provider": "mcp", "kind": "mcp", "workspace": ws}
    return {
        "sentry": Connection.objects.create(
            **mcp,
            binding_key="errors",
            display_name="Sentry",
            metadata={
                "settings": {"url": "https://mcp.sentry.dev/mcp"},
                "tools": SENTRY_TOOLS,
            },
            auth_strategy="oauth",
            require_approval=True,
            available_tools=[
                "search_issues",
                "get_issue_details",
                "list_events",
                "get_event",
                "get_trace",
                "list_releases",
                "update_issue",
                "find_projects",
            ],
        ),
        "linear": Connection.objects.create(
            **mcp,
            binding_key="tickets",
            display_name="Linear",
            metadata={"settings": {"url": "https://mcp.linear.app/mcp"}},
            auth_strategy="oauth",
            require_approval=True,
            available_tools=[
                "list_issues",
                "get_issue",
                "create_issue",
                "update_issue",
                "create_comment",
                "list_projects",
                "list_teams",
            ],
        ),
        "grafana": Connection.objects.create(
            **mcp,
            binding_key="observability",
            display_name="Grafana",
            metadata={"settings": {"url": "https://grafana.acme.example/api/mcp"}},
            auth_strategy="api_key",
            require_approval=False,
            available_tools=[
                "search_dashboards",
                "query_prometheus",
                "query_loki",
                "list_alert_rules",
                "get_dashboard",
            ],
        ),
        "slack": Connection.objects.create(
            workspace=ws,
            provider="webhook",
            kind="notification",
            binding_key="eng-alerts",
            display_name="Slack #eng-alerts",
            auth_strategy="none",
            delivery_url="https://hooks.slack.example/T0/B0/x",
        ),
    }


def next_weekday_morning() -> datetime:
    zone = zoneinfo.ZoneInfo("Europe/Warsaw")
    day = NOW.astimezone(zone).date() + timedelta(days=1)
    while day.weekday() > 4:
        day += timedelta(days=1)
    return datetime(day.year, day.month, day.day, 8, 0, tzinfo=zone)


def grant(
    job: Job, binding: str, kind: str, connection: Connection, **fields: object
) -> None:
    tools = fields.pop("tools", None)
    request = CapabilityRequest.objects.create(
        job=job,
        binding=binding,
        kind=kind,
        requested_tools=tools or [],
        required=kind != "notification",
    )
    JobGrant.objects.create(capability_request=request, connection=connection, **fields)


def jobs(repo: Repository, owner: AbstractUser, conns: dict) -> dict[str, Job]:
    ws = repo.workspace
    reviewed = {"reviewed_by": owner, "reviewed_at": NOW - timedelta(days=3)}
    errors = Job.objects.create(
        workspace=ws,
        repository=repo,
        key="production-errors",
        name="Production error review",
        skill_path=".agents/skills/production-errors/SKILL.md",
        cron="0 8 * * 1-5",
        timezone="Europe/Warsaw",
        enabled=True,
        config_hash="h1",
        triggers=["webhook"],
        next_run_at=next_weekday_morning(),
        token_budget=4_000_000,
        timeout_seconds=1800,
        skill_text="# Production error review\n\n"
        "Look into new or worsening errors and trace them to the code.\n",
    )
    sentry_tools = ["search_issues", "get_issue_details", "get_trace"]
    grant(
        errors,
        "errors",
        "mcp",
        conns["sentry"],
        tools=sentry_tools,
        status="approved",
        allowed_tools=sentry_tools,
        tool_rules={
            "search_issues": {**ACME_ORG, "limit": {"type": "maximum", "value": 25}},
            "get_issue_details": ACME_ORG,
            "get_trace": ACME_ORG,
        },
        **reviewed,
    )
    grant(
        errors,
        "tickets",
        "mcp",
        conns["linear"],
        tools=["list_issues", "create_comment"],
        status="pending",
    )
    grant(
        errors,
        "eng-alerts",
        "notification",
        conns["slack"],
        status="approved",
        **reviewed,
    )
    JobTrigger.objects.create(
        job=errors,
        kind="webhook",
        secret="s3cr3t-value",
        enabled=True,
        max_runs_per_hour=6,
        secret_rotated_at=NOW - timedelta(days=3),
    )

    alerts = Job.objects.create(
        workspace=ws,
        repository=repo,
        key="alert-triage",
        name="Alert triage",
        skill_path=".agents/skills/alert-triage/SKILL.md",
        enabled=True,
        config_hash="h2",
        triggers=["webhook"],
    )
    grafana_tools = ["query_prometheus", "query_loki"]
    grant(
        alerts,
        "observability",
        "mcp",
        conns["grafana"],
        tools=grafana_tools,
        status="approved",
        allowed_tools=grafana_tools,
    )

    release = Job.objects.create(
        workspace=ws,
        repository=repo,
        key="release-readiness",
        name="Release readiness",
        skill_path=".agents/skills/release-readiness/SKILL.md",
        cron="0 15 * * 4",
        enabled=False,
        config_hash="h3",
    )
    grant(
        release,
        "errors",
        "mcp",
        conns["sentry"],
        tools=["list_releases", "update_issue"],
        status="pending",
    )

    deps = Job.objects.create(
        workspace=ws,
        repository=repo,
        key="dependency-review",
        name="Dependency review",
        skill_path=".agents/skills/dependency-review/SKILL.md",
        cron="0 9 * * 1",
        enabled=True,
        config_hash="h4",
    )
    flaky = Job.objects.create(
        workspace=ws,
        repository=repo,
        key="flaky-tests",
        name="Flaky test triage",
        skill_path=".agents/skills/flaky-tests/SKILL.md",
        cron="0 7 * * *",
        enabled=True,
        config_hash="h5",
    )
    return {"errors": errors, "alerts": alerts, "deps": deps, "flaky": flaky}


def run(
    job: Job,
    status: str,
    summary: str,
    minutes_ago: int,
    duration: int = 6,
    **fields: object,
) -> Run:
    budget = 4_000_000 if job.token_budget else 2_000_000
    timeout = job.timeout_seconds or 900
    plan = stored_plan(
        execution={"backend": "pydantic", "label": "Model API", "model": MODEL},
        agent={
            "limits": {
                "max_turns": 40,
                "max_tool_calls": 150,
                "max_tool_result_chars": 65_536,
                "token_budget": budget,
                "timeout_seconds": timeout,
            }
        },
        job={"key": job.key, "skill_path": job.skill_path},
        repository={"full_name": job.repository.full_name},
    )
    started = NOW - timedelta(minutes=minutes_ago)
    created = Run.objects.create(
        job=job,
        task=Task.objects.create(repository=job.repository),
        execution_plan=plan,
        started_by=fields.pop("started_by", RunStart.SCHEDULE),
        status=status,
        commit_sha=COMMIT,
        summary=summary,
        scheduled_for=started,
        started_at=started,
        finished_at=started + timedelta(minutes=duration),
        **fields,
    )
    Run.objects.filter(pk=created.pk).update(created_at=started - timedelta(seconds=4))
    return created


def usage(target: Run, calls: int, tokens: tuple[int, int, int], cost: str) -> None:
    inp, out, cached = tokens
    for _ in range(calls):
        call = ModelUsage.objects.create(
            run=target,
            provider="anthropic",
            model=MODEL,
            status_code=200,
            input_tokens=inp,
            output_tokens=out,
            cache_read_tokens=cached,
            cost_usd=Decimal(cost),
        )
        ModelUsage.objects.filter(pk=call.pk).update(created_at=target.started_at)


def event(
    target: Run, offset: int, message: str, level: str = "info", **meta: object
) -> None:
    created = RunEvent.objects.create(
        run=target, message=message, level=level, metadata=meta
    )
    RunEvent.objects.filter(pk=created.pk).update(
        created_at=target.started_at + timedelta(seconds=offset)
    )


def recent_runs(jobs: dict[str, Job]) -> dict[str, Run]:
    """The five runs of the last two days that the limits screenshots show."""
    succeeded, failed = Run.Status.SUCCEEDED, Run.Status.FAILED
    timed_out = run(
        jobs["alerts"],
        failed,
        "Stopped at its time limit.",
        2900,
        duration=15,
        failure="timeout",
        failure_reason="The run reached its 15 minute time limit.",
    )
    usage(timed_out, 18, (8000, 900, 300000), "0.12")
    quiet = run(jobs["errors"], succeeded, "No new errors since Friday.", 1500)
    usage(quiet, 6, (5000, 600, 30000), "0.019")
    blip = run(
        jobs["alerts"],
        succeeded,
        "Alert was a deploy blip. No code change needed.",
        600,
    )
    usage(blip, 9, (7000, 800, 40000), "0.031")
    budget = run(
        jobs["alerts"],
        failed,
        "Stopped at its token budget after checking 41 alert rules.",
        190,
        duration=11,
        failure="budget",
        failure_reason="The run used its token budget of 2M tokens. "
        "Raise the job's token budget or narrow the skill.",
    )
    usage(budget, 24, (9000, 1200, 760000), "0.21")
    traced = run(
        jobs["errors"],
        succeeded,
        "Traced 3 new errors to the checkout refactor.",
        40,
        started_by=RunStart.WEBHOOK,
    )
    usage(traced, 14, (6000, 900, 52000), "0.038")
    return {"budget": budget, "traced": traced}


def history(jobs: dict[str, Job]) -> None:
    """Two weeks of older runs, so spend has a trend and a ranking by job."""
    day = 24 * 60
    plan = [
        # job, days ago, summary, model calls, cost per call
        (
            "deps",
            3.2,
            "Two minor upgrades are safe. One needs a migration.",
            30,
            "0.031",
        ),
        ("deps", 5.1, "No dependency changes this week.", 12, "0.024"),
        ("flaky", 3.6, "test_checkout_retry fails on slow runners.", 36, "0.042"),
        ("flaky", 4.6, "No new flaky tests.", 14, "0.028"),
        ("flaky", 6.4, "test_export_csv depends on test order.", 32, "0.04"),
        ("errors", 4.1, "One new error in billing, already fixed on main.", 12, "0.03"),
        ("errors", 6.1, "No new errors.", 8, "0.025"),
        ("deps", 8.2, "Three upgrades are safe.", 26, "0.03"),
        ("deps", 12.2, "No dependency changes this week.", 10, "0.024"),
        ("flaky", 8.6, "No new flaky tests.", 12, "0.027"),
        ("flaky", 10.6, "test_sync_webhook times out under load.", 30, "0.04"),
        ("errors", 9.1, "No new errors.", 8, "0.025"),
        ("errors", 11.1, "Traced a timeout to the search index.", 14, "0.032"),
        (
            "alerts",
            8.4,
            "Checked 30 alert rules. Two thresholds are too tight.",
            24,
            "0.12",
        ),
        ("alerts", 10.4, "Latency alert traced to a slow migration.", 22, "0.12"),
        ("alerts", 11.9, "Checked 28 alert rules. No changes needed.", 20, "0.11"),
        ("alerts", 9.5, "Disk alert on db-2 cleared on its own.", 10, "0.03"),
        ("alerts", 13.4, "CPU alert matched the nightly backup.", 9, "0.03"),
    ]
    for key, days_ago, summary, calls, cost in plan:
        done = run(jobs[key], Run.Status.SUCCEEDED, summary, int(days_ago * day), 7)
        usage(done, calls, (6000, 800, 40000), cost)


def timelines(runs: dict[str, Run]) -> None:
    budget = runs["budget"]
    event(budget, 0, "Run started.")
    event(budget, 6, "Checked out the repository at 3f9c2a100000 (2.4 MiB compressed).")
    event(budget, 9, "Sandbox started without network access.")
    tools = [
        "observability.list_alert_rules",
        "observability.query_prometheus",
        "observability.query_loki",
        "observability.query_prometheus",
        "observability.query_loki",
        "observability.query_prometheus",
    ]
    for i, tool in enumerate(tools):
        event(
            budget,
            20 + i * 70,
            f"Tool completed: {tool}",
            tool=tool,
            ok=True,
            result_bytes=18000 + i * 4000,
        )
    event(
        budget,
        440,
        "Tool blocked by a restriction: observability.query_loki",
        level="warning",
        tool="observability.query_loki",
        ok=False,
        reason="The query must stay within the last 24 hours.",
    )
    event(
        budget,
        650,
        "Agent execution finished.",
        "error",
        reason="token_budget_exhausted",
    )

    traced = runs["traced"]
    event(traced, 0, "Run started.")
    event(traced, 5, "Checked out the repository at 3f9c2a100000 (2.4 MiB compressed).")
    event(traced, 8, "Sandbox started without network access.")
    tools = [
        "errors.search_issues",
        "errors.get_issue_details",
        "errors.get_trace",
        "errors.get_issue_details",
    ]
    for i, tool in enumerate(tools):
        event(
            traced,
            30 + i * 50,
            f"Tool completed: {tool}",
            tool=tool,
            ok=True,
            result_bytes=9000,
        )
    event(traced, 340, "Agent execution finished.", reason="completed")
    event(traced, 352, "Run succeeded. The report is stored as the run's output.")


def trigger_requests(errors: Job, traced: Run) -> None:
    trigger = JobTrigger.objects.get(job=errors)
    for minutes, outcome, reason, started in [
        (52, "started", "", traced),
        (47, "refused", "This trigger already started 6 runs in the last hour.", None),
        (45, "ignored", "Same alert as a run started 3 minutes ago.", None),
    ]:
        received = TriggerEvent.objects.create(
            trigger=trigger, outcome=outcome, reason=reason, run=started
        )
        TriggerEvent.objects.filter(pk=received.pk).update(
            received_at=NOW - timedelta(minutes=minutes)
        )


def provisioning_key() -> str:
    key = ec.generate_private_key(ec.SECP256R1()).public_key()
    return key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()


def runners() -> None:
    common = {
        "token": "t",
        "environments": list(IMAGES),
        "images": IMAGES,
        "default_environment": "python",
    }
    Runner.objects.create(
        name="default",
        url="http://runner:8080",
        max_sessions=4,
        agents=True,
        provisioning_key=provisioning_key(),
        **common,
    )
    Runner.objects.create(
        name="gpu-box",
        url="https://gpu-box.acme.example",
        max_sessions=2,
        agents=False,
        provisioning_key=provisioning_key(),
        state="down",
        failing_since=NOW - timedelta(minutes=14),
        **common,
    )
    Runner.objects.create(
        name="runner-b",
        url="https://runner-b.acme.example",
        max_sessions=4,
        agents=True,
        provisioning_key=provisioning_key(),
        state="draining",
        **common,
    )


def api_tokens(ws: Workspace, owner: AbstractUser) -> None:
    for name, scope, days_used in [
        ("Claude Code on laptop", "run", 0),
        ("CI", "read", 2),
    ]:
        ApiToken.objects.create(
            workspace=ws,
            created_by=owner,
            name=name,
            scope=scope,
            token_hash=secrets.token_hex(32),
            expires_at=NOW + timedelta(days=60),
            last_used_at=NOW - timedelta(days=days_used, hours=1),
        )


def main() -> None:
    owner, admin, member = people()
    ws = workspaces(owner, admin, member)
    repo = Repository.objects.create(
        workspace=ws,
        full_name="acme/platform",
        default_branch="main",
        source_type="local",
        local_path="/srv/repos/platform",
        enabled=True,
        config_sha=COMMIT,
        last_synced_at=NOW - timedelta(minutes=12),
    )
    all_jobs = jobs(repo, owner, connections(ws, owner, admin))
    recent = recent_runs(all_jobs)
    history(all_jobs)
    timelines(recent)
    trigger_requests(all_jobs["errors"], recent["traced"])
    runners()
    api_tokens(ws, owner)
    print(f"Seeded. The budget run is /runs/{recent['budget'].pk}.")


main()
