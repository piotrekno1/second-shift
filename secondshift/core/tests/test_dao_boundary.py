"""Model queries live in each app's dao.py.

Other modules call the dao helpers. They never use a model manager, and they
never query through a reverse relation. Reading a relation with `.all()` is
fine, since it uses prefetched rows.
"""

import ast
from pathlib import Path

from django.apps import apps

import secondshift

PACKAGE = Path(secondshift.__file__).parent
SKIPPED_PARTS = {"tests", "migrations", "benchmarks"}
ALLOWED_FILES = {
    # The only code that changes a run's status. See its module docstring.
    "runs/transitions.py",
}
# Data migrations call migration_helpers with historical models, which a dao
# cannot use.
QUERY_MODULES = {"dao.py", "migration_helpers.py"}
# Queryset methods that dicts and sets do not have, so a match is a query.
QUERY_METHODS = {
    "aexists",
    "afirst",
    "aget",
    "aggregate",
    "annotate",
    "bulk_create",
    "count",
    "create",
    "delete",
    "exclude",
    "exists",
    "filter",
    "first",
    "get",
    "get_or_create",
    "in_bulk",
    "last",
    "order_by",
    "prefetch_related",
    "select_related",
    "update_or_create",
    "values_list",
}


def reverse_accessors() -> set[str]:
    """The names of every reverse relation on the project's models."""
    return {
        relation.get_accessor_name()
        for config in apps.get_app_configs()
        if config.name.startswith("secondshift.")
        for model in config.get_models()
        for relation in model._meta.related_objects
    }


def modules() -> list[Path]:
    return [
        path
        for path in sorted(PACKAGE.rglob("*.py"))
        if not set(path.relative_to(PACKAGE).parts) & SKIPPED_PARTS
        and path.name not in QUERY_MODULES
        and path.relative_to(PACKAGE).as_posix() not in ALLOWED_FILES
    ]


def uses_manager(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "objects"


def queries_relation(node: ast.AST, accessors: set[str]) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in QUERY_METHODS
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr in accessors
    )


def queries(tree: ast.AST, accessors: set[str]) -> list[int]:
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if uses_manager(node) or queries_relation(node, accessors)
    )


def test_model_queries_live_in_dao_modules(db: object) -> None:
    accessors = reverse_accessors()
    found = [
        f"{path.relative_to(PACKAGE)}:{line}"
        for path in modules()
        for line in queries(ast.parse(path.read_text()), accessors)
    ]
    assert found == []


def test_the_check_finds_manager_and_reverse_relation_queries() -> None:
    tree = ast.parse(
        "Run.objects.filter(pk=1)\n"
        "job.runs.filter(status='queued')\n"
        "job.runs.all()\n"
        "manifest.jobs.values()\n"
    )
    assert queries(tree, {"runs", "jobs"}) == [1, 2]
