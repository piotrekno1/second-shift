from django.contrib import admin
from django.http import HttpRequest

from secondshift.workspaces.models import ApiToken, Workspace, WorkspaceMembership


class MembershipInline(admin.TabularInline):
    model = WorkspaceMembership
    extra = 0


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "created_at")
    search_fields = ("name", "slug", "memberships__user__email")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [MembershipInline]


@admin.register(WorkspaceMembership)
class WorkspaceMembershipAdmin(admin.ModelAdmin):
    list_display = ("workspace", "user", "role", "created_at")
    list_filter = ("role",)
    search_fields = ("workspace__name", "user__email")


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    list_display = ("name", "workspace", "created_by", "scope", "expires_at")
    list_filter = ("scope",)
    search_fields = ("name", "workspace__name", "created_by__email")
    exclude = ("token_hash",)
    readonly_fields = ("last_used_at",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        # A token's secret is generated and shown once from the dashboard.
        return False
