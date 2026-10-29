"""结算 app 的路由。

  GET   /api/groups/{id}/settle/       生成结算建议（不落库）
  GET   /api/groups/{id}/settlements/  结算历史（分页）
  POST  /api/groups/{id}/settlements/  登记实际结算
"""

from django.urls import path

from apps.settlements.views import (
    GroupSettleSuggestionView,
    GroupSettlementListCreateView,
)

urlpatterns = [
    path(
        "groups/<int:group_id>/settle/",
        GroupSettleSuggestionView.as_view(),
        name="group-settle",
    ),
    path(
        "groups/<int:group_id>/settlements/",
        GroupSettlementListCreateView.as_view(),
        name="group-settlement-list",
    ),
]
