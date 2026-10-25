from django.contrib import admin
from django.http import HttpRequest
from django.utils import timezone

from secondshift.jobs.models import CapabilityRequest, Job, JobGrant


class CapabilityRequestInline(admin.TabularInline):
    model = CapabilityRequest
    extra = 0
    can_delete = False
    readonly_fields = (
        "binding",
        "kind",
        "requested_tools",
        "required",
        "created_at",
        "updated_at",
    )


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "repository",
        "cron",
        "timezone",
        "enabled",
        "next_run_at",
    )
    list_filter = ("enabled", "timezone")
    search_fields = ("name", "key", "repository__full_name", "skill_path")
    readonly_fields = ("config_hash", "created_at", "updated_at")
    inlines = [CapabilityRequestInline]


@admin.register(CapabilityRequest)
class CapabilityRequestAdmin(admin.ModelAdmin):
    list_display = ("binding", "kind", "job", "required", "grant_status")
    list_filter = ("kind", "required")
    search_fields = ("binding", "job__name", "job__repository__full_name")
    readonly_fields = (
        "job",
        "binding",
        "kind",
        "requested_tools",
        "required",
        "created_at",
        "updated_at",
    )

    @admin.display(description="Grant")
    def grant_status(self, request: CapabilityRequest) -> str:
        grant = getattr(request, "grant", None)
        return grant.status if grant else "unbound"


@admin.register(JobGrant)
class JobGrantAdmin(admin.ModelAdmin):
    list_display = ("capability_request", "connection", "status", "reviewed_at")
    list_filter = ("status", "connection__kind")
    search_fields = (
        "capability_request__binding",
        "capability_request__job__name",
        "connection__display_name",
    )
    readonly_fields = ("reviewed_by", "reviewed_at", "created_at", "updated_at")

    def save_model(
        self,
        request: HttpRequest,
        obj: JobGrant,
        form: object,
        change: bool,
    ) -> None:
        if obj.status in {JobGrant.Status.APPROVED, JobGrant.Status.DENIED}:
            obj.reviewed_by = request.user
            obj.reviewed_at = timezone.now()
        else:
            obj.reviewed_by = None
            obj.reviewed_at = None
        super().save_model(request, obj, form, change)
