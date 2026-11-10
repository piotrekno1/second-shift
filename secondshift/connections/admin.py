from django.contrib import admin

from secondshift.connections.models import Connection


@admin.register(Connection)
class ConnectionAdmin(admin.ModelAdmin):
    list_display = (
        "provider",
        "display_name",
        "binding_key",
        "kind",
        "workspace",
        "status",
        "expires_at",
    )
    list_filter = ("provider", "kind", "status", "auth_strategy")
    search_fields = (
        "display_name",
        "binding_key",
        "workspace__name",
        "external_account_id",
    )
    exclude = ("access_token", "refresh_token", "delivery_url")
    readonly_fields = ("created_at", "updated_at")
