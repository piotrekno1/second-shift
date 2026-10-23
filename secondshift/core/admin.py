from django.contrib import admin

from secondshift.core.models import Workspace, WorkspaceMembership

admin.site.site_header = "SecondShift Admin"
admin.site.site_title = "SecondShift Admin"
admin.site.index_title = "Administration"


class MembershipInline(admin.TabularInline):
    model = WorkspaceMembership
    extra = 0


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "owner", "created_at")
    search_fields = ("name", "slug", "owner__email")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [MembershipInline]


@admin.register(WorkspaceMembership)
class WorkspaceMembershipAdmin(admin.ModelAdmin):
    list_display = ("workspace", "user", "role", "created_at")
    list_filter = ("role",)
    search_fields = ("workspace__name", "user__email")
