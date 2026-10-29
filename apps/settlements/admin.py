"""结算记录的 admin 配置。"""

from django.contrib import admin

from apps.settlements.models import Settlement


@admin.register(Settlement)
class SettlementAdmin(admin.ModelAdmin):
    list_display = ("id", "group", "from_user", "to_user", "amount", "created_at")
    list_filter = ("group",)
    raw_id_fields = ("group", "from_user", "to_user")
