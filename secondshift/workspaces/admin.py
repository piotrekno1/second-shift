from django.contrib import admin

from secondshift.workspaces.models import Workspace, WorkspaceMembership


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
