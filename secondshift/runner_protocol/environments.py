"""Sandbox environments, each an image the server admin chose.

The runner's env sets one variable per environment, such as
SECONDSHIFT_SANDBOX_PYTHON_NODE=nikolaik/python-nodejs@sha256:...
SECONDSHIFT_SANDBOX_DEFAULT names the one jobs get when they name none.
A job names only the environment. The image always comes from the runner.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

SANDBOX_PREFIX = "SECONDSHIFT_SANDBOX_"
DEFAULT_SANDBOX = "SECONDSHIFT_SANDBOX_DEFAULT"
# What a job that names no environment asks for. The runner picks the image.
DEFAULT_ENVIRONMENT = "default"
# Env var names hold letters, digits and underscores, so names do too.
ENVIRONMENT_NAME_PATTERN = r"[a-z][a-z0-9_]{0,39}"
PINNED_IMAGE = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}")


class EnvironmentConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RunnerEnvironments:
    """The images a runner offers, by name, and the one jobs get by default."""

    images: dict[str, str]
    default: str

    def offers(self, name: str) -> bool:
        return name == DEFAULT_ENVIRONMENT or name in self.images

    def image(self, name: str) -> str | None:
        return self.images.get(self.default if name == DEFAULT_ENVIRONMENT else name)


def environment_variable(name: str) -> str:
    """The env var that sets an environment's image."""
    return f"{SANDBOX_PREFIX}{name.upper()}"


def configured_environments(
    environ: Mapping[str, str], *, require_digest: bool
) -> RunnerEnvironments:
    """The environments in the env. Raises when the default is not one of them."""
    images = {}
    for key, value in environ.items():
        if not key.startswith(SANDBOX_PREFIX) or key == DEFAULT_SANDBOX:
            continue
        name = key.removeprefix(SANDBOX_PREFIX).lower()
        image = value.strip()
        if not re.fullmatch(ENVIRONMENT_NAME_PATTERN, name):
            raise EnvironmentConfigError(
                f"{key}: a sandbox name starts with a letter and has only "
                "letters, digits and underscores, up to 40 characters."
            )
        if not image:
            raise EnvironmentConfigError(f"{key} is empty. Set an image or remove it.")
        if require_digest and not PINNED_IMAGE.fullmatch(image):
            raise EnvironmentConfigError(
                f"{key} must name an image pinned by digest, "
                "such as ghcr.io/org/runner-node@sha256:<digest>."
            )
        images[name] = image
    default = environ.get(DEFAULT_SANDBOX, "").strip().lower()
    if not default:
        raise EnvironmentConfigError(
            f"Set {DEFAULT_SANDBOX} to the sandbox jobs get when they name none, "
            "such as python."
        )
    if default not in images:
        raise EnvironmentConfigError(
            f"{DEFAULT_SANDBOX} is {default}, but {environment_variable(default)} "
            "is not set."
        )
    return RunnerEnvironments(images=dict(sorted(images.items())), default=default)
