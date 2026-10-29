"""群组与成员管理视图。

视图只做三件事：认证（全局 TokenAuthentication）、从 queryset 取到
当前用户可见的对象、调用 GroupService 并整形响应。
"""

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.exceptions import NotFoundError
from apps.groups.models import Group
from apps.groups.serializers import (
    BalanceReportSerializer,
    GroupCreateSerializer,
    GroupMemberSerializer,
    GroupSerializer,
    GroupUpdateSerializer,
    InviteMemberSerializer,
)
from apps.groups.services import GroupService


class GroupViewSet(viewsets.ModelViewSet):
    """群组 CRUD 与成员管理。

    列表/详情只暴露当前用户已加入的群组，非成员访问返回 404。
    """

    serializer_class = GroupSerializer

    def get_queryset(self):
        return (
            Group.objects.filter(members__user=self.request.user)
            .select_related("created_by")
            .prefetch_related("members__user")
            .distinct()
        )

    def get_serializer_class(self):
        if self.action == "create":
            return GroupCreateSerializer
        if self.action in {"update", "partial_update"}:
            return GroupUpdateSerializer
        return GroupSerializer

    def get_object(self):
        """取当前用户可见的群组；不存在或不是成员统一返回 404。"""
        lookup = self.kwargs.get(self.lookup_field)
        try:
            group = self.get_queryset().filter(pk=lookup).first()
        except (TypeError, ValueError):
            group = None
        if group is None:
            raise NotFoundError("群组不存在，或你不是该群组成员")
        return group

    def create(self, request, *args, **kwargs):
        serializer = GroupCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        group = GroupService.create_group(user=request.user, **serializer.validated_data)
        return Response(
            GroupSerializer(group, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        group = self.get_object()
        serializer = GroupUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        group = GroupService.update_group(
            group, actor=request.user, **serializer.validated_data
        )
        return Response(
            GroupSerializer(group, context=self.get_serializer_context()).data
        )

    def destroy(self, request, *args, **kwargs):
        group = self.get_object()
        GroupService.delete_group(group, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="invite")
    def invite(self, request, pk=None):
        """POST /api/groups/{id}/invite/ —— 邀请成员。"""
        group = self.get_object()
        serializer = InviteMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        membership = GroupService.invite_member(
            group, actor=request.user, username=serializer.validated_data["username"]
        )
        return Response(
            GroupMemberSerializer(membership).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"], url_path="leave")
    def leave(self, request, pk=None):
        """POST /api/groups/{id}/leave/ —— 退出群组（净余额必须为 0）。"""
        group = self.get_object()
        GroupService.leave_group(group, user=request.user)
        return Response({"detail": "已退出群组", "group_id": group.id})

    @action(detail=True, methods=["delete"], url_path=r"members/(?P<user_id>[0-9]+)")
    def remove_member(self, request, pk=None, user_id=None):
        """DELETE /api/groups/{id}/members/{uid}/ —— 移除成员（仅 admin）。"""
        group = self.get_object()
        GroupService.remove_member(group, actor=request.user, user_id=int(user_id))
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"], url_path="balances")
    def balances(self, request, pk=None):
        """GET /api/groups/{id}/balances/ —— 当前用户视角的净余额与两两明细。"""
        group = self.get_object()
        report = GroupService.get_balances(group, viewer=request.user)
        return Response(BalanceReportSerializer(report).data)
