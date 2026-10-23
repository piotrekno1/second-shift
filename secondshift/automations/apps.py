from importlib import import_module

from django.apps import AppConfig


class AutomationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "secondshift.automations"

    def ready(self) -> None:
        import_module("secondshift.automations.signals")
