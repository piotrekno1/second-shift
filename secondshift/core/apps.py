from typing import Any

from django.apps import AppConfig
from django.core.management import call_command
from django.db.models.signals import post_migrate


def create_cache_tables(using: str, **kwargs: Any) -> None:
    """Create the database cache table, so every migrate leaves it ready."""
    call_command("createcachetable", database=using, verbosity=0)


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "secondshift.core"

    def ready(self) -> None:
        # Django sends post_migrate only for apps with models, and core has
        # none. Listen to every app. The command skips a table that exists.
        post_migrate.connect(create_cache_tables, dispatch_uid="create_cache_tables")
