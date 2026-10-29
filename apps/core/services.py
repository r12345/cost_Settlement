"""core 的 service 层：目前只提供与业务无关的运行时信息。"""

from django.conf import settings


class SystemService:
    """系统级只读信息。"""

    @staticmethod
    def service_metadata():
        """返回服务名与版本号，供健康检查与运维探针使用。"""
        return {
            "status": "ok",
            "service": "cost-settlement",
            "version": settings.SERVICE_VERSION,
        }
