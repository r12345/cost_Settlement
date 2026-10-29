"""项目级 pytest 共享 fixture。

导入 Model / Service 一律放在 fixture 内部，避免 conftest 在 Django 完成
初始化之前被 import（根 conftest 的加载时机早于 pytest-django 的 configure）。
"""

import pytest

PASSWORD = "Str0ngPassw0rd!"


@pytest.fixture(autouse=True)
def isolate_ai_settings(settings):
    """测试默认不联网：清空 AI 凭据，需要 AI 的用例自己覆盖回去。

    本机 ``.env`` 可能配了真实的 ``OPENAI_API_KEY``，若不隔离，凡是「创建支出」
    的用例都会真的去请求 embedding 接口，测试就不再是确定性的了。
    """
    settings.OPENAI_API_KEY = ""
    settings.OPENAI_BASE_URL = ""
    settings.OPENAI_EMBEDDING_API_KEY = ""
    settings.OPENAI_EMBEDDING_BASE_URL = ""
    return settings


@pytest.fixture
def api_client():
    """未认证的 DRF 客户端。"""
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def user(db):
    """普通用户 alice。"""
    from apps.users.models import User

    return User.objects.create_user(
        username="alice", password=PASSWORD, nickname="爱丽丝"
    )


@pytest.fixture
def other_user(db):
    """另一个普通用户 bob。"""
    from apps.users.models import User

    return User.objects.create_user(
        username="bob", password=PASSWORD, nickname="鲍勃"
    )


@pytest.fixture
def admin_user(db):
    """超级管理员。"""
    from apps.users.models import User

    return User.objects.create_superuser(
        username="root", password=PASSWORD, email="root@example.com"
    )


@pytest.fixture
def auth_client(user):
    """已通过 TokenAuthentication 登录的客户端。"""
    from rest_framework.authtoken.models import Token
    from rest_framework.test import APIClient

    token, _created = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Token {}".format(token.key))
    return client


@pytest.fixture
def group(user):
    """由 user 创建的群组，user 是 admin，暂无其他成员。"""
    from apps.groups.services import GroupService

    return GroupService.create_group(user=user, name="测试群")


@pytest.fixture
def group_with_members(group, user, other_user):
    """group + other_user 已加入。"""
    from apps.groups.services import GroupService

    GroupService.invite_member(group, actor=user, username=other_user.username)
    return group
