"""业务异常定义与 DRF 统一异常处理。

约定：Service 层只抛 :class:`BusinessError` 及其子类，绝不直接构造 HTTP 响应；
API 层不写 try/except，由 :func:`business_exception_handler` 统一翻译为
结构化的 400 / 403 / 404 / 409 响应。
"""

import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


class BusinessError(Exception):
    """业务规则异常，默认映射为 HTTP 400。

    :param message: 面向用户的中文提示
    :param code: 机器可读的错误码，便于前端做分支处理
    :param status_code: 覆盖默认的 HTTP 状态码
    :param extra: 需要附加到响应体中的额外字段
    """

    default_message = "业务处理失败"
    default_code = "business_error"
    default_status = status.HTTP_400_BAD_REQUEST

    def __init__(self, message=None, code=None, status_code=None, extra=None):
        self.message = message or self.default_message
        self.code = code or self.default_code
        self.status_code = status_code or self.default_status
        self.extra = dict(extra or {})
        super().__init__(self.message)

    def to_dict(self):
        """序列化为响应体：{detail, code, **extra}。"""
        payload = {"detail": self.message, "code": self.code}
        payload.update(self.extra)
        return payload

    def __repr__(self):
        return "<{} {}: {}>".format(self.__class__.__name__, self.code, self.message)


class NotFoundError(BusinessError):
    """资源不存在（404）。"""

    default_message = "资源不存在"
    default_code = "not_found"
    default_status = status.HTTP_404_NOT_FOUND


class ForbiddenError(BusinessError):
    """权限不足（403）。"""

    default_message = "没有权限执行该操作"
    default_code = "forbidden"
    default_status = status.HTTP_403_FORBIDDEN


class ConflictError(BusinessError):
    """状态冲突，例如成员已存在、余额未结清（409）。"""

    default_message = "当前状态不允许该操作"
    default_code = "conflict"
    default_status = status.HTTP_409_CONFLICT


def business_exception_handler(exc, context):
    """DRF 全局异常处理器。

    依次处理：业务异常 -> Django 表单/模型校验异常 -> DRF 默认异常。
    未被识别的异常交回 Django，避免吞掉真正的 500 堆栈。
    """
    if isinstance(exc, BusinessError):
        return Response(exc.to_dict(), status=exc.status_code)

    if isinstance(exc, DjangoValidationError):
        return Response(
            {
                "detail": "；".join(exc.messages),
                "code": "validation_error",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    return drf_exception_handler(exc, context)
