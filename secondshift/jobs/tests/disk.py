from pathlib import Path

from secondshift.jobs.manifest import load_manifest
from secondshift.jobs.models import Job
from secondshift.jobs.sync import sync_jobs
from secondshift.repositories.models import Repository


def sync_manifest(repository: Repository, path: Path) -> list[Job]:
    """Sync from a manifest on disk. Skill paths resolve next to the manifest."""
    manifest = load_manifest(path)
    skills = {
        item.skill: skill_file.read_text()
        for item in manifest.jobs.values()
        if (skill_file := path.parent / item.skill).is_file()
    }
    return sync_jobs(repository, manifest, skills)
