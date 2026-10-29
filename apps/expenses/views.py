"""支出相关视图。

视图只负责：校验输入格式（serializer）、取出当前用户可见的对象、
调用 :class:`apps.expenses.services.ExpenseService`、返回响应。
"""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import NotFoundError
from apps.core.pagination import StandardPagination
from apps.expenses.models import Expense
from apps.expenses.serializers import (
    ExpenseCreateSerializer,
    ExpenseSerializer,
    ExpenseUpdateSerializer,
)
from apps.expenses.services import ExpenseService
from apps.groups.models import Group
from apps.groups.services import GroupService


def get_group_for_member(group_id, user):
    """取群组并校验成员身份；不存在或非成员统一 404。"""
    group = Group.objects.filter(pk=group_id).first()
    if group is None:
        raise NotFoundError("群组不存在，或你不是该群组成员")
    GroupService.require_member(group, user)
    return group


def get_expense_for_member(expense_id, user):
    """取支出并校验其群组成员身份；不存在或非成员统一 404。"""
    expense = (
        Expense.objects.select_related("group", "payer", "created_by")
        .prefetch_related("splits__user")
        .filter(pk=expense_id)
        .first()
    )
    if expense is None:
        raise NotFoundError("支出不存在")
    GroupService.require_member(expense.group, user)
    return expense


class GroupExpenseListCreateView(APIView):
    """GET/POST /api/groups/{id}/expenses/ —— 群组支出列表与创建。"""

    def get(self, request, group_id):
        group = get_group_for_member(group_id, request.user)
        queryset = (
            Expense.objects.filter(group=group, is_deleted=False)
            .select_related("group", "payer", "created_by")
            .prefetch_related("splits__user")
        )

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = ExpenseSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request, group_id):
        group = get_group_for_member(group_id, request.user)
        serializer = ExpenseCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        expense = ExpenseService.create_expense(
            group=group, actor=request.user, **serializer.validated_data
        )
        return Response(
            ExpenseSerializer(expense).data, status=status.HTTP_201_CREATED
        )


class ExpenseDetailView(APIView):
    """GET/PATCH/DELETE /api/expenses/{id}/ —— 支出详情、编辑与软删除。"""

    def get(self, request, pk):
        expense = get_expense_for_member(pk, request.user)
        return Response(ExpenseSerializer(expense).data)

    def patch(self, request, pk):
        expense = get_expense_for_member(pk, request.user)
        serializer = ExpenseUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        expense = ExpenseService.update_expense(
            expense, actor=request.user, **serializer.validated_data
        )
        return Response(
            ExpenseSerializer(
                Expense.objects.prefetch_related("splits__user").get(pk=expense.pk)
            ).data
        )

    def delete(self, request, pk):
        expense = get_expense_for_member(pk, request.user)
        ExpenseService.soft_delete_expense(expense, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
