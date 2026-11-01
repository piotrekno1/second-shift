from django.contrib import admin

from secondshift.triggers.models import JobTrigger, TriggerEvent


@admin.register(JobTrigger)
class JobTriggerAdmin(admin.ModelAdmin):
    list_display = ("job", "kind", "enabled", "secret_rotated_at")
    list_filter = ("kind", "enabled")
    # The secret is never shown. Rotate it from the dashboard.
    exclude = ("secret",)
    readonly_fields = ("job", "kind", "public_id", "secret_rotated_at")


@admin.register(TriggerEvent)
class TriggerEventAdmin(admin.ModelAdmin):
    list_display = ("trigger", "outcome", "reason", "run", "received_at")
    list_filter = ("outcome",)
    readonly_fields = ("trigger", "outcome", "reason", "run", "received_at")
