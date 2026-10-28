from secondshift.jobs.limits import RunLimit
from secondshift.runs.execution.budgets import BUDGET_HEADLINE
from secondshift.runs.execution.deadlines import TIMEOUT_HEADLINE
from secondshift.runs.models import Run
from secondshift.runs.reporting.failures import (
    MODEL_PROVIDER_LABEL,
    describe_failure,
    model_provider_next_step,
)
from secondshift.runs.schemas import (
    RunDetailSchema,
    RunEventSchema,
    RunFailureSchema,
    RunFileName,
    RunFileSchema,
    RunSchema,
)


def run_limit(run: Run, limit: RunLimit) -> int | None:
    """A limit frozen into the run's plan. Older plans may not have it."""
    limits = (run.execution_plan.get("agent") or {}).get("limits") or {}
    value = limits.get(str(limit))
    return int(value) if isinstance(value, int) else None


def run_failure(run: Run) -> RunFailureSchema | None:
    if run.status == Run.Status.CANCELED:
        return RunFailureSchema(
            label="Canceled", reason=run.summary or "The run was canceled."
        )
    if run.status != Run.Status.FAILED:
        return None
    view = describe_failure(run.summary)
    connection_id, next_step = None, ""
    if view.label == MODEL_PROVIDER_LABEL:
        model = run.execution_plan.get("model") or {}
        if isinstance(model.get("connection_id"), int):
            connection_id = model["connection_id"]
        name = model.get("connection_name") or "the model connection"
        next_step = model_provider_next_step(view.reason, name)
    return RunFailureSchema(
        label=view.label,
        reason=view.reason,
        limit=view.label in (BUDGET_HEADLINE, TIMEOUT_HEADLINE),
        connection_id=connection_id,
        next_step=next_step,
    )


def serialize_run(run: Run) -> RunSchema:
    return RunSchema(
        id=run.id,
        job_id=run.job_id,
        job=run.job.name,
        repository_id=run.job.repository_id,
        repository=run.job.repository.display_name,
        status=run.status,
        scheduled_for=run.scheduled_for,
        started_at=run.started_at,
        finished_at=run.finished_at,
        summary=run.summary,
        plan_ready=bool(run.execution_plan.get("ready", False)),
        commit_sha=run.commit_sha,
        model=(run.execution_plan.get("model") or {}).get("default_model") or None,
        max_turns=run_limit(run, RunLimit.MAX_TURNS),
        token_budget=run_limit(run, RunLimit.TOKEN_BUDGET),
        model_requests=getattr(run, "model_requests", None) or 0,
        model_input_tokens=getattr(run, "model_input_tokens", None) or 0,
        model_output_tokens=getattr(run, "model_output_tokens", None) or 0,
        failure=run_failure(run),
        has_input=bool(run.input_path),
        has_output=bool(run.output_path),
        model_tokens=getattr(run, "model_tokens", None) or 0,
        model_cost_usd=(
            float(cost) if (cost := getattr(run, "model_cost_usd", None)) else None
        ),
        model_cost_partial=bool(getattr(run, "unpriced_requests", 0)),
    )


def run_file_path(run: Run, name: RunFileName) -> str:
    return run.input_path if name == "input" else run.output_path


def run_files(run: Run) -> list[RunFileSchema]:
    files: list[RunFileSchema] = []
    if run.input_path:
        files.append(
            RunFileSchema(
                name="input",
                label="Input",
                filename=f"run-{run.pk}-input.md",
                size_bytes=run.input_size_bytes,
                truncated=False,
            )
        )
    if run.output_path:
        files.append(
            RunFileSchema(
                name="output",
                label="Output",
                filename=f"run-{run.pk}-output.md",
                size_bytes=run.output_size_bytes,
                truncated=run.output_truncated,
            )
        )
    return files


def serialize_run_detail(run: Run) -> RunDetailSchema:
    model = run.execution_plan.get("model") or {}
    return RunDetailSchema(
        **serialize_run(run).model_dump(),
        job_key=str(run.input_snapshot.get("job_key") or run.job.key),
        skill_path=str(run.input_snapshot.get("skill_path") or run.job.skill_path),
        created_at=run.created_at,
        exit_code=run.exit_code,
        model_provider=model.get("connection_name"),
        output_excerpt=run.output_excerpt,
        output_truncated=run.output_truncated,
        output_size_bytes=run.output_size_bytes,
        events=[
            RunEventSchema(
                id=event.id,
                level=event.level,
                message=event.message,
                created_at=event.created_at,
            )
            for event in run.events.order_by("created_at", "pk")
        ],
        files=run_files(run),
    )
