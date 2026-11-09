"""Versioned data operations used by generated runs migrations.

Keep these functions independent of current models and job configuration.
"""

from django.apps.registry import Apps
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
