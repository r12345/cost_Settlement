"""支出、分摊明细与账本模型。

三张表的关系：

* :class:`Expense` —— 一笔支出（谁付了多少钱、按什么方式分摊）；
* :class:`ExpenseSplit` —— 该支出落到每个参与人头上的份额；
* :class:`LedgerEntry` —— 该支出对每个参与人净余额的影响（可回滚的账本）。

LedgerEntry 的语义：正数表示该用户「应收变多」（垫付超过自己份额），
负数表示「应付变多」（自己份额没付）。群组内某用户的净余额
= 该用户所有 LedgerEntry.amount 之和。
"""

from decimal import Decimal

from django.conf import settings
from django.db import models


class Expense(models.Model):
    """一笔支出。"""

    class SplitType(models.TextChoices):
        EQUAL = "EQUAL", "Equal"
        EXACT = "EXACT", "Exact"
        PERCENTAGE = "PERCENTAGE", "Percentage"

    class Category(models.TextChoices):
        FOOD = "FOOD", "Food"
        TRANSPORT = "TRANSPORT", "Transport"
        ACCOMMODATION = "ACCOMMODATION", "Accommodation"
        ENTERTAINMENT = "ENTERTAINMENT", "Entertainment"
        OTHER = "OTHER", "Other"

    group = models.ForeignKey(
        "groups.Group",
        verbose_name="群组",
        on_delete=models.CASCADE,
        related_name="expenses",
    )
    payer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="付款人",
        on_delete=models.PROTECT,
        related_name="expenses_paid",
    )
    amount = models.DecimalField("总额", max_digits=12, decimal_places=2)
    split_type = models.CharField("分摊方式", max_length=20, choices=SplitType.choices)
    note = models.CharField("备注", max_length=255, blank=True)
    category = models.CharField(
        "分类",
        max_length=20,
        choices=Category.choices,
        default=Category.OTHER,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="录入人",
        on_delete=models.PROTECT,
        related_name="expenses_created",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    is_deleted = models.BooleanField("已删除", default=False)

    class Meta:
        verbose_name = "支出"
        verbose_name_plural = "支出"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["group", "is_deleted"])]

    def __str__(self):
        return "{} {} {}".format(self.group_id, self.payer, self.amount)


class ExpenseSplit(models.Model):
    """支出在某位参与人身上的份额。"""

    expense = models.ForeignKey(
        Expense,
        verbose_name="支出",
        on_delete=models.CASCADE,
        related_name="splits",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="参与人",
        on_delete=models.PROTECT,
        related_name="expense_splits",
    )
    share_amount = models.DecimalField("分摊金额", max_digits=12, decimal_places=2)
    share_value = models.DecimalField(
        "原始比例/数值",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="百分比分摊时保存原始百分比，其余方式为空",
    )

    class Meta:
        verbose_name = "分摊明细"
        verbose_name_plural = "分摊明细"
        unique_together = ("expense", "user")

    def __str__(self):
        return "{} -> {} {}".format(self.expense_id, self.user, self.share_amount)


class LedgerEntry(models.Model):
    """账本分录：某笔支出对某用户净余额的影响。"""

    expense = models.ForeignKey(
        Expense,
        verbose_name="支出",
        on_delete=models.CASCADE,
        related_name="ledger_entries",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="用户",
        on_delete=models.PROTECT,
        related_name="ledger_entries",
    )
    amount = models.DecimalField(
        "影响金额",
        max_digits=12,
        decimal_places=2,
        help_text="正数表示应收增加，负数表示应付增加",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "账本分录"
        verbose_name_plural = "账本分录"
        ordering = ["id"]

    def __str__(self):
        return "{} {} {}".format(self.expense_id, self.user, self.amount)

    @property
    def zero(self):
        """便于断言与序列化时判断零值。"""
        return self.amount == Decimal("0.00")
