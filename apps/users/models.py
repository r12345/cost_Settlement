"""自定义用户模型。

沿用 AbstractUser 的全部能力（username / password / email / is_staff ...），
额外增加昵称与头像两个可选字段，供前端展示。
"""

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """系统用户。"""

    nickname = models.CharField("昵称", max_length=50, blank=True, default="")
    avatar = models.URLField("头像地址", blank=True, default="")

    class Meta:
        verbose_name = "用户"
        verbose_name_plural = "用户"

    def __str__(self):
        return self.nickname or self.username

    @property
    def display_name(self):
        """群组消息、账本展示时优先使用昵称。"""
        return self.nickname or self.username
