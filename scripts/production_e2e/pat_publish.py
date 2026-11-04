"""Publish a real git patch through the PAT source saved by the browser probe."""

# Django must initialize before importing models.
# ruff: noqa: E402
import os
import tempfile
from pathlib import Path

import django

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "secondshift.config.settings.production"
)
django.setup()

from django.utils import timezone

from secondshift.github.app import configured
from secondshift.repositories.models import Repository
from secondshift.repositories.pull_requests import (
    DraftPullRequest,
    publish_pull_request,
)


def main() -> None:
    assert not configured()
    repository = Repository.objects.select_related("github_connection").get(
        full_name="acme/e2e-fixture",
        removed_at__isnull=True,
    )
    assert repository.github_connection.auth_strategy == "api_key"
    with tempfile.TemporaryDirectory() as directory:
        patch = Path(directory) / "changes.patch"
        patch.write_text(
            "diff --git a/pat-probe.txt b/pat-probe.txt\n"
            "new file mode 100644\n--- /dev/null\n+++ b/pat-probe.txt\n"
            "@@ -0,0 +1 @@\n+PAT publication probe\n"
        )
        opened = publish_pull_request(
            repository,
            repository.config_sha,
            patch,
            DraftPullRequest(
                branch="secondshift/pat-probe",
                base="main",
                title="PAT probe",
                body="Production deployment test",
                message="PAT probe",
                committed_at=timezone.now(),
            ),
        )
    assert opened.number == 77
    print(
        "1 checks passed: PAT pushes a real branch and opens a draft without App configuration"
    )


if __name__ == "__main__":
    main()
