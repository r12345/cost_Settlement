"""支出 app 的路由。

  GET/POST      /api/groups/{id}/expenses/
  GET/PATCH/DELETE  /api/expenses/{id}/
"""

from django.urls import path

from apps.expenses.views import ExpenseDetailView, GroupExpenseListCreateView

urlpatterns = [
    path(
        "groups/<int:group_id>/expenses/",
        GroupExpenseListCreateView.as_view(),
        name="group-expense-list",
    ),
    path(
        "expenses/<int:pk>/",
        ExpenseDetailView.as_view(),
        name="expense-detail",
    ),
]
