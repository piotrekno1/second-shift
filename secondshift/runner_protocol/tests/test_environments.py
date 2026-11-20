import pytest

from secondshift.runner_protocol.environments import (
    EnvironmentConfigError,
    RunnerEnvironments,
    configured_environments,
    environment_variable,
)

NODE = "ghcr.io/org/runner-node@sha256:" + "a" * 64
PYTHON = "python@sha256:" + "b" * 64


def test_each_env_var_names_one_sandbox_and_one_names_the_default() -> None:
    environ = {
        "SECONDSHIFT_SANDBOX_NODE": NODE,
        "SECONDSHIFT_SANDBOX_GO_1_23": f" {NODE} ",
        "SECONDSHIFT_SANDBOX_PYTHON": PYTHON,
        "SECONDSHIFT_SANDBOX_DEFAULT": "Node",
        "PATH": "/usr/bin",
    }

    offered = configured_environments(environ, require_digest=True)

    assert offered == RunnerEnvironments(
        images={"go_1_23": NODE, "node": NODE, "python": PYTHON}, default="node"
    )
    assert environment_variable("go_1_23") == "SECONDSHIFT_SANDBOX_GO_1_23"


def test_default_means_whichever_sandbox_the_server_chose() -> None:
    offered = RunnerEnvironments(
        images={"python": PYTHON, "node": NODE}, default="node"
    )

    assert offered.offers("default") and offered.offers("python")
    assert not offered.offers("browser")
    assert offered.image("default") == NODE
    assert offered.image("python") == PYTHON
    assert offered.image("browser") is None


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("SECONDSHIFT_SANDBOX_", NODE, "starts with a letter"),
        ("SECONDSHIFT_SANDBOX_1NODE", NODE, "starts with a letter"),
        ("SECONDSHIFT_SANDBOX_NODE-22", NODE, "starts with a letter"),
        ("SECONDSHIFT_SANDBOX_NODE", " ", "is empty"),
        ("SECONDSHIFT_SANDBOX_NODE", "node:22", "pinned by digest"),
    ],
)
def test_a_server_refuses_a_bad_sandbox(key: str, value: str, message: str) -> None:
    environ = {key: value, "SECONDSHIFT_SANDBOX_DEFAULT": "node"}
    with pytest.raises(EnvironmentConfigError, match=message):
        configured_environments(environ, require_digest=True)


@pytest.mark.parametrize(
    ("default", "message"),
    [
        ("", "Set SECONDSHIFT_SANDBOX_DEFAULT"),
        ("browser", "SECONDSHIFT_SANDBOX_BROWSER is not set"),
    ],
)
def test_the_default_must_be_one_of_the_sandboxes(default: str, message: str) -> None:
    environ = {"SECONDSHIFT_SANDBOX_NODE": NODE, "SECONDSHIFT_SANDBOX_DEFAULT": default}
    with pytest.raises(EnvironmentConfigError, match=message):
        configured_environments(environ, require_digest=True)


def test_one_machine_may_use_a_tag() -> None:
    environ = {
        "SECONDSHIFT_SANDBOX_NODE": "node:22",
        "SECONDSHIFT_SANDBOX_DEFAULT": "node",
    }

    offered = configured_environments(environ, require_digest=False)

    assert offered.images == {"node": "node:22"}
