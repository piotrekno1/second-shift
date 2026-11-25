"""Names the runs app shares between its modules and with other apps."""

from enum import StrEnum

EXECUTE_RUN_WORKFLOW = "secondshift.execute_run"
SCHEDULED_JOB_WORKFLOW = "secondshift.scheduled_job"
RETRY_NOTIFICATION_WORKFLOW = "secondshift.retry_notification"
RETRY_PULL_REQUEST_WORKFLOW = "secondshift.retry_pull_request"
RETRY_ISSUES_WORKFLOW = "secondshift.retry_issues"
RETRY_HAND_BACK_WORKFLOW = "secondshift.retry_hand_back"

# Run event metadata keys. A subscription agent's progress is one event, kept
# up to date and left out of the timeline. A workspace tool call's event names
# its command call, so a retried step records it once.
AGENT_PROGRESS = "agent_progress"
COMMAND_CALL = "command_call"


class RunStart(StrEnum):
    """What started a run."""

    SCHEDULE = "schedule"
    MANUAL = "manual"
    WEBHOOK = "webhook"
    MCP = "mcp"


class RunSandbox(StrEnum):
    """What a run records as its sandbox."""

    PENDING = "pending-runner-service"
    ISOLATED_RUNNER = "isolated-runner"
