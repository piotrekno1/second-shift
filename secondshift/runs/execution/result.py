"""What an agent produced, from either backend."""

from typing import Any, Self

from pydantic import BaseModel, ConfigDict

from secondshift.config.constants import SUMMARY_MAX_CHARS
from secondshift.runs.artifacts import StoredOutput, store_run_text
from secondshift.runs.models import Run, RunFailure
from secondshift.runs.reporting.failures import StageFailure

FULL_REPORT_NOTE = "\n[... full report stored as a run artifact ...]"


class AgentResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    succeeded: bool
    summary: str
    output: StoredOutput | None = None
    failure: RunFailure = RunFailure.OTHER
    reason: str = ""
    details: dict[str, Any] = {}

    @property
    def exit_code(self) -> int:
        return 0 if self.succeeded else 1

    @classmethod
    def success(
        cls, run: Run, report: str, details: dict[str, Any] | None = None
    ) -> Self:
        summary = report[:SUMMARY_MAX_CHARS]
        if len(report) > SUMMARY_MAX_CHARS:
            summary += FULL_REPORT_NOTE
        return cls(
            succeeded=True,
            summary=summary,
            output=store_run_text(run, report),
            details=details or {},
        )

    @classmethod
    def stopped(
        cls,
        run: Run,
        failure: StageFailure,
        report: str = "",
        details: dict[str, Any] | None = None,
    ) -> Self:
        """A failed result that keeps whatever the agent wrote before it stopped."""
        return cls(
            succeeded=False,
            summary=failure.summary,
            output=store_run_text(run, report) if report else None,
            failure=failure.kind,
            reason=failure.reason,
            details={**(details or {}), "partial_report": bool(report)},
        )
