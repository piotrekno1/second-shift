from django.contrib import admin
from django.http import FileResponse, Http404, HttpRequest
from django.shortcuts import get_object_or_404
from django.urls import URLPattern, URLResolver, path, reverse
from django.utils import timezone
from django.utils.html import format_html

from secondshift.automations.artifacts import artifact_storage
from secondshift.automations.models import (
    CapabilityRequest,
    Job,
    JobGrant,
    Run,
    RunEvent,
)


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


class RunEventInline(admin.TabularInline):
    model = RunEvent
    extra = 0
    can_delete = False
    readonly_fields = ("level", "message", "metadata", "created_at")


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


@admin.register(Run)
class RunAdmin(admin.ModelAdmin):
    list_display = (
        "job",
        "status",
        "scheduled_for",
        "sandbox_runtime",
        "finished_at",
    )
    list_filter = ("status", "sandbox_runtime")
    search_fields = ("job__name", "job__repository__full_name", "commit_sha")
    readonly_fields = (
        "job",
        "status",
        "commit_sha",
        "scheduled_for",
        "started_at",
        "finished_at",
        "sandbox_runtime",
        "exit_code",
        "dbos_workflow_id",
        "summary",
        "input_snapshot",
        "execution_plan",
        "output_excerpt",
        "full_output",
        "output_size_bytes",
        "output_truncated",
        "output_sha256",
        "created_at",
    )
    exclude = ("output_path",)
    inlines = [RunEventInline]

    def get_urls(self) -> list[URLPattern | URLResolver]:
        return [
            path(
                "<int:run_id>/output/",
                self.admin_site.admin_view(self.output_view),
                name="automations_run_output",
            ),
            *super().get_urls(),
        ]

    @admin.display(description="Full output")
    def full_output(self, run: Run) -> str:
        if not run.output_path:
            return "-"
        url = reverse("admin:automations_run_output", args=[run.pk])
        return format_html('<a href="{}">Download output.log</a>', url)

    def output_view(self, request: HttpRequest, run_id: int) -> FileResponse:
        run = get_object_or_404(Run, pk=run_id)
        if not self.has_view_permission(request, run) or not run.output_path:
            raise Http404
        return FileResponse(
            artifact_storage().open(run.output_path, "rb"),
            as_attachment=True,
            filename=f"run-{run.pk}-output.log",
            content_type="text/plain; charset=utf-8",
        )


@admin.register(RunEvent)
class RunEventAdmin(admin.ModelAdmin):
    list_display = ("run", "level", "message", "created_at")
    list_filter = ("level",)
    readonly_fields = ("run", "level", "message", "metadata", "created_at")
