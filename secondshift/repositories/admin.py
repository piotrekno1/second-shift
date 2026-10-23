from django.contrib import admin

from secondshift.repositories.models import Repository


@admin.register(Repository)
class RepositoryAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "source_type",
        "workspace",
        "enabled",
        "removed_at",
        "default_branch",
        "last_synced_at",
    )
    list_filter = ("source_type", "enabled", "removed_at")
    search_fields = ("full_name", "local_path", "workspace__name")
    readonly_fields = ("created_at", "updated_at", "last_synced_at")
