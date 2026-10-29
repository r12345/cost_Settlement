"""core 的 serializers 仅承载与工具接口相关的输出整形。"""

from rest_framework import serializers


class HealthSerializer(serializers.Serializer):
    """健康检查响应结构。"""

    status = serializers.CharField()
    service = serializers.CharField()
    version = serializers.CharField()
