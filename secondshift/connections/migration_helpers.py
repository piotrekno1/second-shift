"""Versioned data operations used by generated connections migrations.

Keep these functions independent of current models.
"""

from django.apps.registry import Apps
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def record_requested_scopes_v1(
    apps: Apps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Keep OAuth requirements apart from grants on connections saved before b10."""
    connection_model = apps.get_model("connections", "Connection")
    alias = schema_editor.connection.alias
    changed = []
    for connection in connection_model.objects.using(alias).filter(
        auth_strategy="oauth"
    ):
        oauth = connection.metadata.get("oauth", {})
        if "requested_scopes" in oauth:
            continue
        connection.metadata = {
            **connection.metadata,
            "oauth": {**oauth, "requested_scopes": list(connection.scopes)},
        }
        changed.append(connection)
    connection_model.objects.using(alias).bulk_update(changed, ["metadata"])
