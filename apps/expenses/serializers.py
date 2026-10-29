"""支出相关的输入校验与输出整形。

只做字段级校验（类型、必填、枚举、长度）与结构整形，
金额分摊、成员归属、权限、合计校验等业务规则全部在
:mod:`apps.expenses.services` 中实现。
"""

from rest_framework import serializers

from apps.expenses.models import Expense, ExpenseSplit
from apps.groups.serializers import MemberUserSerializer


class SplitInputSerializer(serializers.Serializer):
    """单条分摊入参。

    EXACT 用 ``share_amount``，PERCENTAGE 用 ``share_value``，由 service 按
    分摊方式取用；两者同时出现时按分摊方式选择，不做格式外的推断。
    """

    user = serializers.IntegerField()
    share_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    share_value = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )


class ExpenseCreateSerializer(serializers.Serializer):
    """创建支出入参。"""

    payer = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    split_type = serializers.ChoiceField(choices=Expense.SplitType.choices)
    participants = serializers.ListField(
        child=serializers.IntegerField(), allow_empty=False
    )
    note = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )
    category = serializers.ChoiceField(
        choices=Expense.Category.choices,
        required=False,
        default=Expense.Category.OTHER,
    )
    splits = serializers.ListField(
        child=SplitInputSerializer(), required=False, allow_null=True, default=None
    )


class ExpenseUpdateSerializer(serializers.Serializer):
    """编辑支出入参：所有字段可选，payer / group 不可修改。"""

    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False
    )
    split_type = serializers.ChoiceField(
        choices=Expense.SplitType.choices, required=False
    )
    participants = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=False
    )
    splits = serializers.ListField(
        child=SplitInputSerializer(), required=False, allow_null=True
    )
    note = serializers.CharField(
        max_length=255, required=False, allow_blank=True
    )
    category = serializers.ChoiceField(
        choices=Expense.Category.choices, required=False
    )

    def validate(self, attrs):
        for field in ("payer", "group"):
            if field in self.initial_data:
                raise serializers.ValidationError(
                    {field: "该字段不可修改，如需更换请删除后重建支出"}
                )
        if not attrs:
            raise serializers.ValidationError("没有需要更新的字段")
        return attrs


class ExpenseSplitSerializer(serializers.ModelSerializer):
    """分摊明细输出，金额序列化为两位小数字符串。"""

    user = MemberUserSerializer(read_only=True)

    class Meta:
        model = ExpenseSplit
        fields = ("id", "user", "share_amount", "share_value")
        read_only_fields = fields


class ExpenseSerializer(serializers.ModelSerializer):
    """支出列表/详情输出。"""

    payer = MemberUserSerializer(read_only=True)
    created_by = MemberUserSerializer(read_only=True)
    splits = serializers.SerializerMethodField()

    class Meta:
        model = Expense
        fields = (
            "id",
            "group",
            "payer",
            "amount",
            "split_type",
            "note",
            "category",
            "created_by",
            "created_at",
            "is_deleted",
            "splits",
        )
        read_only_fields = fields

    def get_splits(self, obj):
        """按分摊创建顺序返回明细，保证「最后一人」的位置稳定。"""
        splits = sorted(obj.splits.all(), key=lambda split: split.id)
        return ExpenseSplitSerializer(splits, many=True).data
