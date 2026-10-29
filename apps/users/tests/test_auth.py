"""认证接口测试：注册 / 登录 / 登出。"""

import pytest
from rest_framework.test import APIClient

from apps.users.models import User

PASSWORD = "Str0ngPassw0rd!"


@pytest.mark.django_db
def test_register_success(api_client):
    """注册成功返回 201 + token + user。"""
    response = api_client.post(
        "/api/auth/register/",
        {
            "username": "newbie",
            "password": PASSWORD,
            "email": "newbie@example.com",
            "nickname": "新用户",
        },
        format="json",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["token"]
    assert body["user"]["username"] == "newbie"
    assert body["user"]["nickname"] == "新用户"


@pytest.mark.django_db
def test_register_duplicate_username(api_client, user):
    """用户名重复返回 400 username_taken。"""
    response = api_client.post(
        "/api/auth/register/", {"username": user.username, "password": PASSWORD}, format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "username_taken"


@pytest.mark.django_db
def test_register_weak_password(api_client):
    """纯数字密码过不了密码校验，返回 400 weak_password。"""
    response = api_client.post(
        "/api/auth/register/", {"username": "weakling", "password": "123456"}, format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "weak_password"


@pytest.mark.django_db
def test_login_success(api_client, user):
    """正确凭证登录返回 200 + token。"""
    response = api_client.post(
        "/api/auth/login/", {"username": user.username, "password": PASSWORD}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["token"]


@pytest.mark.django_db
def test_login_invalid_credentials(api_client, user):
    """密码错误返回 400 invalid_credentials。"""
    response = api_client.post(
        "/api/auth/login/", {"username": user.username, "password": "wrong-pass"}, format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_credentials"


@pytest.mark.django_db
def test_logout_deletes_token(user):
    """登出后令牌被删除，旧 token 访问受保护接口返回 401。"""
    from rest_framework.authtoken.models import Token

    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Token {}".format(token.key))

    assert client.post("/api/auth/logout/").status_code == 204
    assert not Token.objects.filter(user=user).exists()

    stale = APIClient()
    stale.credentials(HTTP_AUTHORIZATION="Token {}".format(token.key))
    assert stale.get("/api/groups/").status_code == 401


@pytest.mark.django_db
def test_protected_endpoint_without_token(api_client):
    """未携带令牌访问受保护接口返回 401。"""
    assert api_client.get("/api/groups/").status_code == 401


@pytest.mark.django_db
def test_nickname_saved(api_client):
    """中文昵称正确落库。"""
    response = api_client.post(
        "/api/auth/register/",
        {"username": "zhangsan", "password": PASSWORD, "nickname": "张三"},
        format="json",
    )

    assert response.status_code == 201
    assert User.objects.get(username="zhangsan").nickname == "张三"
