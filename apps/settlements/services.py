"""结算领域逻辑：债务简化纯函数 + 实际结算登记。

分层约定：

* :func:`simplify_debts` 是纯函数，只吃 ``dict[int, Decimal]``、只吐 ``list[dict]``，
  函数体内没有任何 ORM 调用，可以脱离数据库单独测试；
* 余额口径不在本模块重复实现，一律调用 :class:`apps.groups.services.GroupService`；
* 结算建议永远不落库，只有 :meth:`SettlementService.record_settlement` 会写库。
"""

import heapq
from collections import defaultdict

from django.db import transaction

from apps.core.exceptions import BusinessError
from apps.core.utils import ZERO, to_money
from apps.groups.models import GroupMember
from apps.groups.services import GroupService
from apps.settlements.models import Settlement
from apps.users.models import User


def simplify_debts(net_balances):
    """用双优先队列贪心把净余额压缩成尽量少的转账笔数。

    :param net_balances: ``{user_id: Decimal}``，正数表示应收、负数表示应付，
                         合计必须严格等于 0；净余额为 0 的用户会被直接忽略。
    :return: ``[{"from_user_id": int, "to_user_id": int, "amount": Decimal}, ...]``，
             每条 amount 均大于 0，转出侧合计 == 接收侧合计。
    :raises BusinessError: 输入合计不为 0（code=unbalanced_balances），
                           或输出未通过不变量自检（code=settlement_invariant_violated）。

    两堆都以负值入堆：heapq 是最小堆，取负之后弹出的就是绝对值最大的一方，
    这正是贪心「每次取欠款最多的人转给应收最多的人」所需要的顺序。
    """
    balances = {}
    for user_id, amount in net_balances.items():
        value = to_money(amount)
        if value != ZERO:
            balances[user_id] = value

    total = sum(balances.values(), ZERO)
    if total != ZERO:
        raise BusinessError(
            "净余额合计必须为 0，当前为 {}".format(total), code="unbalanced_balances"
        )

    if not balances:
        return []

    debtors = []  # (负数欠款额, user_id)，弹出绝对值最大的债务人
    creditors = []  # (负数应收额, user_id)，弹出应收最多的人
    for user_id, amount in balances.items():
        if amount < ZERO:
            heapq.heappush(debtors, (amount, user_id))
        else:
            heapq.heappush(creditors, (-amount, user_id))

    transfers = []
    while debtors and creditors:
        negative_debt, debtor_id = heapq.heappop(debtors)
        negative_credit, creditor_id = heapq.heappop(creditors)
        debt = -negative_debt
        credit = -negative_credit

        amount = min(debt, credit)
        transfers.append(
            {
                "from_user_id": debtor_id,
                "to_user_id": creditor_id,
                "amount": to_money(amount),
            }
        )

        remaining_debt = debt - amount
        remaining_credit = credit - amount
        if remaining_debt > ZERO:
            heapq.heappush(debtors, (-remaining_debt, debtor_id))
        if remaining_credit > ZERO:
            heapq.heappush(creditors, (-remaining_credit, creditor_id))

    _assert_settlement_invariant(balances, transfers)
    return transfers


def _assert_settlement_invariant(net_balances, transfers):
    """校验转账方案确实把每个人归零，且笔数不超过「非零人数 - 1」。

    符号约定：``net_balance`` 正数表示应收。转出方付出金额后欠款减少、
    余额上升，接收方收到金额后余额下降，因此归零条件是
    ``balance + outbound - inbound == 0``。
    """
    inbound = defaultdict(lambda: ZERO)
    outbound = defaultdict(lambda: ZERO)

    for transfer in transfers:
        if transfer["from_user_id"] == transfer["to_user_id"]:
            raise BusinessError(
                "结算方案出现自己转给自己：用户 {}".format(transfer["from_user_id"]),
                code="settlement_invariant_violated",
            )
        if transfer["amount"] <= ZERO:
            raise BusinessError(
                "结算方案出现非正数金额：{}".format(transfer["amount"]),
                code="settlement_invariant_violated",
            )
        outbound[transfer["from_user_id"]] += transfer["amount"]
        inbound[transfer["to_user_id"]] += transfer["amount"]

    total_out = sum(outbound.values(), ZERO)
    total_in = sum(inbound.values(), ZERO)
    if total_out != total_in:
        raise BusinessError(
            "结算方案转出合计 {} 与转入合计 {} 不一致".format(total_out, total_in),
            code="settlement_invariant_violated",
        )

    for user_id, balance in net_balances.items():
        if balance + outbound[user_id] - inbound[user_id] != ZERO:
            raise BusinessError(
                "结算方案未把用户 {} 的余额归零".format(user_id),
                code="settlement_invariant_violated",
            )

    non_zero = len([balance for balance in net_balances.values() if balance != ZERO])
    if len(transfers) > max(non_zero - 1, 0):
        raise BusinessError(
            "结算方案用了 {} 笔转账，超过理论上限 {}".format(
                len(transfers), max(non_zero - 1, 0)
            ),
            code="settlement_invariant_violated",
        )


