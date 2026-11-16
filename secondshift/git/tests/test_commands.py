import subprocess
from pathlib import Path
from typing import Any

import pytest

from secondshift.git import commands
from secondshift.git.commands import GitError


def repository(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    return path


def commit(path: Path) -> subprocess.CompletedProcess[bytes]:
    (path / "file.txt").write_text("content\n")
    commands.output(path, "add", "file.txt")
    return commands.run(
        path,
        "commit",
        "--quiet",
        "--message=Change",
        extra_environment={
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@secondshift.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@secondshift.invalid",
        },
    )


def test_the_repository_cannot_run_hooks(tmp_path: Path) -> None:
    path = repository(tmp_path / "repository")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    marker = tmp_path / "ran"
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    subprocess.run(
        ["git", "-C", str(path), "config", "core.hooksPath", str(hooks)], check=True
    )
    default_hook = path / ".git" / "hooks" / "pre-commit"
    default_hook.write_text(hook.read_text())
    default_hook.chmod(0o755)

    assert commit(path).returncode == 0
    assert not marker.exists()


def test_the_repository_cannot_run_a_file_system_monitor(tmp_path: Path) -> None:
    path = repository(tmp_path / "repository")
    marker = tmp_path / "ran"
    monitor = tmp_path / "monitor"
    monitor.write_text(f"#!/bin/sh\ntouch {marker}\n")
    monitor.chmod(0o755)
    subprocess.run(
        ["git", "-C", str(path), "config", "core.fsmonitor", str(monitor)], check=True
    )

    commands.run(path, "status", "--porcelain")

    assert not marker.exists()


def test_global_and_environment_settings_are_ignored(
    monkeypatch: Any, tmp_path: Path
) -> None:
    path = repository(tmp_path / "repository")
    global_settings = tmp_path / "gitconfig"
    global_settings.write_text("[user]\n\tname = Server User\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_settings))
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".gitconfig").write_text("[user]\n\tname = Server User\n")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.email")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "server@example.com")
    # A variable like this one would point git at another repository.
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))

    for name in ("user.name", "user.email"):
        assert commands.run(path, "config", "--get", name).returncode == 1
    assert commands.output(path, "rev-parse", "--git-dir") == ".git"


def test_proxy_and_certificate_variables_reach_git(monkeypatch: Any) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
    monkeypatch.setenv("SSL_CERT_FILE", "/etc/ssl/company.pem")
    monkeypatch.setenv("SECONDSHIFT_SECRET_KEY", "not-for-git")

    environment = commands.invocation(None, ["version"]).environment

    assert environment["HTTPS_PROXY"] == "http://proxy.internal:3128"
    assert environment["SSL_CERT_FILE"] == "/etc/ssl/company.pem"
    assert "SECONDSHIFT_SECRET_KEY" not in environment


def test_the_home_folder_is_the_repository_unless_given(tmp_path: Path) -> None:
    assert commands.invocation(tmp_path, ["status"]).environment["HOME"] == str(
        tmp_path
    )
    other = tmp_path / "other"
    assert commands.invocation(tmp_path, ["status"], home=other).environment[
        "HOME"
    ] == str(other)


def test_a_failure_keeps_git_output_out_of_the_message(tmp_path: Path) -> None:
    path = repository(tmp_path / "repository")

    with pytest.raises(GitError) as caught:
        commands.output(path, "show", "missing-revision")

    assert str(caught.value) == "git show failed."
    assert str(tmp_path) not in str(caught.value)
    assert caught.value.returncode == 128
    assert "missing-revision" in caught.value.stderr
    assert not caught.value.timed_out


def test_a_timeout_is_a_git_error(monkeypatch: Any, tmp_path: Path) -> None:
    def slow(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired("git", 1)

    monkeypatch.setattr(commands.subprocess, "run", slow)

    with pytest.raises(GitError) as caught:
        commands.output(tmp_path, "fetch")

    assert caught.value.timed_out


def test_a_missing_git_is_a_git_error(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(GitError):
        commands.output(tmp_path, "status")


def test_output_can_go_to_a_file(tmp_path: Path) -> None:
    path = repository(tmp_path / "repository")
    commit(path)
    destination = tmp_path / "log.txt"

    with destination.open("wb") as stream:
        result = commands.run(path, "log", "--format=%s", stdout=stream)

    assert result.returncode == 0
    assert destination.read_text() == "Change\n"
