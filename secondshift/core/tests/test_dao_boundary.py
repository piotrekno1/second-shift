"""Model queries live in each app's dao.py.

Other modules call the dao helpers. They never use a model manager, never
pass a model to get_object_or_404, never refine a dao queryset, and never
query through a reverse relation. Reading a relation with `.all()` is
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


SHORTCUTS = {"get_object_or_404", "get_list_or_404"}
# Called on what a function returned, these refine a dao queryset outside the dao.
CHAINED_METHODS = {
    "afirst",
    "aggregate",
    "annotate",
    "exclude",
    "filter",
    "first",
    "order_by",
    "prefetch_related",
    "select_related",
    "values_list",
}


def model_names() -> set[str]:
    return {
        model.__name__
        for config in apps.get_app_configs()
        if config.name.startswith("secondshift.")
        for model in config.get_models()
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


def slices_relation(node: ast.AST, accessors: set[str]) -> bool:
    """`job.runs.all()[:5]` runs a query with a limit, even after a prefetch."""
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "all"
        and isinstance(node.value.func.value, ast.Attribute)
        and node.value.func.value.attr in accessors
    )


def looks_up_model(node: ast.AST, models: set[str]) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in SHORTCUTS
        and bool(node.args)
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id in models
    )


def refines_a_query(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in CHAINED_METHODS
        and isinstance(node.func.value, ast.Call)
    )


def queries(tree: ast.AST, accessors: set[str], models: set[str]) -> list[int]:
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if uses_manager(node)
        or queries_relation(node, accessors)
        or slices_relation(node, accessors)
        or looks_up_model(node, models)
        or refines_a_query(node)
    )


def test_model_queries_live_in_dao_modules(db: object) -> None:
    accessors = reverse_accessors()
    models = model_names()
    found = [
        f"{path.relative_to(PACKAGE)}:{line}"
        for path in modules()
        for line in queries(ast.parse(path.read_text()), accessors, models)
    ]
    assert found == []


def test_the_check_finds_manager_and_reverse_relation_queries() -> None:
    tree = ast.parse(
        "Run.objects.filter(pk=1)\n"
        "job.runs.filter(status='queued')\n"
        "job.runs.all()\n"
        "manifest.jobs.values()\n"
        "get_object_or_404(Run, pk=1)\n"
        "get_object_or_404(workspace_runs(1), pk=1)\n"
        "workspace_runs(1).filter(status='queued')\n"
        "job.runs.all()[:5]\n"
    )
    assert queries(tree, {"runs", "jobs"}, {"Run"}) == [1, 2, 5, 7, 8]
