"""用户领域业务逻辑：注册、登录鉴权、令牌签发与注销。"""

from django.contrib.auth import authenticate as django_authenticate  # 密码比对
from django.contrib.auth.password_validation import validate_password  # 密码强度
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework.authtoken.models import Token

from apps.core.exceptions import BusinessError
from apps.users.models import User


class UserService:
    """注册、登录、退出登录的业务入口。"""

    @staticmethod
    @transaction.atomic  # 开启事务。
    def register(username, password, email="", nickname="", avatar=""):
        """创建用户并返回实例。

        校验用户名唯一性与密码强度，任一不满足抛出 :class:`BusinessError`。
        """
        username = (username or "").strip()
        if not username:
            raise BusinessError("用户名不能为空", code="username_required")
        if User.objects.filter(username=username).exists():
            raise BusinessError("用户名 {} 已被注册".format(username), code="username_taken")

        candidate = User(
            username=username,
            email=(email or "").strip(),
            nickname=(nickname or "").strip(),
            avatar=(avatar or "").strip(),
        )
        try:
            validate_password(password, user=candidate)  # 密码不能和用户名一样
        except DjangoValidationError as exc:
            raise BusinessError("；".join(exc.messages), code="weak_password")

        candidate.set_password(password)  # 内部调用 Django 密码哈希算法（默认 PBKDF2）
        candidate.save()
        return candidate

    @staticmethod
    def authenticate(username, password):
        """校验用户名口令，失败抛出 :class:`BusinessError`。"""
        user = django_authenticate(username=username, password=password)
        if user is None:
            raise BusinessError("用户名或密码错误", code="invalid_credentials")
        if not user.is_active:
            raise BusinessError("账号已被禁用", code="inactive_user")
        return user

    @staticmethod
    def issue_token(user):
        """签发（或复用）该用户的 DRF 令牌。"""
        token, _created = Token.objects.get_or_create(user=user)
        return token

    @staticmethod
    def revoke_token(user):
        """注销令牌，使其立即失效。"""
        Token.objects.filter(user=user).delete()
