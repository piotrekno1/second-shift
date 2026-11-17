"""Named sandbox environments, each an image the server admin chose.

The admin sets one env var per environment, such as
SECONDSHIFT_RUNNER_ENVIRONMENT_NODE=ghcr.io/org/runner-node@sha256:...
A job names only the environment. The image always comes from the server.
SECONDSHIFT_RUNNER_IMAGE is the default environment.
"""

import re
from collections.abc import Mapping

ENVIRONMENT_PREFIX = "SECONDSHIFT_RUNNER_ENVIRONMENT_"
DEFAULT_ENVIRONMENT = "default"
# Env var names hold letters, digits and underscores, so names do too.
ENVIRONMENT_NAME_PATTERN = r"[a-z][a-z0-9_]{0,39}"
PINNED_IMAGE = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}")


class EnvironmentConfigError(ValueError):
    pass


def environment_variable(name: str) -> str:
    """The env var that sets an environment's image."""
    return f"{ENVIRONMENT_PREFIX}{name.upper()}"


def configured_environments(
    environ: Mapping[str, str], *, require_digest: bool
) -> dict[str, str]:
    """The named environments in the env, by name. Leaves out the default."""
    environments = {}
    for key, value in environ.items():
        if not key.startswith(ENVIRONMENT_PREFIX):
            continue
        name = key.removeprefix(ENVIRONMENT_PREFIX).lower()
        image = value.strip()
        if not re.fullmatch(ENVIRONMENT_NAME_PATTERN, name):
            raise EnvironmentConfigError(
                f"{key}: an environment name starts with a letter and has only "
                "letters, digits and underscores, up to 40 characters."
            )
        if name == DEFAULT_ENVIRONMENT:
            raise EnvironmentConfigError(
                f"{key}: set the default environment with SECONDSHIFT_RUNNER_IMAGE."
            )
        if not image:
            raise EnvironmentConfigError(f"{key} is empty. Set an image or remove it.")
        if require_digest and not PINNED_IMAGE.fullmatch(image):
            raise EnvironmentConfigError(
                f"{key} must name an image pinned by digest, "
                "such as ghcr.io/org/runner-node@sha256:<digest>."
            )
        environments[name] = image
    return dict(sorted(environments.items()))
