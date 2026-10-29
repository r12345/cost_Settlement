from django.apps import AppConfig


class CoreConfig(AppConfig):
    """core 是纯工具 app，不定义任何数据库模型。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "核心工具"
