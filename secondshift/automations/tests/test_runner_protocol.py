import pytest

from secondshift.automations.runner_protocol import (
    CommandRequest,
    CommandResult,
    FakeRunnerBackend,
    RunnerProtocolError,
    RunnerSessionSpec,
)


def spec() -> RunnerSessionSpec:
    return RunnerSessionSpec(
        run_id=42,
        repository="example/project",
        commit_sha="a" * 40,
        image="secondshift-runner@sha256:abc",
    )


def test_fake_runner_deduplicates_a_retried_tool_call() -> None:
    executions: list[CommandRequest] = []

    def execute(
        session_spec: RunnerSessionSpec, request: CommandRequest
    ) -> CommandResult:
        executions.append(request)
        return CommandResult(request.call_id, 0, session_spec.commit_sha)

    runner = FakeRunnerBackend(execute)
    session_id = runner.start(spec())
    request = CommandRequest("turn-1-tool-1", ("git", "status", "--short"))

    first = runner.execute(session_id, request)
    retried = runner.execute(session_id, request)

    assert first == retried
    assert len(executions) == 1


def test_fake_runner_rejects_call_id_reuse_with_other_arguments() -> None:
    runner = FakeRunnerBackend()
    session_id = runner.start(spec())
    runner.execute(session_id, CommandRequest("call-1", ("git", "status")))

    with pytest.raises(RunnerProtocolError, match="different arguments"):
        runner.execute(session_id, CommandRequest("call-1", ("git", "diff")))


@pytest.mark.parametrize("cwd", ["/workspace", "../other", "src/../../other"])
def test_command_working_directory_cannot_escape_workspace(cwd: str) -> None:
    with pytest.raises(ValueError, match="inside the workspace"):
        CommandRequest("call-1", ("pwd",), cwd=cwd)


def test_runner_session_cannot_be_replaced_with_another_spec() -> None:
    runner = FakeRunnerBackend()
    runner.start(spec())

    with pytest.raises(RunnerProtocolError, match="different session spec"):
        runner.start(
            RunnerSessionSpec(
                run_id=42,
                repository="attacker/project",
                commit_sha="b" * 40,
                image="attacker/image",
            )
        )


@pytest.mark.parametrize("repository", ["project", "owner/..", "owner/./project"])
def test_repository_name_must_be_a_normalized_relative_path(repository: str) -> None:
    with pytest.raises(ValueError, match="normalized owner/name"):
        RunnerSessionSpec(
            run_id=42,
            repository=repository,
            commit_sha="a" * 40,
            image="secondshift-runner:test",
        )
