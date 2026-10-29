"""结算相关的输入校验与输出整形。"""

from rest_framework import serializers

from apps.groups.serializers import MemberUserSerializer
from apps.settlements.models import Settlement


class SettlementSerializer(serializers.ModelSerializer):
    """结算记录输出，金额序列化为两位小数字符串。"""

    from_user = MemberUserSerializer(read_only=True)
    to_user = MemberUserSerializer(read_only=True)

    class Meta:
        model = Settlement
        fields = ("id", "group", "from_user", "to_user", "amount", "created_at")
        read_only_fields = fields


class SettlementCreateSerializer(serializers.Serializer):
    """登记结算入参。"""

    from_user_id = serializers.IntegerField()
    to_user_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)


class TransferSuggestionSerializer(serializers.Serializer):
    """单条转账建议。"""

    from_user_id = serializers.IntegerField()
    from_username = serializers.CharField()
    to_user_id = serializers.IntegerField()
    to_username = serializers.CharField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
