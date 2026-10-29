"""AI 相关的输入校验与草稿整形。

草稿（draft）用的是「AI 侧字段名」：``payer_id`` / ``splits[].user_id``；
这里负责把它们翻译成 ``ExpenseService.create_expense`` 的入参名，
不改任何业务校验——金额、成员、份额合计仍然由 Service 把关。
"""

from rest_framework import serializers

from apps.ai.services import MAX_TEXT_LENGTH
from apps.expenses.models import Expense


class ParseExpenseRequestSerializer(serializers.Serializer):
    """自然语言解析入参。"""

    text = serializers.CharField(max_length=MAX_TEXT_LENGTH)


class QueryRequestSerializer(serializers.Serializer):
    """账本问答入参。"""

    question = serializers.CharField(max_length=500)


class DraftSplitSerializer(serializers.Serializer):
    """草稿里的一条分摊。"""

    user_id = serializers.IntegerField()
    share_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    share_value = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )


class ExpenseDraftSerializer(serializers.Serializer):
    """草稿落库入参。

    ``confidence``、``group_id`` 等非支出字段不在声明里，DRF 会直接忽略；
    群组一律以 URL 上的为准。
    """

    payer_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    participants = serializers.ListField(
        child=serializers.IntegerField(), allow_empty=False
    )
    split_type = serializers.ChoiceField(choices=Expense.SplitType.choices)
    splits = serializers.ListField(
        child=DraftSplitSerializer(), required=False, allow_null=True, default=None
    )
    note = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )
    category = serializers.ChoiceField(
        choices=Expense.Category.choices,
        required=False,
        default=Expense.Category.OTHER,
    )

    def validate(self, attrs):
        """把草稿的 splits 结构转成 Service 认识的结构。"""
        splits = attrs.get("splits")
        if splits:
            converted = []
            for item in splits:
                entry = {"user": item["user_id"]}
                if item.get("share_amount") is not None:
                    entry["share_amount"] = item["share_amount"]
                if item.get("share_value") is not None:
                    entry["share_value"] = item["share_value"]
                converted.append(entry)
            attrs["splits"] = converted
        return attrs
