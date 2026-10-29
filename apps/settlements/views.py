"""结算相关视图。

视图只负责：校验入参格式、取到当前用户可见的群组、调用 SettlementService、
返回响应；任何金额计算与校验都在 service 层。
"""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardPagination
from apps.groups.services import GroupService
from apps.settlements.models import Settlement
from apps.settlements.serializers import (
    SettlementCreateSerializer,
    SettlementSerializer,
    TransferSuggestionSerializer,
)
from apps.settlements.services import SettlementService


class GroupSettleSuggestionView(APIView):
    """GET /api/groups/{id}/settle/ —— 生成结算建议（只读，不落库）。"""

    def get(self, request, group_id):
        group = GroupService.get_group_for_member(group_id, request.user)
        transfers = SettlementService.suggest_settlements(group)
        return Response(
            {"transfers": TransferSuggestionSerializer(transfers, many=True).data}
        )


class GroupSettlementListCreateView(APIView):
    """GET/POST /api/groups/{id}/settlements/ —— 结算历史与登记结算。"""

    def get(self, request, group_id):
        group = GroupService.get_group_for_member(group_id, request.user)
        queryset = (
            Settlement.objects.filter(group=group)
            .select_related("from_user", "to_user")
            .order_by("-created_at", "-id")
        )

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            SettlementSerializer(page, many=True).data
        )

    def post(self, request, group_id):
        group = GroupService.get_group_for_member(group_id, request.user)
        serializer = SettlementCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        settlement = SettlementService.record_settlement(
            group, actor=request.user, **serializer.validated_data
        )
        return Response(
            SettlementSerializer(settlement).data, status=status.HTTP_201_CREATED
        )
