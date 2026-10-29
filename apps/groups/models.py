"""群组与成员关系模型。

一个 Group 下有多条 GroupMember 记录；创建者自动成为 ``admin``。
管理员是唯一可以修改/删除群组、移除成员的角色。
"""

from django.conf import settings
from django.db import models


class Group(models.Model):
    """一次共同消费的容器（例如「川西自驾游」）。"""

    name = models.CharField("群组名称", max_length=100)
    description = models.TextField("描述", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="创建者",
        on_delete=models.PROTECT,
        related_name="groups_created",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "群组"
        verbose_name_plural = "群组"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.name


class GroupMember(models.Model):
    """群组成员及其角色。"""

    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    group = models.ForeignKey(
        Group,
        verbose_name="群组",
        on_delete=models.CASCADE,
        related_name="members",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="成员",
        on_delete=models.PROTECT,
        related_name="group_memberships",
    )
    role = models.CharField(
        "角色", max_length=10, choices=Role.choices, default=Role.MEMBER
    )
    joined_at = models.DateTimeField("加入时间", auto_now_add=True)

    class Meta:
        verbose_name = "群组成员"
        verbose_name_plural = "群组成员"
        unique_together = ("group", "user")
        ordering = ["joined_at", "id"]

    def __str__(self):
        return "{}@{}".format(self.user, self.group)

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN
