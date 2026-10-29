"""全局分页配置：列表接口默认每页 20 条，允许通过 ?page_size= 覆盖。"""

from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """项目统一分页器。"""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
