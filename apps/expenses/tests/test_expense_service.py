"""支出业务规则测试（以 Service 为主，另含两条 HTTP 权限断言）。"""

from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.core.exceptions import BusinessError
from apps.expenses.models import Expense, ExpenseSplit, LedgerEntry
from apps.expenses.services import ExpenseService
from apps.groups.services import GroupService
from apps.users.models import User


@pytest.fixture
def carol(db):
    return User.objects.create_user(username="carol", password="Str0ngPassw0rd!")


@pytest.fixture
def outsider(db):
    return User.objects.create_user(username="outsider", password="Str0ngPassw0rd!")


@pytest.fixture
def trio(group_with_members, user, other_user, carol):
    """群组里已有 alice / bob，再把 carol 拉进来，返回三个用户。"""
    GroupService.invite_member(group_with_members, actor=user, username=carol.username)
    return [user, other_user, carol]


def _create(group, payer, participants, amount="300.00", split_type="EQUAL", splits=None):
    return ExpenseService.create_expense(
        group=group,
        actor=payer,
        payer=payer.id,
        amount=amount,
        split_type=split_type,
        participants=[member.id for member in participants],
        splits=splits,
    )


def _shares(expense):
    """{username: share_amount}，按创建顺序排列。"""
    return {
        split.user.username: split.share_amount
        for split in expense.splits.select_related("user").order_by("id")
    }


def _ledger(expense):
    return {
        row.user.username: row.amount
        for row in expense.ledger_entries.select_related("user")
    }


def _net_balances(group, viewer=None):
    """当前净余额 {username: Decimal}，默认用群组创建者视角。"""
    viewer = viewer or group.created_by
    report = GroupService.get_balances(group, viewer=viewer)
    return {item["username"]: item["net_balance"] for item in report["balances"]}


def _client_for(user):
    from rest_framework.authtoken.models import Token

    token, _created = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Token {}".format(token.key))
    return client


@pytest.mark.django_db
def test_equal_split_remainder(group_with_members, user, trio):
    """100 / 3：前两人 33.33，最后一人 33.34，合计严格等于 100。"""
    expense = _create(group_with_members, user, trio, amount="100.00")

    shares = _shares(expense)
    assert list(shares.values()) == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]
    assert sum(shares.values()) == Decimal("100.00")


@pytest.mark.django_db
def test_equal_split_min_amount(group_with_members, user, trio):
    """0.01 / 3：最后一人拿 0.01，其余为 0。"""
    expense = _create(group_with_members, user, trio, amount="0.01")

    shares = _shares(expense)
    assert list(shares.values()) == [Decimal("0.00"), Decimal("0.00"), Decimal("0.01")]
    assert sum(shares.values()) == Decimal("0.01")


@pytest.mark.django_db
def test_exact_split_valid(group_with_members, user, trio):
    """EXACT：份额合计等于总额时创建成功。"""
    splits = [
        {"user": trio[0].id, "share_amount": "100.00"},
        {"user": trio[1].id, "share_amount": "200.00"},
        {"user": trio[2].id, "share_amount": "300.00"},
    ]

    expense = _create(group_with_members, user, trio, amount="600.00", split_type="EXACT", splits=splits)

    assert _shares(expense) == {
        trio[0].username: Decimal("100.00"),
        trio[1].username: Decimal("200.00"),
        trio[2].username: Decimal("300.00"),
    }


@pytest.mark.django_db
def test_exact_split_sum_mismatch(group_with_members, user, trio):
    """EXACT：份额合计不等于总额时抛 split_sum_mismatch。"""
    splits = [
        {"user": trio[0].id, "share_amount": "100.00"},
        {"user": trio[1].id, "share_amount": "200.00"},
        {"user": trio[2].id, "share_amount": "299.99"},
    ]

    with pytest.raises(BusinessError) as excinfo:
        _create(group_with_members, user, trio, amount="600.00", split_type="EXACT", splits=splits)

    assert excinfo.value.code == "split_sum_mismatch"


@pytest.mark.django_db
def test_percentage_split_valid(group_with_members, user, trio):
    """PERCENTAGE：33.33 / 33.33 / 33.34 合计 100，份额与原始百分比都正确。"""
    splits = [
        {"user": trio[0].id, "share_value": "33.33"},
        {"user": trio[1].id, "share_value": "33.33"},
        {"user": trio[2].id, "share_value": "33.34"},
    ]

    expense = _create(group_with_members, user, trio, amount="100.00", split_type="PERCENTAGE", splits=splits)

    assert list(_shares(expense).values()) == [
        Decimal("33.33"),
        Decimal("33.33"),
        Decimal("33.34"),
    ]
    stored = list(expense.splits.order_by("id").values_list("share_value", flat=True))
    assert stored == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]


