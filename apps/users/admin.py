"""自定义用户的 admin 配置，便于本地调试数据。"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.users.models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """在默认 UserAdmin 基础上展示昵称与头像。"""

    fieldsets = BaseUserAdmin.fieldsets + (
        ("扩展资料", {"fields": ("nickname", "avatar")}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("扩展资料", {"fields": ("nickname", "avatar")}),
    )
    list_display = ("username", "nickname", "email", "is_staff", "is_active")
    search_fields = ("username", "nickname", "email")
