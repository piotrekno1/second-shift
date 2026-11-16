"""The runner and the control plane meet only through runner_protocol.

The runner never imports control plane apps. The control plane reaches the
runner through its client and a few modules listed here. runner_protocol
imports neither side, apart from the agent image's outcomes, which the image
ships on its own with only the standard library.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import secondshift

PACKAGE = Path(secondshift.__file__).parent
SKIPPED_PARTS = {"tests", "migrations", "benchmarks"}
AGENT_OUTCOMES = "secondshift.runner.agent_image.outcomes"
# What the runner may import from the rest of SecondShift.
RUNNER_IMPORTS = ("secondshift.runner", "secondshift.runner_protocol")
RUNNER_SHARED = {"secondshift.config.constants"}
# What the control plane may import from the runner.
CONTROL_PLANE_IMPORTS = {
    # The HTTP client for the runner's API.
    "secondshift.runner.client",
    # `secondshift up` builds the agent image on this machine.
    "secondshift.runner.images",
    AGENT_OUTCOMES,
    # The name sub-agents run under, which the control plane reports.
    "secondshift.runner.agent_image.subagent_hook",
}


def source_files(folder: Path) -> Iterator[Path]:
    for path in sorted(folder.rglob("*.py")):
        if not SKIPPED_PARTS.intersection(path.relative_to(PACKAGE).parts):
            yield path


def imported_modules(path: Path) -> Iterator[str]:
    """Each SecondShift module a file imports, including names of submodules."""
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module
            yield from (f"{node.module}.{alias.name}" for alias in node.names)


def within(module: str, package: str) -> bool:
    return module == package or module.startswith(f"{package}.")


def is_module(module: str) -> bool:
    relative = Path(*module.split(".")[1:])
    return (PACKAGE / relative).with_suffix(".py").exists() or (
        PACKAGE / relative / "__init__.py"
    ).exists()


def secondshift_imports(path: Path) -> Iterator[str]:
    for module in imported_modules(path):
        if within(module, "secondshift") and is_module(module):
            yield module


def relative(path: Path) -> str:
    return path.relative_to(PACKAGE).as_posix()


def test_runner_protocol_imports_neither_side() -> None:
    problems = [
        f"{relative(path)} imports {module}"
        for path in source_files(PACKAGE / "runner_protocol")
        for module in secondshift_imports(path)
        if not within(module, "secondshift.runner_protocol")
        and module != AGENT_OUTCOMES
    ]
    assert problems == []


def test_runner_imports_no_control_plane_code() -> None:
    problems = [
        f"{relative(path)} imports {module}"
        for path in source_files(PACKAGE / "runner")
        for module in secondshift_imports(path)
        if not any(within(module, package) for package in RUNNER_IMPORTS)
        and module not in RUNNER_SHARED
    ]
    assert problems == []


def test_control_plane_reaches_the_runner_through_its_protocol() -> None:
    control_plane = [
        path
        for path in source_files(PACKAGE)
        if path.relative_to(PACKAGE).parts[0]
        not in {"runner", "runner_protocol", "config"}
    ]
    problems = [
        f"{relative(path)} imports {module}"
        for path in control_plane
        for module in secondshift_imports(path)
        if within(module, "secondshift.runner")
        and not within(module, "secondshift.runner_protocol")
        and module not in CONTROL_PLANE_IMPORTS
        and not any(within(allowed, module) for allowed in CONTROL_PLANE_IMPORTS)
    ]
    assert problems == []
