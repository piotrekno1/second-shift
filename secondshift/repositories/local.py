import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml
from django.db import transaction

from secondshift.automations.manifest import RepositoryManifest, load_manifest
from secondshift.automations.sync import sync_manifest
from secondshift.core.models import Workspace
from secondshift.repositories.models import Repository


class LocalRepositoryError(ValueError):
    pass


@dataclass(frozen=True)
class LocalRepositoryMetadata:
    root: Path
    manifest: Path
    revision: str
    branch: str
    repository_name: str


def git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    if completed.returncode:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise LocalRepositoryError(message or "Git could not inspect the repository.")
    return completed.stdout.strip()


def inspect_local_repository(raw_path: str) -> LocalRepositoryMetadata:
    candidate = Path(raw_path).expanduser().resolve()
    if not candidate.is_dir():
        raise LocalRepositoryError("The selected path is not a directory.")

    try:
        root = Path(git(candidate, "rev-parse", "--show-toplevel")).resolve()
    except (LocalRepositoryError, subprocess.TimeoutExpired) as error:
        raise LocalRepositoryError(
            "The selected path is not a Git repository."
        ) from error

    manifest_path = root / ".secondshift.yaml"
    if not manifest_path.is_file():
        raise LocalRepositoryError("The repository has no root .secondshift.yaml file.")
    if manifest_path.is_symlink():
        raise LocalRepositoryError("The manifest cannot be a symbolic link.")
    resolved_manifest = manifest_path.resolve()
    if not resolved_manifest.is_relative_to(root):
        raise LocalRepositoryError("The manifest must be stored inside the repository.")

    try:
        manifest = load_manifest(resolved_manifest)
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise LocalRepositoryError(f"Invalid .secondshift.yaml: {error}") from error
    tracked_paths = [resolved_manifest]
    validate_skill_files(root, manifest, tracked_paths)
    relative_paths = [str(path.relative_to(root)) for path in tracked_paths]
    try:
        git(root, "ls-files", "--error-unmatch", "--", *relative_paths)
        dirty = git(root, "status", "--porcelain", "--", *relative_paths)
        revision = git(root, "rev-parse", "HEAD")
    except (LocalRepositoryError, subprocess.TimeoutExpired) as error:
        raise LocalRepositoryError(
            "The manifest and skill files must be committed before scheduling them."
        ) from error
    if dirty:
        raise LocalRepositoryError(
            "Commit changes to the manifest and skill files before adding the repository."
        )

    try:
        branch = git(root, "symbolic-ref", "--short", "HEAD")
    except (LocalRepositoryError, subprocess.TimeoutExpired):
        branch = "detached"
    return LocalRepositoryMetadata(
        root=root,
        manifest=resolved_manifest,
        revision=revision,
        branch=branch,
        repository_name=local_repository_name(root),
    )


def validate_skill_files(
    root: Path,
    manifest: RepositoryManifest,
    tracked_paths: list[Path],
) -> None:
    for job in manifest.jobs.values():
        configured_skill = root / job.skill
        if configured_skill.is_symlink():
            raise LocalRepositoryError(f"Skill {job.skill} cannot be a symbolic link.")
        skill = configured_skill.resolve()
        if not skill.is_relative_to(root) or not skill.is_file():
            raise LocalRepositoryError(
                f"Skill {job.skill} does not resolve to a file inside the repository."
            )
        tracked_paths.append(skill)


def local_repository_name(root: Path) -> str:
    digest = hashlib.sha256(str(root).encode()).hexdigest()[:8]
    return f"local/{root.name}-{digest}"


@transaction.atomic
def attach_local_repository(workspace: Workspace, raw_path: str) -> Repository:
    metadata = inspect_local_repository(raw_path)
    repository, _ = Repository.objects.update_or_create(
        workspace=workspace,
        local_path=str(metadata.root),
        defaults={
            "source_type": Repository.SourceType.LOCAL,
            "full_name": metadata.repository_name,
            "default_branch": metadata.branch,
            "config_path": ".secondshift.yaml",
            "config_sha": metadata.revision,
            "enabled": True,
            "removed_at": None,
        },
    )
    sync_manifest(repository, metadata.manifest)
    return repository
