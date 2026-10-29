"""core 的视图：健康检查，不需要认证。"""

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.services import SystemService


class HealthCheckView(APIView):
    """GET /api/health/ —— 用于确认服务与数据库迁移状态。"""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(SystemService.service_metadata())
