"""Files the package reads at runtime must reach the container image."""

from fnmatch import fnmatch
from pathlib import Path

from secondshift.config.first_run import RUNNER_IMAGES

ROOT = Path(__file__).resolve().parents[3]


def sent_to_docker(path: str) -> bool:
    """Whether .dockerignore lets path into the build. The last match decides."""
    sent = True
    for line in (ROOT / ".dockerignore").read_text().splitlines():
        pattern = line.strip()
        if not pattern or pattern.startswith("#"):
            continue
        negated = pattern.startswith("!")
        pattern = pattern.removeprefix("!")
        if fnmatch(path, pattern) or fnmatch(path, pattern.removeprefix("**/")):
            sent = negated
    return sent


def test_the_default_sandbox_images_reach_the_image() -> None:
    path = RUNNER_IMAGES.relative_to(ROOT).as_posix()
    assert sent_to_docker(path)


def test_other_env_files_stay_out_of_the_image() -> None:
    assert not sent_to_docker(".env")
    assert not sent_to_docker("secondshift/config/production.env")