@pytest.mark.django_db
def test_percentage_split_sum_invalid(group_with_members, user, trio):
    """PERCENTAGE：百分比合计不是 100 时抛 percentage_sum_invalid。"""
    splits = [
        {"user": trio[0].id, "share_value": "33.33"},
        {"user": trio[1].id, "share_value": "33.33"},
        {"user": trio[2].id, "share_value": "33.33"},
    ]

    with pytest.raises(BusinessError) as excinfo:
        _create(group_with_members, user, trio, amount="100.00", split_type="PERCENTAGE", splits=splits)

    assert excinfo.value.code == "percentage_sum_invalid"


@pytest.mark.django_db
def test_payer_not_in_participants(group_with_members, user, trio):
    """payer 不在 participants 里：payer 额外记一条 +总额。"""
    expense = _create(group_with_members, user, [trio[1], trio[2]], amount="60.00")

    assert expense.splits.count() == 2
    assert _ledger(expense) == {
        user.username: Decimal("60.00"),
        trio[1].username: Decimal("-30.00"),
        trio[2].username: Decimal("-30.00"),
    }


@pytest.mark.django_db
def test_update_recalculates_balance(group_with_members, user, trio):
    """编辑金额后余额 = 「删除旧支出 + 创建新支出」的结果。"""
    expense = _create(group_with_members, user, trio, amount="300.00")
    ExpenseService.update_expense(
        expense, actor=user, amount="600.00", participants=[member.id for member in trio]
    )

    after_update = _net_balances(group_with_members)

    fresh_group = GroupService.create_group(user=user, name="对照群")
    for member in trio[1:]:
        GroupService.invite_member(fresh_group, actor=user, username=member.username)
    _create(fresh_group, user, trio, amount="600.00")
    fresh = _net_balances(fresh_group)

    assert after_update == fresh
    assert after_update[user.username] == Decimal("400.00")
    assert after_update[trio[1].username] == Decimal("-200.00")


@pytest.mark.django_db
def test_update_ledger_count_matches_participants(group_with_members, user, trio):
    """编辑后账本条目数等于参与人数，不是 2 倍。"""
    expense = _create(group_with_members, user, trio, amount="300.00")

    ExpenseService.update_expense(
        expense, actor=user, amount="450.00", participants=[member.id for member in trio]
    )

    assert LedgerEntry.objects.filter(expense=expense).count() == 3
    assert ExpenseSplit.objects.filter(expense=expense).count() == 3


@pytest.mark.django_db
def test_soft_delete_clears_ledger(group_with_members, user, other_user):
    """软删除后账本清空、余额归零，分摊明细保留。"""
    expense = _create(group_with_members, user, [user, other_user], amount="100.00")

    ExpenseService.soft_delete_expense(expense, actor=user)

    expense.refresh_from_db()
    assert expense.is_deleted is True
    assert LedgerEntry.objects.filter(expense=expense).count() == 0
    assert expense.splits.count() == 2
    assert _net_balances(group_with_members)[user.username] == Decimal("0.00")


@pytest.mark.django_db
def test_soft_delete_twice_returns_400(group_with_members, user, other_user):
    """重复软删除返回 400 already_deleted。"""
    expense = _create(group_with_members, user, [user, other_user], amount="100.00")
    ExpenseService.soft_delete_expense(expense, actor=user)

    with pytest.raises(BusinessError) as excinfo:
        ExpenseService.soft_delete_expense(expense, actor=user)

    assert excinfo.value.code == "already_deleted"


@pytest.mark.django_db
def test_non_member_get_expense_returns_404(group_with_members, user, outsider):
    """非群组成员查支出详情返回 404。"""
    expense = _create(group_with_members, user, [user], amount="50.00")

    response = _client_for(outsider).get("/api/expenses/{}/".format(expense.id))

    assert response.status_code == 404


@pytest.mark.django_db
def test_non_creator_patch_returns_403(group_with_members, user, other_user):
    """非录入人且非 admin 编辑支出返回 403。"""
    expense = _create(group_with_members, user, [user, other_user], amount="100.00")

    response = _client_for(other_user).patch(
        "/api/expenses/{}/".format(expense.id), {"amount": "200.00"}, format="json"
    )

    assert response.status_code == 403
    assert response.json()["code"] == "expense_forbidden"


@pytest.mark.django_db
def test_negative_amount_returns_400(group_with_members, user, other_user):
    """金额为负返回 400 invalid_amount。"""
    with pytest.raises(BusinessError) as excinfo:
        _create(group_with_members, user, [user, other_user], amount="-1.00")

    assert excinfo.value.code == "invalid_amount"
    assert excinfo.value.status_code == 400


@pytest.mark.django_db
def test_empty_participants_returns_400(group_with_members, user):
    """参与人为空返回 400 participants_required。"""
    with pytest.raises(BusinessError) as excinfo:
        _create(group_with_members, user, [], amount="100.00")

    assert excinfo.value.code == "participants_required"
    assert excinfo.value.status_code == 400
