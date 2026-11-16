"""Server code starts git only through secondshift.git.commands."""

import ast
from pathlib import Path

import secondshift

PACKAGE = Path(secondshift.__file__).parent
SKIPPED_PARTS = {"tests", "migrations", "benchmarks"}


def starts_with_git(node: ast.AST) -> bool:
    """A literal command line like ["git", "status"]."""
    return (
        isinstance(node, ast.List | ast.Tuple)
        and len(node.elts) > 1
        and isinstance(node.elts[0], ast.Constant)
        and node.elts[0].value == "git"
    )


def test_no_other_module_builds_a_git_command_line() -> None:
    problems = [
        f"{path.relative_to(PACKAGE)}:{node.lineno}"
        for path in sorted(PACKAGE.rglob("*.py"))
        if not SKIPPED_PARTS.intersection(path.relative_to(PACKAGE).parts)
        and path.relative_to(PACKAGE).parts[0] != "git"
        for node in ast.walk(ast.parse(path.read_text()))
        if starts_with_git(node)
    ]
    assert problems == []
