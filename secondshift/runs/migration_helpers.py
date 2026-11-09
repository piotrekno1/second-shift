"""Versioned data operations used by generated runs migrations.

Keep these functions independent of current models and job configuration.
"""

from typing import Any

from django.apps.registry import Apps
from django.conf import settings
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def backfill_tasks_v1(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Preserve the assignment actually recorded on each historical run."""
    run_model = apps.get_model("runs", "Run")
    task_model = apps.get_model("runs", "Task")
    alias = schema_editor.connection.alias
    while runs := list(
        run_model.objects.using(alias).filter(task__isnull=True).order_by("pk")[:500]
    ):
        tasks = task_model.objects.using(alias).bulk_create(
            [
                task_model(
                    job_id=run.job_id,
                    origin=run.started_by,
                    commit_sha=run.commit_sha,
                    input_snapshot=run.input_snapshot,
                    scheduled_for=run.scheduled_for,
                    created_at=run.created_at,
                )
                for run in runs
            ]
        )
        for run, task in zip(runs, tasks, strict=True):
            run.task_id = task.pk
        run_model.objects.using(alias).bulk_update(runs, ["task"])


def backfill_agent_connections_v1(
    apps: Apps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Record the agent login each ready plan chose, so its cap counts it."""
    run_model = apps.get_model("runs", "Run")
    login_model = apps.get_model("connections", "AgentConnection")
    alias = schema_editor.connection.alias
    logins = set(login_model.objects.using(alias).values_list("pk", flat=True))
    runs = run_model.objects.using(alias).filter(
        execution_plan__ready=True,
        execution_plan__execution__connection_id__isnull=False,
    )
    changed = []
    for run in runs.iterator():
        login = run.execution_plan["execution"]["connection_id"]
        if login in logins:
            run.agent_connection_id = login
            changed.append(run)
    run_model.objects.using(alias).bulk_update(changed, ["agent_connection"], 500)


PLAN_LIMIT_SETTINGS_V1 = {
    "max_turns": "AGENT_MAX_TURNS",
    "max_tool_calls": "AGENT_MAX_TOOL_CALLS",
    "max_tool_result_chars": "AGENT_MAX_TOOL_RESULT_CHARS",
    "token_budget": "MODEL_RUN_TOKEN_BUDGET",
    "timeout_seconds": "RUN_TIMEOUT_SECONDS",
}


def picked_v1(values: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    """The known keys of a stored block, with defaults for missing ones."""
    values = values if isinstance(values, dict) else {}
    return {key: values.get(key, default) for key, default in defaults.items()}


def normalized_plan_v1(
    plan: dict[str, Any], fallback: dict[str, Any]
) -> dict[str, Any]:
    """One stored plan in the shape every reader now expects.

    Plans from older releases lack blocks added later. A run created without
    a plan keeps it as not ready, with its job and commit from the run.
    """
    agent = plan.get("agent") or {}
    limits = agent.get("limits") or {}
    model = plan.get("model")
    return {
        "execution": picked_v1(
            plan.get("execution"),
            {
                "backend": "pydantic",
                "label": "",
                "ready": True,
                "problem": "",
                "connection_id": None,
                "credential_key": None,
                "owner_id": None,
            },
        ),
        "ready": bool(plan.get("ready", False)),
        "agent": {
            "limits": {
                key: int(limits.get(key, getattr(settings, name)))
                for key, name in PLAN_LIMIT_SETTINGS_V1.items()
            },
            "tools": list(agent.get("tools") or []),
            "subagents": int(agent.get("subagents") or 0),
        },
        "job": picked_v1(
            plan.get("job") or fallback["job"],
            {"key": "", "skill_path": "", "config_hash": ""},
        ),
        "repository": picked_v1(
            plan.get("repository") or fallback["repository"],
            {"full_name": "", "config_sha": ""},
        ),
        "runner": picked_v1(
            plan.get("runner"),
            {
                "image": settings.RUNNER_IMAGE,
                "network_policy": "none",
                "collect_changes": False,
            },
        ),
        "pull_request": (
            picked_v1(plan["pull_request"], {"base": "", "run_ci": False})
            if plan.get("pull_request")
            else None
        ),
        "issues": (
            picked_v1(plan["issues"], {"max": 0, "labels": []})
            if plan.get("issues")
            else None
        ),
        "model": (
            picked_v1(
                model,
                {
                    "connection_id": 0,
                    "connection_name": "",
                    "provider": "",
                    "family": "",
                    "default_model": "",
                    "allowed_models": [],
                },
            )
            if isinstance(model, dict)
            else None
        ),
        "capabilities": [
            picked_v1(
                capability,
                {
                    "binding": "",
                    "kind": "",
                    "required": True,
                    "requested_tools": [],
                    "connection_id": None,
                    "connection_name": None,
                    "effective_tools": [],
                    "authorized": False,
                    "reasons": [],
                    "missing": None,
                    "external": None,
                    "tool_rules": {},
                },
            )
            for capability in plan.get("capabilities") or []
        ],
    }


def normalize_execution_plans_v1(
    apps: Apps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Rewrite every stored plan in the current shape."""
    run_model = apps.get_model("runs", "Run")
    alias = schema_editor.connection.alias
    runs = run_model.objects.using(alias).select_related(
        "job__repository", "task__repository"
    )
    changed = []
    for run in runs.iterator(chunk_size=500):
        job = run.job
        repository = job.repository if job else run.task.repository
        fallback = {
            "job": {
                "key": job.key if job else "",
                "skill_path": job.skill_path if job else "",
                "config_hash": job.config_hash if job else "",
            },
            "repository": {
                "full_name": repository.full_name if repository else "",
                "config_sha": run.commit_sha,
            },
        }
        normalized = normalized_plan_v1(run.execution_plan or {}, fallback)
        if normalized != run.execution_plan:
            run.execution_plan = normalized
            changed.append(run)
    run_model.objects.using(alias).bulk_update(changed, ["execution_plan"], 500)
