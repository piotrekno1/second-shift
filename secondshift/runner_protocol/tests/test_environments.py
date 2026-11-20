import pytest

from secondshift.runner_protocol.environments import (
    BUILT_IN_ENVIRONMENTS,
    EnvironmentConfigError,
    configured_environments,
    environment_variable,
)

NODE = "ghcr.io/org/runner-node@sha256:" + "a" * 64


def test_each_env_var_names_one_environment() -> None:
    environ = {
        "SECONDSHIFT_RUNNER_ENVIRONMENT_NODE": NODE,
        "SECONDSHIFT_RUNNER_ENVIRONMENT_GO_1_23": f" {NODE} ",
        "SECONDSHIFT_RUNNER_IMAGE": "python:3.13-slim",
        "PATH": "/usr/bin",
    }

    assert configured_environments(environ, require_digest=True) == {
        "fullstack": BUILT_IN_ENVIRONMENTS["fullstack"],
        "go_1_23": NODE,
        "node": NODE,
    }
    assert environment_variable("go_1_23") == "SECONDSHIFT_RUNNER_ENVIRONMENT_GO_1_23"


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("SECONDSHIFT_RUNNER_ENVIRONMENT_DEFAULT", NODE, "SECONDSHIFT_RUNNER_IMAGE"),
        ("SECONDSHIFT_RUNNER_ENVIRONMENT_", NODE, "starts with a letter"),
        ("SECONDSHIFT_RUNNER_ENVIRONMENT_1NODE", NODE, "starts with a letter"),
        ("SECONDSHIFT_RUNNER_ENVIRONMENT_NODE-22", NODE, "starts with a letter"),
        ("SECONDSHIFT_RUNNER_ENVIRONMENT_NODE", " ", "is empty"),
        ("SECONDSHIFT_RUNNER_ENVIRONMENT_NODE", "node:22", "pinned by digest"),
    ],
)
def test_a_server_refuses_a_bad_environment(key: str, value: str, message: str) -> None:
    with pytest.raises(EnvironmentConfigError, match=message):
        configured_environments({key: value}, require_digest=True)


def test_one_machine_may_use_a_tag() -> None:
    environ = {"SECONDSHIFT_RUNNER_ENVIRONMENT_NODE": "node:22"}

    assert configured_environments(environ, require_digest=False) == {
        "fullstack": BUILT_IN_ENVIRONMENTS["fullstack"],
        "node": "node:22",
    }


def test_a_server_has_the_fullstack_environment_without_config() -> None:
    assert configured_environments({}, require_digest=True) == BUILT_IN_ENVIRONMENTS


def test_an_admin_replaces_or_turns_off_a_built_in_environment() -> None:
    key = "SECONDSHIFT_RUNNER_ENVIRONMENT_FULLSTACK"

    assert configured_environments({key: NODE}, require_digest=True) == {
        "fullstack": NODE
    }
    assert configured_environments({key: " "}, require_digest=True) == {}
