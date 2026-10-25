"""Turn workflow exceptions into run summaries a person can act on."""

from dataclasses import dataclass
from typing import Literal

import requests
from pydantic_ai.exceptions import ModelHTTPError

from secondshift.automations.budgets import BUDGET_HEADLINE
from secondshift.automations.deadlines import TIMEOUT_HEADLINE
from secondshift.runner.protocol import RunnerProtocolError

RunStage = Literal["starting", "checkout", "sandbox", "skill", "finishing"]

STAGE_FAILURES: dict[str, str] = {
    "starting": "The run could not start",
    "checkout": "Couldn't check out the repository",
    "sandbox": "Couldn't start the sandbox",
    "skill": "The skill run failed",
    "finishing": "The run could not be recorded",
}
MAX_DETAIL_CHARS = 300

# Messages of these types come from SecondShift or Pydantic AI (whose run errors
# are RuntimeErrors) and never carry credentials, so they are shown. Anything
# else could echo arbitrary data, so only its type is shown.
SHOWN_ERROR_TYPES: tuple[type[BaseException], ...] = (
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


def failure_detail(error: BaseException) -> str:
    if isinstance(error, ModelHTTPError):
        message = provider_message(error.body)
        reply = f"The model provider answered HTTP {error.status_code}"
        return one_line(f"{reply}: {message}" if message else f"{reply}.")
    if isinstance(error, requests.Timeout):
        return "The runner did not answer in time."
    if isinstance(error, requests.ConnectionError):
        return "The runner service could not be reached."
    if isinstance(error, SHOWN_ERROR_TYPES) and str(error).strip():
        return one_line(str(error))
    return f"Unexpected {type(error).__name__}."


def failure_summary(stage: str, error: BaseException) -> str:
    """One line naming the failed stage and the original cause."""
    headline = STAGE_FAILURES.get(stage, "The run failed")
    return f"{headline}: {failure_detail(root_error(error))}"


def failure_metadata(stage: str, error: BaseException) -> dict[str, object]:
    attempts = getattr(error, "errors", None)
    return {
        "stage": stage,
        "error_type": type(root_error(error)).__name__,
        "attempts": len(attempts) if isinstance(attempts, list) else 1,
    }


STAGE_LABELS: dict[str, str] = {
    "starting": "Start",
    "checkout": "Checkout",
    "sandbox": "Sandbox",
    "skill": "Skill",
    "finishing": "Recording",
}
MODEL_PROVIDER_PREFIX = "The model provider answered "
LEGACY_WORKFLOW_PREFIX = "The durable workflow failed unexpectedly ("


@dataclass(frozen=True)
class FailureView:
    """A failure split for compact display: where it happened, and why."""

    label: str
    reason: str


def describe_failure(summary: str) -> FailureView:
    """Split a stored failure summary into a short label and reason.

    Works on summaries written by ``failure_summary`` and on older ones, so
    existing runs display the same way.
    """
    text = " ".join(summary.split())
    for headline in (TIMEOUT_HEADLINE, BUDGET_HEADLINE):
        if text.startswith(f"{headline}: "):
            return FailureView(headline, text[len(headline) + 2 :])
    for stage, headline in STAGE_FAILURES.items():
        if text.startswith(f"{headline}: "):
            detail = text[len(headline) + 2 :]
            if detail.startswith(MODEL_PROVIDER_PREFIX):
                # "HTTP 401: User not found." reads better as "HTTP 401 · User not found."
                return FailureView(
                    "Model provider",
                    detail[len(MODEL_PROVIDER_PREFIX) :].replace(": ", " · ", 1),
                )
            return FailureView(STAGE_LABELS[stage], detail)
    if text.startswith(LEGACY_WORKFLOW_PREFIX) and text.endswith(")."):
        return FailureView(
            "Workflow", f"Unexpected {text[len(LEGACY_WORKFLOW_PREFIX) : -2]}."
        )
    return FailureView("Run", text or "No reason was recorded.")
