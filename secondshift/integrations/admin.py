from django.contrib import admin

from secondshift.integrations.forms import SecretAdminForm
from secondshift.integrations.models import Connection, Secret


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


@admin.register(Secret)
class SecretAdmin(admin.ModelAdmin):
    form = SecretAdminForm
    list_display = ("name", "workspace", "description", "last_used_at", "updated_at")
    search_fields = ("name", "workspace__name", "description")
    readonly_fields = ("last_used_at", "created_at", "updated_at")
