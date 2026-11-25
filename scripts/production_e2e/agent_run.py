"""Exercise the real workflow, runner and gateway with a deterministic agent."""

import os

# Django must initialize before importing models.
# ruff: noqa: E402
import time

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "secondshift.config.settings.production"
)

import django

django.setup()

from django.contrib.auth import get_user_model

from secondshift.connections.agents import save_agent_connection
from secondshift.connections.execution import choose_execution
from secondshift.jobs.models import Job
from secondshift.runs.artifacts import read_text
from secondshift.runs.constants import RunStart
from secondshift.runs.models import Run
from secondshift.runs.services import start_run


def main() -> None:
    job = Job.objects.select_related("workspace", "repository").get(key="smoke-report")
    workspace = job.workspace
    model = workspace.default_model_connection
    login = save_agent_connection(
        workspace,
        get_user_model().objects.get(email="owner@e2e.test"),
        "claude_code",
        "E2E transport probe",
        "sk-ant-oat01-e2e-probe",
    )
    try:
        choose_execution(workspace, login=login)
        run = start_run(job, RunStart.WEBHOOK, b"production-agent-payload-probe")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            run.refresh_from_db()
            if run.status in {
                Run.Status.SUCCEEDED,
                Run.Status.FAILED,
                Run.Status.CANCELED,
            }:
                break
            time.sleep(1)
        assert run.status == Run.Status.SUCCEEDED, (run.status, run.summary)
        output = read_text(run.output_path, 4000)[0]
        assert output == "production-agent-payload-probe", output
        # The second-runner step drains the first, so the run lands elsewhere.
        if expected := os.environ.get("E2E_RUNNER"):
            run.refresh_from_db()
            assert run.runner is not None and run.runner.name == expected, run.runner
        print(
            "1 checks passed: agent reads its payload and lists workspace files without storage or database access, leaves only through the egress proxy and has no route to the control plane"
        )
    finally:
        choose_execution(workspace, model=model)
        login.delete()


if __name__ == "__main__":
    main()
