"""GroupService 业务规则测试。"""

from decimal import Decimal

import pytest

from apps.core.exceptions import BusinessError, ForbiddenError, NotFoundError
from apps.expenses.services import ExpenseService
from apps.groups.models import Group, GroupMember
from apps.groups.services import GroupService


def _add_expense(group, payer, participants, amount="100.00"):
    """在群组里记一笔等额分摊的支出。"""
    return ExpenseService.create_expense(
        group=group,
        actor=payer,
        payer=payer.id,
        amount=amount,
        split_type="EQUAL",
        participants=[member.id for member in participants],
    )


def _net_balance(group, viewer, member):
    """从 balances 报告里取某个成员的净余额。"""
    report = GroupService.get_balances(group, viewer=viewer)
    for item in report["balances"]:
        if item["user_id"] == member.id:
            return item["net_balance"]
    return None


@pytest.mark.django_db
def test_create_group_sets_creator_as_admin(user):
    """创建者自动成为 admin 成员。"""
    group = GroupService.create_group(user=user, name="川西自驾")

    membership = group.members.get(user=user)
    assert membership.role == GroupMember.Role.ADMIN
    assert group.created_by_id == user.id


@pytest.mark.django_db
def test_non_member_access_returns_404(group, other_user):
    """非成员访问群组返回 404。"""
    with pytest.raises(NotFoundError) as excinfo:
        GroupService.require_member(group, other_user)

    assert excinfo.value.status_code == 404


@pytest.mark.django_db
def test_invite_success(group, user, other_user):
    """邀请成员成功，角色为 member。"""
    membership = GroupService.invite_member(group, actor=user, username=other_user.username)

    assert membership.role == GroupMember.Role.MEMBER
    assert GroupMember.objects.filter(group=group, user=other_user).exists()


@pytest.mark.django_db
def test_invite_duplicate_returns_400(group, user, other_user):
    """重复邀请返回 400 already_member。"""
    GroupService.invite_member(group, actor=user, username=other_user.username)

    with pytest.raises(BusinessError) as excinfo:
        GroupService.invite_member(group, actor=user, username=other_user.username)

    assert excinfo.value.code == "already_member"
    assert excinfo.value.status_code == 400


@pytest.mark.django_db
def test_invite_unknown_user_returns_400(group, user):
    """邀请不存在的用户返回 400 user_not_found。"""
    with pytest.raises(BusinessError) as excinfo:
        GroupService.invite_member(group, actor=user, username="ghost")

    assert excinfo.value.code == "user_not_found"
    assert excinfo.value.status_code == 400


@pytest.mark.django_db
def test_leave_with_zero_balance_succeeds(group_with_members, other_user):
    """余额为 0 时退出成功。"""
    GroupService.leave_group(group_with_members, user=other_user)

    assert not GroupMember.objects.filter(group=group_with_members, user=other_user).exists()


@pytest.mark.django_db
def test_leave_with_nonzero_balance_returns_400(group_with_members, user, other_user):
    """有欠款时退出被拦截。"""
    _add_expense(group_with_members, user, [user, other_user])

    with pytest.raises(BusinessError) as excinfo:
        GroupService.leave_group(group_with_members, user=other_user)

    assert excinfo.value.code == "unsettled_balance"
    assert "50.00" in excinfo.value.message


@pytest.mark.django_db
def test_remove_member_by_admin_succeeds(group_with_members, user, other_user):
    """管理员移除成员成功。"""
    GroupService.remove_member(group_with_members, actor=user, user_id=other_user.id)

    assert not GroupMember.objects.filter(group=group_with_members, user=other_user).exists()


@pytest.mark.django_db
def test_remove_member_by_non_admin_returns_403(group_with_members, user, other_user):
    """非管理员不能移除成员。"""
    with pytest.raises(ForbiddenError) as excinfo:
        GroupService.remove_member(group_with_members, actor=other_user, user_id=user.id)

    assert excinfo.value.code == "admin_required"
    assert excinfo.value.status_code == 403


@pytest.mark.django_db
def test_admin_cannot_remove_self_returns_400(group_with_members, user):
    """管理员移除自己返回 400。"""
    with pytest.raises(BusinessError) as excinfo:
        GroupService.remove_member(group_with_members, actor=user, user_id=user.id)

    assert excinfo.value.code == "use_leave_endpoint"
    assert excinfo.value.status_code == 400


@pytest.mark.django_db
def test_delete_group_with_nonzero_balance_returns_400(group_with_members, user, other_user):
    """有欠款时删除群组被拦截。"""
    _add_expense(group_with_members, user, [user, other_user])

    with pytest.raises(BusinessError) as excinfo:
        GroupService.delete_group(group_with_members, actor=user)

    assert excinfo.value.code == "unsettled_balance"


@pytest.mark.django_db
def test_last_member_leave_deletes_group(group, user):
    """最后一名成员退出时群组被删除。"""
    group_id = group.id

    GroupService.leave_group(group, user=user)

    assert not Group.objects.filter(pk=group_id).exists()


@pytest.mark.django_db
def test_admin_leave_transfers_to_earliest_member(group_with_members, user, other_user):
    """管理员退出后，最早加入的成员接任管理员。"""
    GroupService.leave_group(group_with_members, user=user)

    membership = GroupMember.objects.get(group=group_with_members, user=other_user)
    assert membership.role == GroupMember.Role.ADMIN


@pytest.mark.django_db
def test_balances_all_zero_when_no_expense(group_with_members, user, other_user):
    """没有支出时所有人净余额为 0，两两明细为空。"""
    report = GroupService.get_balances(group_with_members, viewer=user)

    assert [item["net_balance"] for item in report["balances"]] == [
        Decimal("0.00"),
        Decimal("0.00"),
    ]
    assert report["relationships"] == []
    assert report["is_settled"] is True


@pytest.mark.django_db
def test_balances_reflects_expense(group_with_members, user, other_user):
    """一笔 100 元 AA 后，两人各 +50 / -50，并出现一条两两明细。"""
    _add_expense(group_with_members, user, [user, other_user], amount="100.00")

    report = GroupService.get_balances(group_with_members, viewer=user)

    assert _net_balance(group_with_members, user, user) == Decimal("50.00")
    assert _net_balance(group_with_members, user, other_user) == Decimal("-50.00")
    assert len(report["relationships"]) == 1
    assert report["relationships"][0]["user_id"] == other_user.id
    assert report["relationships"][0]["owes_me"] == Decimal("50.00")
    assert report["is_settled"] is False