class SettlementService:
    """结算建议与实际结算登记。"""

    @staticmethod
    def suggest_settlements(group):
        """基于当前净余额生成结算建议（只读，不落库）。"""
        net_balances = GroupService.get_net_balances(group)
        non_zero = {
            user_id: amount
            for user_id, amount in net_balances.items()
            if amount != ZERO
        }
        transfers = simplify_debts(non_zero)

        involved = set()
        for transfer in transfers:
            involved.add(transfer["from_user_id"])
            involved.add(transfer["to_user_id"])
        usernames = dict(
            User.objects.filter(pk__in=involved).values_list("id", "username")
        )

        return [
            {
                "from_user_id": transfer["from_user_id"],
                "from_username": usernames.get(transfer["from_user_id"], ""),
                "to_user_id": transfer["to_user_id"],
                "to_username": usernames.get(transfer["to_user_id"], ""),
                "amount": transfer["amount"],
            }
            for transfer in transfers
        ]

    @staticmethod
    @transaction.atomic
    def record_settlement(group, actor, from_user_id, to_user_id, amount):
        """登记一笔实际结算。

        校验顺序：操作者身份 -> 双方不同 -> 金额为正 -> 双方都是成员
        -> 转出方确实欠款（净余额为负）-> 接收方确实是债权人 -> 金额不超过欠款。

        「实际欠款」取转出方当前的整体净欠款（净余额取负）。这里刻意不做
        「必须欠给这个具体的人」的强校验：结算建议由 ``simplify_debts`` 基于
        净余额生成，可能出现 C 欠 A 70、欠 B 30，但建议 C 直接转 100 给 A 的
        情况（A 是净债权人）。若按两两方向强校验，系统自己给出的建议会被自己
        拒绝。因此只要求「转出方是净债务人、接收方是净债权人、金额不超过转出方
        的净欠款」这三条，既不会让人多付，也不会出现债务人付钱给另一个债务人。
        """
        GroupService.require_member(group, actor)

        if from_user_id == to_user_id:
            raise BusinessError("转出方和接收方不能是同一个人", code="same_user")

        total = to_money(amount)
        if total <= ZERO:
            raise BusinessError("结算金额必须大于 0", code="invalid_amount")

        debtor = SettlementService._require_member_user(
            group, from_user_id, "转出方"
        )
        creditor = SettlementService._require_member_user(
            group, to_user_id, "接收方"
        )

        debtor_balance = GroupService.get_user_net_balance(group, debtor)
        creditor_balance = GroupService.get_user_net_balance(group, creditor)
        owed_amount = -debtor_balance if debtor_balance < ZERO else ZERO

        if owed_amount <= ZERO:
            raise BusinessError(
                "{} 当前没有待结清的欠款（净余额 {}），无法登记结算".format(
                    debtor.display_name, debtor_balance
                ),
                code="no_debt",
            )

        if creditor_balance <= ZERO:
            raise BusinessError(
                "{} 当前不是债权人（净余额 {}），不能接收结算".format(
                    creditor.display_name, creditor_balance
                ),
                code="no_debt",
            )

        if total > owed_amount:
            raise BusinessError(
                "结算金额 {} 超过实际欠款 {}，请核对后重新提交".format(
                    total, owed_amount
                ),
                code="over_settle",
            )

        return Settlement.objects.create(
            group=group, from_user=debtor, to_user=creditor, amount=total
        )

    @staticmethod
    def _require_member_user(group, user_id, label):
        """确认 user_id 是该群组成员，返回 User 实例。"""
        user = User.objects.filter(pk=user_id).first()
        if user is None or not GroupMember.objects.filter(
            group=group, user=user
        ).exists():
            raise BusinessError(
                "{}（id={}）不是该群组成员".format(label, user_id),
                code="not_group_member",
            )
        return user
