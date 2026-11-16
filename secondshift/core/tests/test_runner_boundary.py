"""The runner, the control plane and the agent image stay apart.

The runner never imports control plane apps, and the control plane never
imports the runner. They meet through runner_protocol, which imports neither.
The agent image is a third part. Its files use only the standard library,
because the image has nothing else installed.
"""

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

import secondshift

PACKAGE = Path(secondshift.__file__).parent
SKIPPED_PARTS = {"tests", "migrations", "benchmarks"}
AGENT_IMAGE = "secondshift.agent.image"
# Names every part may use: settings and launcher names.
SHARED = {"secondshift.config.constants"}
# What each part may import from SecondShift, besides SHARED.
ALLOWED = {
    "runner_protocol": ("secondshift.runner_protocol", AGENT_IMAGE),
    "runner": (
        "secondshift.runner",
        "secondshift.runner_protocol",
        "secondshift.agent",
    ),
    "agent": ("secondshift.agent",),
}
# config starts every process, so it imports each one's entry point.
NOT_CONTROL_PLANE = {*ALLOWED, "config"}


def source_files(folder: Path) -> Iterator[Path]:
    for path in sorted(folder.rglob("*.py")):
        if not SKIPPED_PARTS.intersection(path.relative_to(PACKAGE).parts):
            yield path


def imported_modules(path: Path) -> Iterator[str]:
    """Each module a file imports, including names that may be submodules."""
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


def test_each_part_imports_only_what_it_may() -> None:
    problems = [
        f"{relative(path)} imports {module}"
        for part, allowed in ALLOWED.items()
        for path in source_files(PACKAGE / part)
        for module in secondshift_imports(path)
        if module not in SHARED
        and not any(within(module, package) for package in allowed)
    ]
    assert problems == []


def test_the_control_plane_never_imports_the_runner() -> None:
    control_plane = [
        path
        for path in source_files(PACKAGE)
        if path.relative_to(PACKAGE).parts[0] not in NOT_CONTROL_PLANE
    ]
    problems = [
        f"{relative(path)} imports {module}"
        for path in control_plane
        for module in secondshift_imports(path)
        if within(module, "secondshift.runner")
    ]
    assert problems == []


def test_the_agent_image_uses_only_the_standard_library() -> None:
    problems = [
        f"{relative(path)} imports {module}"
        for path in source_files(PACKAGE / "agent" / "image")
        for module in imported_modules(path)
        if module.split(".")[0] not in sys.stdlib_module_names
        and not within(module, AGENT_IMAGE)
    ]
    assert problems == []
