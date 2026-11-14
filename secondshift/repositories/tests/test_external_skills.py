import hashlib
import io
import os
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Any

import pytest

from secondshift.repositories.external_skills import (
    ExternalSkillError,
    resolve_external_skill,
    skill_snapshot,
)
from secondshift.repositories.snapshots import SnapshotError
from secondshift.repositories.sources import (
    prune_source_cache,
    skill_mirrors_root,
    skill_snapshots_root,
)
from secondshift.repositories.tests.test_local import run_git
from secondshift.workspaces.models import Workspace

REPOSITORY = "vendor/skills"
PATH = "skills/triage/SKILL.md"


def head(path: Path, ref: str = "HEAD") -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", ref], text=True
    ).strip()


@pytest.fixture
def vendor(tmp_path: Path, settings: Any) -> Path:
    """A public skills repository, served from a local folder instead of GitHub."""
    work = tmp_path / "work"
    work.mkdir()
    run_git(work, "init", "-b", "main")
    run_git(work, "config", "user.email", "test@secondshift.local")
    run_git(work, "config", "user.name", "SecondShift Test")
    folder = work / "skills/triage"
    (folder / "scripts").mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: triage\nallowed-tools: mcp__linear__create_issue\n---\n# Triage\n"
    )
    (folder / "scripts/collect.py").write_text("print('ok')\n")
    (work / "README.md").write_text("Not part of the skill.\n")
    run_git(work, "add", ".")
    run_git(work, "commit", "-m", "first")
    run_git(work, "tag", "v1")
    served = tmp_path / "github" / "vendor" / "skills.git"
    served.parent.mkdir(parents=True)
    run_git(tmp_path, "clone", "--bare", "--quiet", str(work), str(served))
    # GitHub serves any reachable commit by SHA. Local Git needs this.
    run_git(served, "config", "uploadpack.allowAnySHA1InWant", "true")
    settings.EXTERNAL_SKILLS_BASE_URL = f"file://{tmp_path / 'github'}"
    settings.SOURCE_CACHE_ROOT = tmp_path / "cache"
    settings.EXTERNAL_SKILLS_ENABLED = True
    return work


def push(work: Path, served: Path) -> None:
    run_git(work, "push", "--quiet", str(served), "main")


def test_the_default_branch_is_pinned_to_its_commit(
    workspace: Workspace, vendor: Path
) -> None:
    skill = resolve_external_skill(workspace.pk, REPOSITORY, PATH)

    assert skill.commit_sha == head(vendor)
    assert skill.text.startswith("---\nname: triage")
    assert skill.file_count == 2
    assert skill.size_bytes > 0


def folder_sha256(folder: Path) -> str:
    """The digest as the docs compute it, from files on disk."""
    files = sorted(
        str(path.relative_to(folder)) for path in folder.rglob("*") if path.is_file()
    )
    listing = "".join(
        f"{hashlib.sha256((folder / name).read_bytes()).hexdigest()}  {name}\n"
        for name in files
    )
    return f"sha256:{hashlib.sha256(listing.encode()).hexdigest()}"


def test_the_digest_hashes_the_skill_folder(workspace: Workspace, vendor: Path) -> None:
    skill = resolve_external_skill(workspace.pk, REPOSITORY, PATH)

    assert skill.digest == folder_sha256(vendor / "skills/triage")


def test_the_digest_ignores_commits_outside_the_skill(
    workspace: Workspace, vendor: Path, tmp_path: Path
) -> None:
    first = resolve_external_skill(workspace.pk, REPOSITORY, PATH)
    (vendor / "README.md").write_text("Changed.\n")
    run_git(vendor, "commit", "-am", "readme")
    push(vendor, tmp_path / "github/vendor/skills.git")

    second = resolve_external_skill(workspace.pk, REPOSITORY, PATH, "main")

    assert second.commit_sha != first.commit_sha
    assert second.digest == first.digest


def test_a_different_digest_is_refused(workspace: Workspace, vendor: Path) -> None:
    actual = resolve_external_skill(workspace.pk, REPOSITORY, PATH).digest

    with pytest.raises(ExternalSkillError, match=f"has digest {actual}"):
        resolve_external_skill(
            workspace.pk, REPOSITORY, PATH, head(vendor), f"sha256:{'0' * 64}"
        )
    assert (
        resolve_external_skill(
            workspace.pk, REPOSITORY, PATH, head(vendor), actual
        ).digest
        == actual
    )


def test_branches_tags_and_commits_resolve(
    workspace: Workspace, vendor: Path, tmp_path: Path
) -> None:
    first = head(vendor)
    (vendor / "skills/triage/SKILL.md").write_text("# Second\n")
    run_git(vendor, "commit", "-am", "second")
    push(vendor, tmp_path / "github/vendor/skills.git")

    assert (
        resolve_external_skill(workspace.pk, REPOSITORY, PATH, "main").text
        == "# Second"
    )
    assert (
        resolve_external_skill(workspace.pk, REPOSITORY, PATH, "v1").commit_sha == first
    )
    assert (
        resolve_external_skill(workspace.pk, REPOSITORY, PATH, first).commit_sha
        == first
    )


