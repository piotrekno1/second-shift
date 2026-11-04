from django.utils import timezone

from secondshift.connections.models import Connection


def record_check(
    connection: Connection, ok: bool, message: str, **details: object
) -> None:
    """Keep the latest check or delivery result, shown on the Connections page.

    details add to it, such as a source's per-feature access. Never secrets.
    """
    connection.metadata = {
        **connection.metadata,
        "last_check": {
            "ok": ok,
            "message": message,
            "checked_at": timezone.now().isoformat(),
            **details,
        },
    }
    connection.save(update_fields=["metadata", "updated_at"])
