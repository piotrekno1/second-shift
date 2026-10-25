"""Data migrations for model ownership transfers without changing row IDs."""

from django.apps.registry import Apps
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

MODEL_APP_MOVES = (
    ("integrations", "connections", ("connection", "secret", "oauthauthorization")),
    ("automations", "jobs", ("job", "capabilityrequest", "jobgrant")),
    ("automations", "runs", ("run", "runevent")),
)


def transfer_content_types(
    apps: Apps, schema_editor: BaseDatabaseSchemaEditor, *, reverse: bool
) -> None:
    content_types = apps.get_model("contenttypes", "ContentType").objects.using(
        schema_editor.connection.alias
    )
    for old_app, new_app, models in MODEL_APP_MOVES:
        source, target = (new_app, old_app) if reverse else (old_app, new_app)
        for model in models:
            existing = content_types.filter(app_label=source, model=model)
            if not existing.exists():
                continue
            if content_types.filter(app_label=target, model=model).exists():
                raise RuntimeError(
                    f"Cannot move {source}.{model}: {target}.{model} already exists."
                )
            existing.update(app_label=target)


def move_content_types(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    transfer_content_types(apps, schema_editor, reverse=False)


def restore_content_types(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    transfer_content_types(apps, schema_editor, reverse=True)