def test_a_cached_commit_needs_no_fetch(
    workspace: Workspace, vendor: Path, tmp_path: Path
) -> None:
    commit = resolve_external_skill(workspace.pk, REPOSITORY, PATH).commit_sha
    (tmp_path / "github/vendor/skills.git").rename(tmp_path / "gone.git")

    assert (
        resolve_external_skill(workspace.pk, REPOSITORY, PATH, commit).commit_sha
        == commit
    )
    with pytest.raises(ExternalSkillError, match="must be public, or readable"):
        resolve_external_skill(workspace.pk, REPOSITORY, PATH, "main")


@pytest.mark.parametrize(
    ("repository", "path", "message"),
    [
        ("vendor", PATH, "owner/name"),
        ("vendor/../x", PATH, "owner/name"),
        ("https://github.com/vendor/skills", PATH, "owner/name"),
        (REPOSITORY, "../SKILL.md", "inside its repository"),
        (REPOSITORY, "/skills/SKILL.md", "inside its repository"),
        (REPOSITORY, "skills/./triage/SKILL.md", "inside its repository"),
        (REPOSITORY, "skills/triage/README.md", "SKILL.md"),
    ],
)
def test_references_are_checked(
    workspace: Workspace, vendor: Path, repository: str, path: str, message: str
) -> None:
    with pytest.raises(ExternalSkillError, match=message):
        resolve_external_skill(workspace.pk, repository, path)


@pytest.mark.parametrize(
    "ref", ["--upload-pack=touch /tmp/x", "main..other", "a" * 39 + "~", "x@{1}"]
)
def test_only_plain_refs_reach_git(
    workspace: Workspace, vendor: Path, ref: str
) -> None:
    with pytest.raises(ExternalSkillError, match="branch, a tag or a full commit"):
        resolve_external_skill(workspace.pk, REPOSITORY, PATH, ref)


def test_a_missing_skill_or_repository_is_explained(
    workspace: Workspace, vendor: Path
) -> None:
    with pytest.raises(ExternalSkillError, match="is not a file in vendor/skills"):
        resolve_external_skill(workspace.pk, REPOSITORY, "skills/other/SKILL.md")
    with pytest.raises(ExternalSkillError, match="Could not fetch HEAD from"):
        resolve_external_skill(workspace.pk, "vendor/missing", PATH)


def test_a_server_can_turn_external_skills_off(
    workspace: Workspace, vendor: Path, settings: Any
) -> None:
    settings.EXTERNAL_SKILLS_ENABLED = False

    with pytest.raises(ExternalSkillError, match="does not run skills"):
        resolve_external_skill(workspace.pk, REPOSITORY, PATH)
    with pytest.raises(ExternalSkillError, match="does not run skills"):
        skill_snapshot(workspace.pk, REPOSITORY, PATH, "a" * 40)


def test_the_snapshot_holds_only_the_skill_folder(
    workspace: Workspace, vendor: Path
) -> None:
    commit = resolve_external_skill(workspace.pk, REPOSITORY, PATH).commit_sha

    snapshot = skill_snapshot(workspace.pk, REPOSITORY, PATH, commit)

    with tarfile.open(snapshot.path, mode="r:gz") as archive:
        assert sorted(archive.getnames()) == ["SKILL.md", "scripts/collect.py"]
    assert skill_snapshot(workspace.pk, REPOSITORY, PATH, commit) == snapshot


def test_a_snapshot_fetches_a_commit_it_has_not_seen(
    workspace: Workspace, vendor: Path
) -> None:
    snapshot = skill_snapshot(workspace.pk, REPOSITORY, PATH, head(vendor))

    data = snapshot.path.read_bytes()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        assert "SKILL.md" in archive.getnames()


def test_a_skill_with_links_cannot_run(
    workspace: Workspace, vendor: Path, tmp_path: Path
) -> None:
    (vendor / "skills/triage/secrets").symlink_to("/etc/passwd")
    run_git(vendor, "add", ".")
    run_git(vendor, "commit", "-m", "link")
    push(vendor, tmp_path / "github/vendor/skills.git")

    with pytest.raises(SnapshotError, match="Not allowed: secrets"):
        skill_snapshot(workspace.pk, REPOSITORY, PATH, head(vendor))


def test_unused_skills_leave_the_cache(workspace: Workspace, vendor: Path) -> None:
    commit = resolve_external_skill(workspace.pk, REPOSITORY, PATH).commit_sha
    snapshot = skill_snapshot(workspace.pk, REPOSITORY, PATH, commit)
    mirror = next(skill_mirrors_root().glob("*.git"))
    assert prune_source_cache().bytes == 0

    old = time.time() - 31 * 24 * 60 * 60
    os.utime(snapshot.path, (old, old))
    os.utime(mirror, (old, old))
    pruned = prune_source_cache()

    assert (pruned.snapshots, pruned.mirrors) == (1, 1)
    assert not list(skill_snapshots_root().iterdir())
    assert not mirror.exists()
