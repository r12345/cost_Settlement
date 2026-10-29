"""群组 app 的路由：由 ViewSet 自动生成 RESTful 路径。

  GET/POST            /api/groups/
  GET/PATCH/DELETE    /api/groups/{id}/
  POST                /api/groups/{id}/invite/
  POST                /api/groups/{id}/leave/
  DELETE              /api/groups/{id}/members/{uid}/
  GET                 /api/groups/{id}/balances/
"""

from rest_framework.routers import DefaultRouter

from apps.groups.views import GroupViewSet

router = DefaultRouter()
router.register("groups", GroupViewSet, basename="group")

urlpatterns = router.urls
