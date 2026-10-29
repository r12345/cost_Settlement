"""用户相关的输入校验与输出整形。

只负责字段层面的格式校验（必填、长度、邮箱格式等）；
用户名重复、密码强度等业务规则由 :class:`apps.users.services.UserService` 负责。
"""

from rest_framework import serializers

from apps.users.models import User


class UserSerializer(serializers.ModelSerializer):
    """用户公开信息。"""

    class Meta:
        model = User
        fields = ("id", "username", "email", "nickname", "avatar")
        read_only_fields = fields


class RegisterSerializer(serializers.Serializer):
    """注册入参。"""

    username = serializers.CharField(max_length=150)
    password = serializers.CharField(
        write_only=True, min_length=6, max_length=128, trim_whitespace=False
    )
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    nickname = serializers.CharField(
        max_length=50, required=False, allow_blank=True, default=""
    )


class LoginSerializer(serializers.Serializer):
    """登录入参。"""

    username = serializers.CharField(max_length=150)
    password = serializers.CharField(
        write_only=True, max_length=128, trim_whitespace=False
    )


class TokenSerializer(serializers.Serializer):
    """登录/注册成功后返回的令牌与用户信息。"""

    token = serializers.CharField()
    user = UserSerializer()
