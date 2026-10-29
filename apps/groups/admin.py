"""群组的 admin 配置，便于本地查看成员关系。"""

from django.contrib import admin

from apps.groups.models import Group, GroupMember


class GroupMemberInline(admin.TabularInline):
    model = GroupMember
    extra = 0
    raw_id_fields = ("user",)


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "created_by", "created_at")
    search_fields = ("name", "description")
    readonly_fields = ("created_at",)
    inlines = [GroupMemberInline]


@admin.register(GroupMember)
class GroupMemberAdmin(admin.ModelAdmin):
    list_display = ("id", "group", "user", "role", "joined_at")
    list_filter = ("role",)
    raw_id_fields = ("group", "user")
