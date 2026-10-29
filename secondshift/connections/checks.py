from django.utils import timezone

from secondshift.connections.models import Connection


def record_check(connection: Connection, ok: bool, message: str) -> None:
    """Keep the latest check or delivery result, shown on the Connections page."""
    connection.metadata = {
        **connection.metadata,
        "last_check": {
            "ok": ok,
            "message": message,
            "checked_at": timezone.now().isoformat(),
        },
    }
    connection.save(update_fields=["metadata", "updated_at"])
