import pytest

from secondshift.runner.protocol import (
    CommandRequest,
    RunnerSessionSpec,
)


def spec() -> RunnerSessionSpec:
    return RunnerSessionSpec(
        run_id=42,
        session_key="0123456789abcdef0123456789abcdef",
        repository="example/project",
        commit_sha="a" * 40,
        image="secondshift-runner@sha256:abc",
        source_sha256="d" * 64,
    )


@pytest.mark.parametrize("cwd", ["/workspace", "../other", "src/../../other"])
def test_command_working_directory_cannot_escape_workspace(cwd: str) -> None:
    with pytest.raises(ValueError, match="inside the workspace"):
        CommandRequest("call-1", ("pwd",), cwd=cwd)


@pytest.mark.parametrize("repository", ["project", "owner/..", "owner/./project"])
def test_repository_name_must_be_a_normalized_relative_path(repository: str) -> None:
    with pytest.raises(ValueError, match="normalized owner/name"):
        RunnerSessionSpec(
            run_id=42,
            session_key="0123456789abcdef0123456789abcdef",
            repository=repository,
            commit_sha="a" * 40,
            image="secondshift-runner:test",
            source_sha256="d" * 64,
        )


@pytest.mark.parametrize("session_key", ["", "A" * 32, "0" * 31, "g" * 32])
def test_session_key_must_be_lowercase_hex(session_key: str) -> None:
    with pytest.raises(ValueError, match="session_key"):
        RunnerSessionSpec(
            run_id=42,
            session_key=session_key,
            repository="example/project",
            commit_sha="a" * 40,
            image="secondshift-runner:test",
            source_sha256="d" * 64,
        )


@pytest.mark.parametrize("history_sha256", ["../x", "E" * 64, "e" * 63])
def test_history_archive_must_be_named_by_its_sha256(history_sha256: str) -> None:
    with pytest.raises(ValueError, match="history_sha256"):
        RunnerSessionSpec(**{**spec().__dict__, "history_sha256": history_sha256})
