"""群组相关的输入校验与输出整形。

只做字段级校验（必填、长度、类型）与结构整形；
成员唯一性、权限、余额等业务规则一律在 :mod:`apps.groups.services` 中。
"""

from rest_framework import serializers

from apps.groups.models import Group, GroupMember
from apps.users.models import User


class MemberUserSerializer(serializers.ModelSerializer):
    """成员在群组上下文中的公开信息（不含邮箱等隐私字段）。"""

    class Meta:
        model = User
        fields = ("id", "username", "nickname", "avatar")
        read_only_fields = fields


class GroupSerializer(serializers.ModelSerializer):
    """群组详情/列表输出。"""

    created_by = MemberUserSerializer(read_only=True)
    member_count = serializers.SerializerMethodField()
    my_role = serializers.SerializerMethodField()

    class Meta:
        model = Group
        fields = (
            "id",
            "name",
            "description",
            "created_by",
            "created_at",
            "member_count",
            "my_role",
        )
        read_only_fields = fields

    def get_member_count(self, obj):
        return obj.members.count()

    def get_my_role(self, obj):
        """当前登录用户在该群组的角色，非成员为 None。"""
        request = self.context.get("request")
        if request is None or not getattr(request, "user", None):
            return None
        for membership in obj.members.all():
            if membership.user_id == request.user.id:
                return membership.role
        return None


class GroupCreateSerializer(serializers.Serializer):
    """创建群组入参。"""

    name = serializers.CharField(max_length=100)
    description = serializers.CharField(required=False, allow_blank=True, default="")


class GroupUpdateSerializer(serializers.Serializer):
    """编辑群组入参，字段均可选。"""

    name = serializers.CharField(max_length=100, required=False)
    description = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("没有需要更新的字段")
        return attrs


class InviteMemberSerializer(serializers.Serializer):
    """邀请成员入参。"""

    username = serializers.CharField(max_length=150)


class GroupMemberSerializer(serializers.ModelSerializer):
    """成员输出。"""

    user = MemberUserSerializer(read_only=True)

    class Meta:
        model = GroupMember
        fields = ("id", "user", "role", "joined_at")
        read_only_fields = fields


class MemberBalanceSerializer(serializers.Serializer):
    """单个成员在群组内的净余额。"""

    user_id = serializers.IntegerField()
    username = serializers.CharField()
    nickname = serializers.CharField(allow_blank=True)
    avatar = serializers.CharField(allow_blank=True)
    role = serializers.CharField()
    net_balance = serializers.DecimalField(max_digits=12, decimal_places=2)


class RelationshipSerializer(serializers.Serializer):
    """当前用户与另一位成员之间的两两明细。

    ``net_balance`` 为正表示对方欠我，为负表示我欠对方。
    """

    user_id = serializers.IntegerField()
    username = serializers.CharField()
    nickname = serializers.CharField(allow_blank=True)
    avatar = serializers.CharField(allow_blank=True)
    net_balance = serializers.DecimalField(max_digits=12, decimal_places=2)
    i_owe = serializers.DecimalField(max_digits=12, decimal_places=2)
    owes_me = serializers.DecimalField(max_digits=12, decimal_places=2)


class BalanceReportSerializer(serializers.Serializer):
    """余额报告输出（金额统一序列化为两位小数字符串）。"""

    group_id = serializers.IntegerField()
    my_net_balance = serializers.DecimalField(max_digits=12, decimal_places=2)
    is_settled = serializers.BooleanField()
    balances = MemberBalanceSerializer(many=True)
    relationships = RelationshipSerializer(many=True)
