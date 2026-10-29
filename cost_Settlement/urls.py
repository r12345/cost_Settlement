"""项目级 URL 路由：前端模板页面 + /api/ 业务接口。"""

from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView, TemplateView

# Django 内置管理后台路由，admin.site 是单例对象，自带增删改查后台，admin.site.urls 是 admin 封装好的路由列表。
urlpatterns = [
    path("admin/", admin.site.urls),
    # 前端页面：全部由 TemplateView 渲染，登录态由页面里的 requireAuth() 检查
    path("", RedirectView.as_view(url="/login/", permanent=False), name="home"),
    path(
        "login/",
        TemplateView.as_view(template_name="frontend/login.html"),
        name="login",
    ),
    path(
        "groups/",
        TemplateView.as_view(template_name="frontend/groups.html"),
        name="groups",
    ),
    path(
        "groups/<int:group_id>/",
        TemplateView.as_view(template_name="frontend/group_detail.html"),
        name="group-detail",
    ),
    path(
        "groups/<int:group_id>/ai/",
        TemplateView.as_view(template_name="frontend/ai_parse.html"),
        name="group-ai",
    ),
    path("api/", include("apps.core.urls")),
    path("api/auth/", include("apps.users.urls")),
    path("api/", include("apps.groups.urls")),
    path("api/", include("apps.expenses.urls")),
    path("api/", include("apps.settlements.urls")),
    path("api/", include("apps.ai.urls")),
]
