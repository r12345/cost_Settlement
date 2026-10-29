"""AI app 的路由。

  POST /api/groups/{id}/expenses/parse/       自然语言解析（返回草稿，不落库）
  POST /api/groups/{id}/expenses/from-draft/  草稿落库（复用 ExpenseService）
  POST /api/groups/{id}/query/                账本语义问答（RAG）
"""

from django.urls import path

from apps.ai.views import ExpenseFromDraftView, ExpenseParseView, GroupQueryView

urlpatterns = [
    path(
        "groups/<int:group_id>/expenses/parse/",
        ExpenseParseView.as_view(),
        name="expense-parse",
    ),
    path(
        "groups/<int:group_id>/expenses/from-draft/",
        ExpenseFromDraftView.as_view(),
        name="expense-from-draft",
    ),
    path(
        "groups/<int:group_id>/query/",
        GroupQueryView.as_view(),
        name="group-query",
    ),
]
