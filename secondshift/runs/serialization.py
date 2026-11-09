from secondshift.runner.agent_image.outcomes import cache_reads, usage_budget
from secondshift.runs.dao import run_events
from secondshift.runs.deliveries import retryable
from secondshift.runs.models import Run, RunDelivery, RunFailure
from secondshift.runs.reporting.failures import (
    TOOL_RULE_NEXT_STEP,
    describe_failure,
    model_provider_next_step,
)
from secondshift.runs.schemas import (
    RunChangesSchema,
    RunDeliverySchema,
    RunDetailSchema,
    RunEventSchema,
    RunFailureSchema,
    RunFileName,
    RunFileSchema,
    RunIssueSchema,
    RunSavedFileSchema,
    RunSchema,
    RunSkippedFileSchema,
)
from secondshift.workspaces.limits import RunLimit


def run_failure(run: Run) -> RunFailureSchema | None:
    if run.status == Run.Status.CANCELED:
        return RunFailureSchema(
            label="Canceled", reason=run.summary or "The run was canceled."
        )
    if run.status != Run.Status.FAILED:
        return None
    view = describe_failure(run)
    connection_id, next_step = None, ""
    if run.failure == RunFailure.MODEL_PROVIDER:
        model = run.plan.model
        connection_id = model.connection_id if model else None
        name = model.connection_name if model else "the model connection"
        next_step = model_provider_next_step(view.reason, name)
    elif run.failure == RunFailure.TOOL_RULE:
        next_step = TOOL_RULE_NEXT_STEP
    return RunFailureSchema(
        label=view.label,
        reason=view.reason,
        limit=run.failure in (RunFailure.BUDGET, RunFailure.TIMEOUT),
        connection_id=connection_id,
        next_step=next_step,
    )


def serialize_run(run: Run) -> RunSchema:
    api = run.plan.execution.backend.uses_model_api
    input_tokens = getattr(run, "model_input_tokens", None) or 0
    output_tokens = getattr(run, "model_output_tokens", None) or 0
    tokens = getattr(run, "model_tokens", None) or 0
    cached_tokens = getattr(run, "model_cache_read_tokens", None) or 0
    budget_tokens = getattr(run, "model_budget_tokens", None) or 0
    if not api:
        usage = getattr(run, "agent_usage", None) or {}
        # Codex input includes cached tokens; Claude reports cache input separately.
        input_tokens = sum(
            usage.get(key, 0)
            for key in (
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
        )
        output_tokens = usage.get("output_tokens", 0)
        cached_tokens = cache_reads(usage)
        # The CLI may report no usage at all. Show that as unknown, not zero.
        tokens = input_tokens + output_tokens if usage else None
        budget_tokens = usage_budget(usage) if usage else None
    return RunSchema(
        id=run.id,
        job_id=run.job_id,
        job=run.display_name,
        repository_id=run.repository.pk,
        repository=run.repository.display_name,
        status=run.status,
        scheduled_for=run.scheduled_for,
        started_by=run.started_by,
        started_at=run.started_at,
        finished_at=run.finished_at,
        summary=run.summary,
        plan_ready=run.plan.ready,
        commit_sha=run.commit_sha,
        model=run.plan.model.default_model if run.plan.model else None,
        # Agent CLIs do not report model calls, so a turn count would read 0.
        max_turns=run.plan.limit(RunLimit.MAX_TURNS) if api else None,
        token_budget=run.plan.limit(RunLimit.TOKEN_BUDGET),
        model_requests=getattr(run, "model_requests", None) or 0,
        model_input_tokens=input_tokens,
        model_output_tokens=output_tokens,
        model_cache_read_tokens=cached_tokens,
        failure=run_failure(run),
        has_input=bool(run.input_path),
        has_output=bool(run.output_path),
        model_tokens=tokens,
        model_budget_tokens=budget_tokens,
        model_usage_source="api" if api else "agent",
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


def serialize_delivery(delivery: RunDelivery) -> RunDeliverySchema:
    return RunDeliverySchema(
        id=delivery.pk,
        kind=delivery.kind,
        target=delivery.target,
        status=delivery.status,
        attempts=delivery.attempts,
        message=delivery.message,
        url=delivery.external_url,
        updated_at=delivery.updated_at,
        retryable=retryable(delivery),
    )


def run_changes(run: Run) -> RunChangesSchema | None:
    changes = (run.outputs or {}).get("changes") or {}
    if not changes.get("file_count"):
        return None
    return RunChangesSchema(
        file_count=changes["file_count"],
        files=changes.get("files", []),
        too_large=bool(changes.get("too_large")),
        available=bool(changes.get("path")),
        size_bytes=changes.get("size_bytes"),
    )


def saved_file_path(run: Run, name: str) -> str:
    """The stored path of one saved file, found by its exact name."""
    for item in (run.outputs or {}).get("files", []):
        if item.get("name") == name:
            return str(item["path"])
    return ""


def serialize_run_detail(run: Run) -> RunDetailSchema:
    model = run.plan.model
    return RunDetailSchema(
        **serialize_run(run).model_dump(),
        submission_input=str(run.input_snapshot.get("submission_input", "")),
        job_key=run.job_key,
        skill_path=run.plan.job.skill_path,
        created_at=run.created_at,
        exit_code=run.exit_code,
        model_provider=model.connection_name if model else None,
        execution_agent=run.plan.execution.label or None,
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
            for event in run_events(run)
        ],
        files=run_files(run),
        deliveries=[serialize_delivery(item) for item in run.deliveries.all()],
        issues=[
            RunIssueSchema(
                title=issue.title,
                number=issue.number,
                url=issue.url,
                already_open=issue.already_open,
            )
            for issue in run.issues.all()
        ],
        changes=run_changes(run),
        saved_files=[
            RunSavedFileSchema(name=item["name"], size_bytes=item["size_bytes"])
            for item in (run.outputs or {}).get("files", [])
        ],
        skipped_files=[
            RunSkippedFileSchema(**item)
            for item in (run.outputs or {}).get("skipped", [])
        ],
    )
