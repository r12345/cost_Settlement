"""群组端点测试：CRUD、邀请、退出、移除、余额与权限边界。"""

import pytest
from rest_framework.test import APIClient

from apps.groups.models import Group, GroupMember


def _client_for(user):
    """为指定用户构造一个带 Token 的客户端。"""
    from rest_framework.authtoken.models import Token

    token, _created = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Token {}".format(token.key))
    return client


@pytest.mark.django_db
def test_list_groups(auth_client, group):
    """GET /api/groups/ 只返回自己加入的群组。"""
    response = auth_client.get("/api/groups/")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["results"][0]["id"] == group.id


@pytest.mark.django_db
def test_create_group(auth_client):
    """POST /api/groups/ 创建群组，创建者是 admin。"""
    response = auth_client.post(
        "/api/groups/", {"name": "川西自驾", "description": "国庆"}, format="json"
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "川西自驾"
    assert body["my_role"] == "admin"
    assert body["member_count"] == 1


@pytest.mark.django_db
def test_retrieve_group(auth_client, group):
    """GET /api/groups/{id}/ 返回详情。"""
    response = auth_client.get("/api/groups/{}/".format(group.id))

    assert response.status_code == 200
    assert response.json()["id"] == group.id


@pytest.mark.django_db
def test_update_group(auth_client, group):
    """PATCH /api/groups/{id}/ 修改名称。"""
    response = auth_client.patch(
        "/api/groups/{}/".format(group.id), {"name": "改名了"}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["name"] == "改名了"


@pytest.mark.django_db
def test_delete_group(auth_client, group):
    """DELETE /api/groups/{id}/ 删除群组。"""
    group_id = group.id

    response = auth_client.delete("/api/groups/{}/".format(group_id))

    assert response.status_code == 204
    assert not Group.objects.filter(pk=group_id).exists()


@pytest.mark.django_db
def test_invite_member(auth_client, group, other_user):
    """POST /api/groups/{id}/invite/ 邀请成员。"""
    response = auth_client.post(
        "/api/groups/{}/invite/".format(group.id),
        {"username": other_user.username},
        format="json",
    )

    assert response.status_code == 201
    assert response.json()["role"] == "member"


@pytest.mark.django_db
def test_leave_group(group_with_members, other_user):
    """POST /api/groups/{id}/leave/ 余额为 0 时退出成功。"""
    client = _client_for(other_user)

    response = client.post("/api/groups/{}/leave/".format(group_with_members.id))

    assert response.status_code == 200
    assert response.json()["detail"] == "已退出群组"


@pytest.mark.django_db
def test_remove_member(auth_client, group_with_members, other_user):
    """DELETE /api/groups/{id}/members/{uid}/ 管理员移除成员。"""
    response = auth_client.delete(
        "/api/groups/{}/members/{}/".format(group_with_members.id, other_user.id)
    )

    assert response.status_code == 204
    assert not GroupMember.objects.filter(
        group=group_with_members, user=other_user
    ).exists()


@pytest.mark.django_db
def test_balances_endpoint(auth_client, group_with_members):
    """GET /api/groups/{id}/balances/ 返回净余额结构。"""
    response = auth_client.get("/api/groups/{}/balances/".format(group_with_members.id))

    assert response.status_code == 200
    body = response.json()
    assert body["group_id"] == group_with_members.id
    assert body["is_settled"] is True
    assert len(body["balances"]) == 2
    assert body["relationships"] == []


@pytest.mark.django_db
def test_non_admin_cannot_update(group_with_members, other_user):
    """非 admin 编辑群组返回 403。"""
    client = _client_for(other_user)

    response = client.patch(
        "/api/groups/{}/".format(group_with_members.id), {"name": "我来改"}, format="json"
    )

    assert response.status_code == 403
    assert response.json()["code"] == "admin_required"


@pytest.mark.django_db
def test_non_admin_cannot_delete(group_with_members, other_user):
    """非 admin 删除群组返回 403。"""
    client = _client_for(other_user)

    response = client.delete("/api/groups/{}/".format(group_with_members.id))

    assert response.status_code == 403
    assert response.json()["code"] == "admin_required"


@pytest.mark.django_db
def test_non_member_cannot_view(group, other_user):
    """非成员访问群组详情返回 404。"""
    client = _client_for(other_user)

    response = client.get("/api/groups/{}/".format(group.id))

    assert response.status_code == 404
