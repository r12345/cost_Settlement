"""SettlementService 集成测试：真实落库，覆盖登记结算、建议结算与余额联动。"""

from decimal import Decimal

import pytest

from apps.core.exceptions import BusinessError
from apps.expenses.services import ExpenseService
from apps.groups.services import GroupService
from apps.settlements.models import Settlement
from apps.settlements.services import SettlementService
from apps.users.models import User

ZERO = Decimal("0.00")


@pytest.fixture
def members(db):
    """四个用户。"""
    return {
        username: User.objects.create_user(
            username=username, password="Str0ngPassw0rd!"
        )
        for username in ("alice", "bob", "carol", "dave")
    }


@pytest.fixture
def triangle(members):
    """4 人群组 + 3 笔支出，净余额 A+170 / B-40 / C-100 / D-30。"""
    alice = members["alice"]
    bob = members["bob"]
    carol = members["carol"]
    dave = members["dave"]

    group = GroupService.create_group(user=alice, name="三角债")
    for user in (bob, carol, dave):
        GroupService.invite_member(group, actor=alice, username=user.username)

    ExpenseService.create_expense(
        group=group,
        actor=alice,
        payer=alice.id,
        amount=Decimal("300.00"),
        split_type="EQUAL",
        participants=[alice.id, bob.id, carol.id],
    )
    ExpenseService.create_expense(
        group=group,
        actor=bob,
        payer=bob.id,
        amount=Decimal("90.00"),
        split_type="EQUAL",
        participants=[bob.id, carol.id, dave.id],
    )
    ExpenseService.create_expense(
        group=group,
        actor=carol,
        payer=carol.id,
        amount=Decimal("60.00"),
        split_type="EQUAL",
        participants=[alice.id, carol.id],
    )
    return group


@pytest.fixture
def three_way(members):
    """3 人群组 + 一笔 300 元 EQUAL：A+200 / B-100 / C-100。"""
    alice = members["alice"]
    bob = members["bob"]
    carol = members["carol"]

    group = GroupService.create_group(user=alice, name="三人饭局")
    for user in (bob, carol):
        GroupService.invite_member(group, actor=alice, username=user.username)

    ExpenseService.create_expense(
        group=group,
        actor=alice,
        payer=alice.id,
        amount=Decimal("300.00"),
        split_type="EQUAL",
        participants=[alice.id, bob.id, carol.id],
    )
    return group


def _relationship(group, viewer, other):
    """从 balances 报告里取 viewer 视角下 other 的两两明细。"""
    report = GroupService.get_balances(group, viewer=viewer)
    for item in report["relationships"]:
        if item["user_id"] == other.id:
            return item
    return None


@pytest.mark.django_db
def test_record_settlement_success(triangle, members):
    """C1：B 欠 A，转 40 元后 B 归零、A 降到 130。"""
    alice = members["alice"]
    bob = members["bob"]

    settlement = SettlementService.record_settlement(
        triangle, actor=bob, from_user_id=bob.id, to_user_id=alice.id, amount="40.00"
    )

    assert settlement.amount == Decimal("40.00")
    assert Settlement.objects.filter(group=triangle).count() == 1
    assert GroupService.get_user_net_balance(triangle, bob) == ZERO
    assert GroupService.get_user_net_balance(triangle, alice) == Decimal("130.00")


@pytest.mark.django_db
def test_record_settlement_reverse_direction_rejected(triangle, members):
    """C2：A 是债权人，不能反向付钱给 B。"""
    alice = members["alice"]
    bob = members["bob"]

    with pytest.raises(BusinessError) as excinfo:
        SettlementService.record_settlement(
            triangle, actor=alice, from_user_id=alice.id, to_user_id=bob.id, amount="10.00"
        )

    assert excinfo.value.code == "no_debt"


