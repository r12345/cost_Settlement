"""结算记录模型。

Settlement 表示一次「线下真实发生的转账」，它不修改 LedgerEntry，
而是作为独立聚合项参与余额计算：转出方余额加回、接收方余额扣减。
"""

from django.conf import settings
from django.db import models


class Settlement(models.Model):
    """一笔实际结算（from_user 转给 to_user 多少钱）。"""

    group = models.ForeignKey(
        "groups.Group",
        verbose_name="群组",
        on_delete=models.CASCADE,
        related_name="settlements",
    )
    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="转出方",
        on_delete=models.PROTECT,
        related_name="settlements_paid",
    )
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="接收方",
        on_delete=models.PROTECT,
        related_name="settlements_received",
    )
    amount = models.DecimalField("结算金额", max_digits=12, decimal_places=2)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "结算记录"
        verbose_name_plural = "结算记录"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return "{} -> {} {}".format(self.from_user, self.to_user, self.amount)
