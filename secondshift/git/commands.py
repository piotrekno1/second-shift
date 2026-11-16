"""One way to run git, with nothing from outside the call changing what it does.

Git reads settings from the system, from the user's home folder and from the
repository. It also reads its environment, and it can run hooks and other
programs that settings name. Here it gets:

- no system or global settings, and no settings from the environment
- no hooks and no file system monitor, whatever the repository's settings say
- a small environment: PATH, a home folder, the C locale, and the proxy and
  certificate variables a fetch needs
- no password prompts, so a missing credential fails instead of waiting

Git's own output can name local folders, and a fetch's can hold a URL. So a
GitError keeps it for the server log and never puts it in its message.
"""

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

DEFAULT_TIMEOUT_SECONDS = 120
# Settings that would let a repository run a program of its choosing.
ISOLATED_SETTINGS = ("core.hooksPath=/dev/null", "core.fsmonitor=false")
# What a fetch through a proxy or with a private certificate authority needs.
NETWORK_VARIABLES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "all_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "GIT_SSL_CAINFO",
    "GIT_SSL_CAPATH",
    "TMPDIR",
)


class GitError(Exception):
    """Git failed, timed out or could not start.

    The message is safe to show. Git's own output is kept in `stderr` for the
    server log only.
    """

    def __init__(
        self,
        arguments: Sequence[str],
        returncode: int | None,
        stderr: bytes = b"",
        *,
        timed_out: bool = False,
    ) -> None:
        # The first argument that is not an option, so no path reaches the message.
        command = next((item for item in arguments if not item.startswith("-")), "")
        outcome = "timed out" if timed_out else "failed"
        super().__init__(" ".join(filter(None, ("git", command, outcome))) + ".")
        self.command = command
        # None when git timed out or could not start.
        self.returncode = returncode
        self.stderr = stderr.decode(errors="replace").strip()
        self.timed_out = timed_out


@dataclass(frozen=True)
class Invocation:
    """The command line and environment for one git process."""

    command: list[str]
    environment: dict[str, str]


def environment(home: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    inherited = {
        name: os.environ[name] for name in NETWORK_VARIABLES if name in os.environ
    }
    return {
        **inherited,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        # Git also reads ignore and attribute files from the home folder.
        "HOME": str(home),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        # No helper may answer a password prompt with stored credentials.
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        **(extra or {}),
    }


def invocation(
    repository: Path | None,
    arguments: Sequence[str],
    *,
    settings: Sequence[str] = (),
    extra_environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Invocation:
    """How to run git in repository, or outside any repository when None.

    The home folder is the repository unless one is given, so no file from
    the server user's home folder is read.
    """
    location = ["-C", str(repository)] if repository is not None else []
    options = [
        part for setting in (*ISOLATED_SETTINGS, *settings) for part in ("-c", setting)
    ]
    folder = home or repository or Path(os.devnull)
    return Invocation(
        command=["git", *location, *options, *arguments],
        environment=environment(folder, extra_environment),
    )


def run(
    repository: Path | None,
    *arguments: str,
    settings: Sequence[str] = (),
    extra_environment: Mapping[str, str] | None = None,
    home: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    stdout: IO[bytes] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run git and return its result, whatever its exit code.

    Raises GitError only when git times out or cannot start. Pass stdout to
    write a large output to a file instead of holding it in memory.
    """
    prepared = invocation(
        repository,
        arguments,
        settings=settings,
        extra_environment=extra_environment,
        home=home,
    )
    try:
        return subprocess.run(
            prepared.command,
            env=prepared.environment,
            stdout=stdout if stdout is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise GitError(arguments, None, timed_out=True) from error
    except OSError as error:
        raise GitError(arguments, None, str(error).encode()) from error


def require(
    result: subprocess.CompletedProcess[bytes], arguments: Sequence[str]
) -> subprocess.CompletedProcess[bytes]:
    if result.returncode:
        raise GitError(arguments, result.returncode, result.stderr or b"")
    return result


def output_bytes(
    repository: Path | None,
    *arguments: str,
    settings: Sequence[str] = (),
    extra_environment: Mapping[str, str] | None = None,
    home: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> bytes:
    """Git's output. Raises GitError unless git succeeds."""
    result = run(
        repository,
        *arguments,
        settings=settings,
        extra_environment=extra_environment,
        home=home,
        timeout=timeout,
    )
    return require(result, arguments).stdout


def output(
    repository: Path | None,
    *arguments: str,
    settings: Sequence[str] = (),
    extra_environment: Mapping[str, str] | None = None,
    home: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Git's output as text, without the trailing newline."""
    content = output_bytes(
        repository,
        *arguments,
        settings=settings,
        extra_environment=extra_environment,
        home=home,
        timeout=timeout,
    )
    return content.decode().strip()
