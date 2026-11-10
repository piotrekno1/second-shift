"""Turn workflow exceptions into run summaries a person can act on."""

from dataclasses import dataclass

from pydantic_ai.exceptions import ModelHTTPError

from secondshift.connections.errors import ExternalRequestError, ExternalToolError
from secondshift.github.errors import GitHubError
from secondshift.jobs.tool_rules import ToolRuleViolation
from secondshift.runner.protocol import RunnerProtocolError
from secondshift.runs.models import Run, RunFailure

STAGE_FAILURES: dict[str, str] = {
    RunFailure.STARTING: "The run could not start",
    RunFailure.CHECKOUT: "Couldn't check out the repository",
    RunFailure.SANDBOX: "Couldn't start the sandbox",
    RunFailure.SKILL: "The skill run failed",
    RunFailure.FINISHING: "The run could not be recorded",
}
MAX_DETAIL_CHARS = 300
TOOL_RULE_NEXT_STEP = (
    "The model used a tool in a way the job's access review does not allow. "
    "Change the restriction or the skill, then run again."
)

# Messages of these types come from SecondShift or Pydantic AI (whose run errors
# are RuntimeErrors) and never carry credentials, so they are shown. Anything
# else could echo arbitrary data, so only its type is shown.
SHOWN_ERROR_TYPES: tuple[type[BaseException], ...] = (
    GitHubError,
    RunnerProtocolError,
    RuntimeError,
)


def root_error(error: BaseException) -> BaseException:
    """The first underlying error, unwrapping exhausted step retries.

    Later retry attempts often fail for a different, misleading reason (for
    example, a conflict because the first attempt already ran), so the first
    attempt is the one that explains the failure.
    """
    attempts = getattr(error, "errors", None)
    if isinstance(attempts, list) and attempts:
        first = attempts[0]
        if isinstance(first, BaseException):
            return first
    return error


def provider_message(body: object) -> str:
    """The human message from an OpenAI- or Anthropic-shaped error body."""
    if not isinstance(body, dict):
        return ""
    nested = body.get("error")
    if isinstance(nested, dict) and isinstance(nested.get("message"), str):
        return nested["message"]
    message = body.get("message")
    return message if isinstance(message, str) else ""


def one_line(text: str) -> str:
    line = " ".join(text.split())
    if len(line) > MAX_DETAIL_CHARS:
        line = line[: MAX_DETAIL_CHARS - 1] + "…"
    return line


def provider_reply(error: ModelHTTPError, separator: str) -> str:
    """`HTTP 401: User not found.`, or `HTTP 401.` when the body says nothing."""
    message = provider_message(error.body)
    reply = f"HTTP {error.status_code}"
    return one_line(f"{reply}{separator}{message}" if message else f"{reply}.")


def failure_detail(error: BaseException) -> str:
    if isinstance(error, ModelHTTPError):
        return one_line(f"The model provider answered {provider_reply(error, ': ')}")
    if isinstance(error, ToolRuleViolation):
        # Names the argument and the rule, never the value the model sent.
        return one_line(f"Blocked by a tool restriction: {error}")
    if isinstance(error, SHOWN_ERROR_TYPES) and str(error).strip():
        return one_line(str(error))
    # SecondShift writes these for people. Request errors may quote the
    # remote service, so they stay hidden.
    shown_tool_error = isinstance(error, ExternalToolError) and not isinstance(
        error, ExternalRequestError
    )
    if shown_tool_error and str(error).strip():
        return one_line(str(error))
    return f"Unexpected {type(error).__name__}."


@dataclass(frozen=True)
class StageFailure:
    kind: RunFailure
    reason: str
    # One line naming the failed stage and the original cause.
    summary: str


def stage_failure(stage: RunFailure, error: BaseException) -> StageFailure:
    """What a workflow error at this stage means for the run."""
    root = root_error(error)
    detail = failure_detail(root)
    summary = f"{STAGE_FAILURES[stage]}: {detail}"
    if isinstance(root, ModelHTTPError):
        # model_provider_next_step reads the status from the start of this.
        reason = provider_reply(root, " · ")
        return StageFailure(RunFailure.MODEL_PROVIDER, reason, summary)
    if isinstance(root, ToolRuleViolation):
        return StageFailure(RunFailure.TOOL_RULE, one_line(str(root)), summary)
    return StageFailure(stage, detail, summary)


def failure_metadata(stage: str, error: BaseException) -> dict[str, object]:
    attempts = getattr(error, "errors", None)
    return {
        "stage": stage,
        "error_type": type(root_error(error)).__name__,
        "attempts": len(attempts) if isinstance(attempts, list) else 1,
    }


@dataclass(frozen=True)
class FailureView:
    """A failure split for compact display: where it happened, and why."""

    label: str
    reason: str


def describe_failure(run: Run) -> FailureView:
    kind = RunFailure(run.failure or RunFailure.OTHER)
    reason = " ".join(run.failure_reason.split())
    return FailureView(kind.label, reason or "No reason was recorded.")


def model_provider_next_step(reason: str, connection_name: str) -> str:
    """What to do about a model provider failure, in plain words."""
    if reason.startswith(("HTTP 401", "HTTP 403")):
        return (
            f"The API key for {connection_name} was refused. "
            "Update the key, then run again."
        )
    if reason.startswith("HTTP 429"):
        return "The provider is limiting requests. Wait, then run again."
    if reason.startswith("HTTP 5"):
        return "The provider had a problem. Run again later."
    return f"Check {connection_name}, then run again."
