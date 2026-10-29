"""simplify_debts 纯函数测试。

这些用例不连数据库，所以没有 ``@pytest.mark.django_db``，也不需要任何 fixture。
"""

from decimal import Decimal

import pytest

from apps.core.exceptions import BusinessError
from apps.settlements.services import simplify_debts


def _flows(net_balances, transfers):
    """按转账结果回算每个用户的净变化：转出使余额上升，接收使余额下降。"""
    flows = {user_id: Decimal("0.00") for user_id in net_balances}
    for transfer in transfers:
        flows[transfer["from_user_id"]] += transfer["amount"]
        flows[transfer["to_user_id"]] -= transfer["amount"]
    return flows


def test_empty_input():
    """空输入直接返回空列表。"""
    assert simplify_debts({}) == []


def test_all_zero():
    """所有人净余额为 0 时不需要任何转账。"""
    assert simplify_debts({1: Decimal("0"), 2: Decimal("0.00")}) == []


def test_single_debtor_creditor():
    """一人欠一人：一笔转账即可清零。"""
    transfers = simplify_debts({1: Decimal("-100"), 2: Decimal("100")})

    assert len(transfers) == 1
    assert transfers[0]["from_user_id"] == 1
    assert transfers[0]["to_user_id"] == 2
    assert transfers[0]["amount"] == Decimal("100.00")
    assert isinstance(transfers[0]["amount"], Decimal)


def test_one_debtor_multiple_creditors():
    """一人欠多人：每个债权人各收一笔。"""
    net = {
        1: Decimal("-300"),
        2: Decimal("100"),
        3: Decimal("100"),
        4: Decimal("100"),
    }
    transfers = simplify_debts(net)

    assert len(transfers) == 3
    assert all(transfer["from_user_id"] == 1 for transfer in transfers)
    assert {transfer["to_user_id"] for transfer in transfers} == {2, 3, 4}
    assert all(transfer["amount"] == Decimal("100.00") for transfer in transfers)


def test_cross_debts():
    """交叉债务：笔数不超过 N-1，且转账后每个人都归零。"""
    net = {
        1: Decimal("-100"),
        2: Decimal("-100"),
        3: Decimal("50"),
        4: Decimal("150"),
    }
    transfers = simplify_debts(net)

    assert len(transfers) <= len(net) - 1

    flows = _flows(net, transfers)
    for user_id, balance in net.items():
        assert balance + flows[user_id] == 0
    assert sum(flows.values()) == Decimal("0.00")


def test_decimal_precision():
    """含小数的余额也必须精确，不能出现浮点误差。"""
    net = {
        1: Decimal("-33.33"),
        2: Decimal("-33.33"),
        3: Decimal("-33.34"),
        4: Decimal("100.00"),
    }
    transfers = simplify_debts(net)

    assert sum(transfer["amount"] for transfer in transfers) == Decimal("100.00")
    for transfer in transfers:
        assert isinstance(transfer["amount"], Decimal)
        assert transfer["amount"] == transfer["amount"].quantize(Decimal("0.01"))

    flows = _flows(net, transfers)
    for user_id, balance in net.items():
        assert balance + flows[user_id] == 0


def test_unbalanced_raises():
    """输入合计不为 0 时抛 unbalanced_balances，而不是静默返回。"""
    with pytest.raises(BusinessError) as excinfo:
        simplify_debts({1: Decimal("-100"), 2: Decimal("99.99")})

    assert excinfo.value.code == "unbalanced_balances"
