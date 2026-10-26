"""Import credential-free Git bundles into the runner's owned store."""

import base64
import fcntl
import re
import tempfile
from pathlib import Path

from django.conf import settings

from secondshift.config.constants import (
    GITHUB_REPOSITORY_PATTERN,
    MAX_REPOSITORY_BUNDLE_BYTES,
)
from secondshift.repositories.materialization import (
    RepositoryMaterializationError,
    git_in,
)
from secondshift.runner.protocol import RunnerProtocolError

COMMIT_PATTERN = r"[a-f0-9]{40}"


def import_bundle(repository: str, commit_sha: str, bundle: str) -> None:
    if (
        not re.fullmatch(GITHUB_REPOSITORY_PATTERN, repository)
        or any(part in {".", ".."} for part in repository.split("/"))
        or not re.fullmatch(COMMIT_PATTERN, commit_sha)
    ):
        raise RunnerProtocolError("Invalid repository identity or commit.")
    if len(bundle) > (MAX_REPOSITORY_BUNDLE_BYTES * 4 // 3 + 4):
        raise RunnerProtocolError("Repository bundle is too large.")
    try:
        data = base64.b64decode(bundle, validate=True)
    except ValueError as error:
        raise RunnerProtocolError("Invalid repository bundle.") from error
    if len(data) > MAX_REPOSITORY_BUNDLE_BYTES:
        raise RunnerProtocolError("Repository bundle is too large.")
    root = settings.RUNNER_REPOSITORY_ROOT.resolve()
    candidate = root / repository
    target = candidate.resolve()
    if not target.is_relative_to(root) or candidate.is_symlink():
        raise RunnerProtocolError("Invalid repository store path.")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    locks = root / ".locks"
    locks.mkdir(exist_ok=True, mode=0o700)
    try:
        with (locks / (repository.replace("/", "__") + ".lock")).open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
                path = Path(temporary) / "source.bundle"
                path.write_bytes(data)
                # Validate the advertised commit before mutating the owned store.
                heads = git_in(target.parent, "bundle", "list-heads", str(path))
                if heads != f"{commit_sha} refs/heads/snapshot":
                    raise RunnerProtocolError(
                        "Repository bundle does not match its commit."
                    )
                if not target.exists():
                    git_in(target.parent, "init", "--bare", str(target))
                if git_in(target, "rev-parse", "--is-bare-repository") != "true":
                    raise RunnerProtocolError("Repository store must be bare.")
                git_in(target, "bundle", "verify", str(path))
                git_in(
                    target,
                    "fetch",
                    str(path),
                    f"refs/heads/snapshot:refs/secondshift/{commit_sha}",
                )
                git_in(target, "cat-file", "-e", f"{commit_sha}^{{commit}}")

    except RepositoryMaterializationError as error:
        raise RunnerProtocolError("Could not import repository bundle.") from error
