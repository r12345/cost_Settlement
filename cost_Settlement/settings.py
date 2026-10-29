"""Django settings for the 「费用分摊与结算系统」 project.

所有环境相关的配置都从环境变量读取；本地开发时项目根目录的 ``.env``
文件会被自动加载（依赖 python-dotenv，未安装时静默跳过）。
"""

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

try:  # .env 是可选的，缺少 python-dotenv 时不影响启动
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:  # pragma: no cover - 取决于部署环境
    pass


def env(name, default=None):
    """读取环境变量，空字符串视为未设置。"""
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def env_bool(name, default=False):
    """读取布尔型环境变量，1/true/yes/on 视为真。"""
    value = env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_list(name, default=()):
    """读取逗号分隔的列表型环境变量。"""
    value = env(name)
    if value is None:
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# 基础配置
# ---------------------------------------------------------------------------
SECRET_KEY = env("SECRET_KEY", "django-insecure-dev-only-key-please-override")
DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", ("*",) if DEBUG else ("localhost", "127.0.0.1"))

SERVICE_VERSION = env("SERVICE_VERSION", "1.0.0")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # 第三方
    "rest_framework",
    "rest_framework.authtoken",
    # 业务 app
    "apps.core.apps.CoreConfig",
    "apps.users.apps.UsersConfig",
    "apps.groups.apps.GroupsConfig",
    "apps.expenses.apps.ExpensesConfig",
    "apps.settlements.apps.SettlementsConfig",
    "apps.ai.apps.AiConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "cost_Settlement.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "cost_Settlement.wsgi.application"
ASGI_APPLICATION = "cost_Settlement.asgi.application"


# ---------------------------------------------------------------------------
# 数据库：DATABASE_URL 从环境变量读取
# ---------------------------------------------------------------------------
DEFAULT_DATABASE_URL = "sqlite:///" + (BASE_DIR / "db.sqlite3").as_posix()


def parse_database_url(url):
    """把 DATABASE_URL 解析成 Django 的 DATABASES['default'] 配置。

    支持 ``postgres://`` / ``postgresql://`` 和 ``sqlite:///`` 三种协议，
    URL 中的用户名、密码会做百分号解码，便于配置含特殊字符的口令。
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()

    if scheme in {"postgres", "postgresql"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")) or "postgres",
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "127.0.0.1",
            "PORT": str(parsed.port or 5432),
            "CONN_MAX_AGE": 60,
            "OPTIONS": {"connect_timeout": 5},
        }

    if scheme in {"sqlite", "sqlite3"}:
        path = unquote(parsed.path.lstrip("/"))
        if not path:
            return {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
        candidate = Path(path)
        name = candidate if candidate.is_absolute() else BASE_DIR / candidate
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": str(name)}

    raise ValueError("不支持的 DATABASE_URL 协议: {!r}".format(parsed.scheme))


DATABASES = {"default": parse_database_url(env("DATABASE_URL", DEFAULT_DATABASE_URL))}


# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "users.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "apps.core.exceptions.business_exception_handler",
    "DEFAULT_THROTTLE_RATES": {
        "anon": "30/min",
        "user": "240/min",
        "ai_parse": "10/min",
    },
    "UNAUTHENTICATED_USER": None,
}

# 限流计数保存在进程内缓存，本项目不引入 Redis
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "cost-settlement",
    }
}


# ---------------------------------------------------------------------------
# AI（OpenAI 兼容接口，可切换 DeepSeek / 通义千问 / OpenAI）
# ---------------------------------------------------------------------------
OPENAI_API_KEY = env("OPENAI_API_KEY", "")
OPENAI_BASE_URL = env("OPENAI_BASE_URL", "")
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-4o-mini")
# embedding 可以独立于 chat 配置（例如 chat 用 DeepSeek、embedding 用通义千问）；
# 留空即回落到上面的 OPENAI_API_KEY / OPENAI_BASE_URL，旧环境无需改动
OPENAI_EMBEDDING_API_KEY = env("OPENAI_EMBEDDING_API_KEY", "") or OPENAI_API_KEY
OPENAI_EMBEDDING_BASE_URL = env("OPENAI_EMBEDDING_BASE_URL", "") or OPENAI_BASE_URL
OPENAI_EMBEDDING_MODEL = env("OPENAI_EMBEDDING_MODEL", "text-embedding-v4")
OPENAI_TIMEOUT = float(env("OPENAI_TIMEOUT", "10"))
AI_ENABLED = env_bool("AI_ENABLED", True)
AI_CONFIDENCE_THRESHOLD = float(env("AI_CONFIDENCE_THRESHOLD", "0.6"))


# ---------------------------------------------------------------------------
# 国际化 / 静态文件
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        }
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "loggers": {
        "apps": {
            "handlers": ["console"],
            "level": env("DJANGO_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
    },
}
