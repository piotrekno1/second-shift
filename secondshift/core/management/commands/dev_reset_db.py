from datetime import timedelta

import djclick as click
from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from secondshift.automations.models import Job, Run, RunEvent
from secondshift.config.constants import SandboxRuntime
from secondshift.integrations.models import Connection
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace


@click.command()
@click.option(
    "--yes",
    is_flag=True,
    help="Confirm destroying every record in the development database.",
)
def command(yes: bool) -> None:
    """Reset the development database, migrate it, and load demo data."""
    if not settings.DEV:
        raise click.ClickException("dev_reset_db only runs with DJANGO_ENV=dev.")
    if not yes:
        raise click.ClickException(
            "dev_reset_db destroys the development database. Pass --yes to continue."
        )
    click.echo("Resetting the development database...")
    call_command("reset_db", interactive=False, close_sessions=True, verbosity=0)
    call_command("migrate", interactive=False, verbosity=0)
    call_command("bootstrap_dev")
    seed_demo_data()
    click.secho("Development demo data is ready.", fg="green")


def seed_demo_data() -> None:
    """Load safe, credential-free records for exploring the development UI."""
    workspace = Workspace.objects.get(slug="secondshift")
    now = timezone.now()

    connections = {
        provider: Connection.objects.create(
            workspace=workspace,
            provider=provider,
            kind=Connection.Kind.IDENTITY,
            display_name=display_name,
            external_account_id=f"demo-{provider}",
            status=Connection.Status.CONNECTED,
            scopes=scopes,
            auth_strategy=Connection.AuthStrategy.NONE,
            metadata={"demo": True},
        )
        for provider, display_name, scopes in (
            (Connection.Provider.GITHUB, "GitHub · demo", ["read:user", "repo"]),
            (Connection.Provider.CHATGPT, "ChatGPT · demo", ["agent:execute"]),
            (
                Connection.Provider.SLACK,
                "Slack · #engineering",
                ["chat:write", "channels:read"],
            ),
        )
    }

    repositories = {
        name: Repository.objects.create(
            workspace=workspace,
            github_connection=connections[Connection.Provider.GITHUB],
            external_id=f"demo-repository-{index}",
            full_name=name,
            clone_url=f"https://github.com/{name}.git",
            default_branch=branch,
            enabled=enabled,
            config_sha=commit_sha,
            last_synced_at=now - timedelta(minutes=index * 7),
        )
        for index, (name, branch, enabled, commit_sha) in enumerate(
            (
                ("wild-edge/wild-edge", "main", True, "8f3c9d2" * 5 + "8f3c9"),
                ("secondshift/examples", "main", True, "34a0be7" * 5 + "34a0b"),
                ("acme/platform-infra", "production", False, "c77b103" * 5 + "c77b1"),
            ),
            start=1,
        )
    }

    jobs = {
        key: Job.objects.create(
            workspace=workspace,
            repository=repositories[repository],
            key=key,
            name=name,
            skill_path=skill_path,
            cron=cron,
            timezone=job_timezone,
            enabled=False,
            config_hash=(key.encode().hex() + "0" * 64)[:64],
        )
        for key, name, repository, skill_path, cron, job_timezone in (
            (
                "weekly-infra",
                "Weekly infrastructure review",
                "wild-edge/wild-edge",
                ".agents/skills/infrastructure-review/SKILL.md",
                "0 9 * * 1",
                "Europe/Warsaw",
            ),
            (
                "dependency-sweep",
                "Dependency freshness sweep",
                "wild-edge/wild-edge",
                ".agents/skills/dependency-sweep/SKILL.md",
                "30 7 * * 2",
                "UTC",
            ),
            (
                "release-notes",
                "Release notes digest",
                "secondshift/examples",
                ".agents/skills/release-notes/SKILL.md",
                "0 16 * * 5",
                "America/New_York",
            ),
            (
                "incident-followup",
                "Incident follow-up",
                "acme/platform-infra",
                ".agents/skills/incident-review/SKILL.md",
                "0 */6 * * *",
                "UTC",
            ),
        )
    }

    run_specs = (
        (
            "weekly-infra",
            Run.Status.SUCCEEDED,
            1,
            "Reviewed 14 infrastructure changes; no critical drift found.",
            0,
        ),
        (
            "dependency-sweep",
            Run.Status.SUCCEEDED,
            2,
            "Found two dependency updates ready for review.",
            0,
        ),
        (
            "weekly-infra",
            Run.Status.FAILED,
            3,
            "The observability capability needs attention.",
            1,
        ),
        (
            "release-notes",
            Run.Status.SUCCEEDED,
            5,
            "Prepared a release digest for the engineering team.",
            0,
        ),
    )
    for index, (job_key, status, days_ago, summary, exit_code) in enumerate(
        run_specs, start=1
    ):
        finished_at = now - timedelta(days=days_ago)
        run = Run.objects.create(
            job=jobs[job_key],
            status=status,
            commit_sha=jobs[job_key].repository.config_sha,
            scheduled_for=finished_at - timedelta(minutes=4),
            started_at=finished_at - timedelta(minutes=4),
            finished_at=finished_at,
            sandbox_runtime=SandboxRuntime.GVISOR,
            exit_code=exit_code,
            summary=summary,
            dbos_workflow_id=f"demo-run-{index}",
        )
        RunEvent.objects.create(
            run=run,
            level="info" if status == Run.Status.SUCCEEDED else "error",
            message=summary,
            metadata={"demo": True},
        )
