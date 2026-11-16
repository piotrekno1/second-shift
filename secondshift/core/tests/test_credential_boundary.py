"""Code reads a connection's secret through connections.credential_store.

Setting the fields stays direct. Reading them anywhere else would skip the
store, so a secret kept in an external manager would look empty.
"""

import ast
from pathlib import Path

import secondshift
from secondshift.connections.credential_store import Secret

PACKAGE = Path(secondshift.__file__).parent
SKIPPED_PARTS = {"tests", "migrations", "benchmarks"}
ALLOWED_FILES = {
    "connections/credential_store.py",
    # OAuth refresh reads and rotates the stored tokens. OAuth connections
    # keep their tokens in the database.
    "connections/mcp/oauth.py",
}
FIELDS = {secret.value for secret in Secret}


def direct_reads(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in FIELDS
        and isinstance(node.ctx, ast.Load)
    ]


def test_secrets_are_read_through_the_credential_store() -> None:
    offenders = [
        f"{path.relative_to(PACKAGE)}:{line}"
        for path in sorted(PACKAGE.rglob("*.py"))
        if not set(path.relative_to(PACKAGE).parts) & SKIPPED_PARTS
        and path.relative_to(PACKAGE).as_posix() not in ALLOWED_FILES
        for line in direct_reads(path)
    ]

    assert offenders == []