@pytest.mark.django_db
def test_record_settlement_after_settled_rejected(triangle, members):
    """C3：B 结清后再转，实际欠款为 0，拒绝。"""
    alice = members["alice"]
    bob = members["bob"]

    SettlementService.record_settlement(
        triangle, actor=bob, from_user_id=bob.id, to_user_id=alice.id, amount="40.00"
    )

    with pytest.raises(BusinessError) as excinfo:
        SettlementService.record_settlement(
            triangle, actor=bob, from_user_id=bob.id, to_user_id=alice.id, amount="10.00"
        )

    assert excinfo.value.code == "no_debt"


@pytest.mark.django_db
def test_record_settlement_over_debt(triangle, members):
    """C4：转账金额超过实际欠款 40.00。"""
    alice = members["alice"]
    bob = members["bob"]

    with pytest.raises(BusinessError) as excinfo:
        SettlementService.record_settlement(
            triangle, actor=bob, from_user_id=bob.id, to_user_id=alice.id, amount="100.00"
        )

    assert excinfo.value.code == "over_settle"
    assert "40.00" in excinfo.value.message


@pytest.mark.django_db
def test_record_settlement_same_user(triangle, members):
    """C5：转出方与接收方不能相同。"""
    alice = members["alice"]

    with pytest.raises(BusinessError) as excinfo:
        SettlementService.record_settlement(
            triangle, actor=alice, from_user_id=alice.id, to_user_id=alice.id, amount="10.00"
        )

    assert excinfo.value.code == "same_user"


@pytest.mark.django_db
@pytest.mark.parametrize("amount", ["0", "0.00", "-10"])
def test_record_settlement_invalid_amount(triangle, members, amount):
    """C6：金额必须大于 0。"""
    alice = members["alice"]
    bob = members["bob"]

    with pytest.raises(BusinessError) as excinfo:
        SettlementService.record_settlement(
            triangle, actor=bob, from_user_id=bob.id, to_user_id=alice.id, amount=amount
        )

    assert excinfo.value.code == "invalid_amount"


@pytest.mark.django_db
def test_suggestions_can_all_be_recorded_and_clear_balances(triangle, members):
    """D：按建议逐笔登记后，四个人全部归零，is_settled 为 True。"""
    alice = members["alice"]
    bob = members["bob"]

    transfers = SettlementService.suggest_settlements(triangle)
    assert len(transfers) <= 3

    for transfer in transfers:
        SettlementService.record_settlement(
            triangle,
            actor=members["alice"],
            from_user_id=transfer["from_user_id"],
            to_user_id=transfer["to_user_id"],
            amount=transfer["amount"],
        )

    report = GroupService.get_balances(triangle, viewer=alice)
    assert all(item["net_balance"] == ZERO for item in report["balances"])
    assert report["is_settled"] is True

    # D4：余额结清后允许退出
    GroupService.leave_group(triangle, user=bob)
    assert not triangle.members.filter(user=bob).exists()

    # D5：全员结清后 admin 可以删除群组
    GroupService.delete_group(triangle, actor=alice)


@pytest.mark.django_db
def test_pairwise_and_net_balance_follow_settlement(three_way, members):
    """E：登记 B->A 50 后，B 的净余额与「B 欠 A」都降为 50；再转 50 则消失。"""
    alice = members["alice"]
    bob = members["bob"]

    before = _relationship(three_way, alice, bob)
    assert before["net_balance"] == Decimal("100.00")

    SettlementService.record_settlement(
        three_way, actor=bob, from_user_id=bob.id, to_user_id=alice.id, amount="50.00"
    )

    assert GroupService.get_user_net_balance(three_way, bob) == Decimal("-50.00")
    after = _relationship(three_way, alice, bob)
    assert after["net_balance"] == Decimal("50.00")
    assert after["owes_me"] == Decimal("50.00")

    SettlementService.record_settlement(
        three_way, actor=bob, from_user_id=bob.id, to_user_id=alice.id, amount="50.00"
    )

    assert GroupService.get_user_net_balance(three_way, bob) == ZERO
    assert _relationship(three_way, alice, bob) is None
