from django.urls import path

from apps.core.views import HealthCheckView

app_name = "core"

urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health"),
]
