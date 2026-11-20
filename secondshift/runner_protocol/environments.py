"""Named sandbox environments, each an image the server admin chose.

The admin sets one env var per environment, such as
SECONDSHIFT_RUNNER_ENVIRONMENT_NODE=ghcr.io/org/runner-node@sha256:...
A job names only the environment. The image always comes from the server.
SECONDSHIFT_RUNNER_IMAGE is the default environment. SecondShift also
supplies fullstack, which an admin can replace or turn off with an empty value.
"""

import re
from collections.abc import Mapping

ENVIRONMENT_PREFIX = "SECONDSHIFT_RUNNER_ENVIRONMENT_"
DEFAULT_ENVIRONMENT = "default"
# Env var names hold letters, digits and underscores, so names do too.
ENVIRONMENT_NAME_PATTERN = r"[a-z][a-z0-9_]{0,39}"
PINNED_IMAGE = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}")
# Python 3.13, Node 24, git and make, for jobs that run a project's checks.
# To update, run `docker buildx imagetools inspect nikolaik/python-nodejs:python3.13-nodejs24`
# and copy the index digest.
BUILT_IN_ENVIRONMENTS = {
    "fullstack": (
        "nikolaik/python-nodejs"
        "@sha256:36165c0fb95721c983def24f998b5f4922216b4a85b639c5a4cdabdcb64d6c91"
    ),
}


class EnvironmentConfigError(ValueError):
    pass


def environment_variable(name: str) -> str:
    """The env var that sets an environment's image."""
    return f"{ENVIRONMENT_PREFIX}{name.upper()}"


def configured_environments(
    environ: Mapping[str, str], *, require_digest: bool
) -> dict[str, str]:
    """The named environments, by name, built-in ones included. Leaves out
    the default. An empty value turns a built-in environment off.
    """
    environments = dict(BUILT_IN_ENVIRONMENTS)
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
        if not image and name in BUILT_IN_ENVIRONMENTS:
            del environments[name]
            continue
        if not image:
            raise EnvironmentConfigError(f"{key} is empty. Set an image or remove it.")
        if require_digest and not PINNED_IMAGE.fullmatch(image):
            raise EnvironmentConfigError(
                f"{key} must name an image pinned by digest, "
                "such as ghcr.io/org/runner-node@sha256:<digest>."
            )
        environments[name] = image
    return dict(sorted(environments.items()))
