from django.contrib.auth import get_user_model

from secondshift.automations.models import Job
from secondshift.automations.scheduling import schedule_name, synchronize_schedules
from secondshift.core.services import create_workspace
from secondshift.repositories.models import Repository


class FakeDBOSClient:
    def __init__(self) -> None:
        self.applied: list[dict[str, object]] = []
        self.deleted: list[str] = []
        self.destroyed = False

    def apply_schedules(self, schedules: list[dict[str, object]]) -> None:
        self.applied = schedules

    def list_schedules(self, **kwargs: object) -> list[dict[str, str]]:
        return [
            {"schedule_name": str(self.applied[0]["schedule_name"])},
            {"schedule_name": "secondshift-job-orphan"},
        ]

    def delete_schedule(self, name: str) -> None:
        self.deleted.append(name)

    def destroy(self) -> None:
        self.destroyed = True


def test_synchronize_schedules_applies_enabled_jobs_and_removes_orphans(
    db,
    monkeypatch,
) -> None:
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace,
        full_name="example/project",
        enabled=True,
    )
    job = Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="review",
        name="Review",
        skill_path=".agents/skills/review/SKILL.md",
        cron="0 9 * * 1",
        timezone="Europe/Warsaw",
        config_hash="abc",
        enabled=True,
    )
    client = FakeDBOSClient()
    monkeypatch.setattr(
        "secondshift.automations.scheduling.new_dbos_client",
        lambda: client,
    )

    count = synchronize_schedules()

    assert count == 1
    assert client.applied[0]["schedule_name"] == schedule_name(job.pk)
    assert client.applied[0]["context"] == {"job_id": job.pk}
    assert client.applied[0]["cron_timezone"] == "Europe/Warsaw"
    assert client.deleted == ["secondshift-job-orphan"]
    assert client.destroyed is True
