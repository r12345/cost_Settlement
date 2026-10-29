"""认证相关视图：注册 / 登录 / 登出。"""

from rest_framework import status
from rest_framework.permissions import AllowAny  # 任何人都可以访问，不需要登录
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.serializers import LoginSerializer, RegisterSerializer, UserSerializer
from apps.users.services import UserService


class RegisterView(APIView):
    """POST /api/auth/register/ —— 注册并直接返回可用令牌。"""

    authentication_classes = []  # 关闭本接口的身份认证，不会去解析 Authorization 头找 token
    permission_classes = [AllowAny]  # 放行所有请求，不管有没有登录

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)  # is_valid()：执行字段校验

        user = UserService.register(**serializer.validated_data)
        token = UserService.issue_token(user)  # 生成Token
        return Response(
            {"token": token.key, "user": UserSerializer(user).data},
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    """POST /api/auth/login/ —— 校验口令并返回令牌。"""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = UserService.authenticate(**serializer.validated_data)
        token = UserService.issue_token(user)
        return Response({"token": token.key, "user": UserSerializer(user).data})


class LogoutView(APIView):
    """POST /api/auth/logout/ —— 删除当前令牌。"""

    def post(self, request):
        UserService.revoke_token(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
