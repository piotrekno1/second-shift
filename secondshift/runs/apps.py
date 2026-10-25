from importlib import import_module

from django.apps import AppConfig


class RunsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "secondshift.runs"

    def ready(self) -> None:
        import_module("secondshift.runs.signals")
