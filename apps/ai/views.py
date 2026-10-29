"""AI 相关视图。

视图只做三件事：确认群组成员身份、取成员名单、调用 AIService / ExpenseService。
AI 产出的草稿在落库前必须完整走一遍 ``ExpenseService.create_expense``，
与手动记账完全同一条路径，没有任何特权校验。
"""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.ai.serializers import (
    ExpenseDraftSerializer,
    ParseExpenseRequestSerializer,
    QueryRequestSerializer,
)
from apps.ai.services import AIService, RagQueryService
from apps.core.exceptions import BusinessError
from apps.expenses.serializers import ExpenseSerializer
from apps.expenses.services import ExpenseService
from apps.groups.services import GroupService


class ExpenseParseView(APIView):
    """POST /api/groups/{id}/expenses/parse/ —— 自然语言记账解析。

    每用户每分钟 10 次，超限由 DRF 返回 429。
    AI 不可用或解析失败时返回 422 + 降级说明，绝不返回堆栈。
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ai_parse"

    def post(self, request, group_id):
        group = GroupService.get_group_for_member(group_id, request.user)

        serializer = ParseExpenseRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        member_map = dict(group.members.values_list("user__username", "user_id"))
        draft = AIService.parse_expense(serializer.validated_data["text"], member_map)

        if draft is None:
            return Response(
                {"fallback": "manual", "reason": AIService.failure_reason()},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        return Response({"draft": draft, "confidence": draft["confidence"]})


class ExpenseFromDraftView(APIView):
    """POST /api/groups/{id}/expenses/from-draft/ —— 草稿落库。

    复用 ExpenseService.create_expense，响应结构与手动创建完全一致。
    """

    def post(self, request, group_id):
        group = GroupService.get_group_for_member(group_id, request.user)

        serializer = ExpenseDraftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        payload = dict(serializer.validated_data)
        payer = payload.pop("payer_id")

        expense = ExpenseService.create_expense(
            group=group, actor=request.user, payer=payer, **payload
        )
        return Response(
            ExpenseSerializer(expense).data, status=status.HTTP_201_CREATED
        )


class GroupQueryView(APIView):
    """POST /api/groups/{id}/query/ —— 账本语义问答。

    与解析端点共用 ``ai_parse`` 限流桶（每用户每分钟 10 次）。
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ai_parse"

    def post(self, request, group_id):
        group = GroupService.get_group_for_member(group_id, request.user)

        serializer = QueryRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = RagQueryService.answer(
                group, request.user, serializer.validated_data["question"]
            )
        except BusinessError as exc:
            if exc.code == "ai_disabled":
                return Response(
                    {"fallback": "manual", "reason": "AI 功能未启用"},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            if exc.code == "query_parse_failed":
                return Response(
                    {"fallback": "manual", "reason": "无法理解该问题，请换个问法"},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            if exc.code == "embedding_failed":
                return Response(
                    {"detail": "语义检索服务暂时不可用"},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            raise

        return Response(result)
