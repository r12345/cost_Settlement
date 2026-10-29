"""支出领域业务逻辑：分摊计算、账本生成、编辑与软删除。

分层约定：

* 视图只做认证、鉴权取对象与响应整形，不出现任何金额计算；
* 金额一律 Decimal，统一量化到分（``apps.core.utils.to_money``）；
* 余额口径只由 :class:`apps.groups.services.GroupService` 提供，本模块不重复实现，
  需要余额时直接调用它的 ``get_user_net_balance`` / ``get_balances``。

账本语义：LedgerEntry.amount 为正表示该用户应收增加（垫付超过自己的份额），
为负表示应付增加（自己的份额由别人垫付了），同一笔支出的账本分录之和恒为 0。
"""

from decimal import ROUND_DOWN, Decimal

from django.db import transaction
from django.db.models import Sum

from apps.core.exceptions import BusinessError, ForbiddenError
from apps.core.utils import CENT, ZERO, to_money
from apps.expenses.models import Expense, ExpenseSplit, LedgerEntry
from apps.groups.models import GroupMember
from apps.groups.services import GroupService
from apps.users.models import User

PERCENT_BASE = Decimal("100")


class ExpenseService:
    """支出的创建、编辑与软删除。"""

    # ------------------------------------------------------------------
    # 对外方法
    # ------------------------------------------------------------------
    @staticmethod
    @transaction.atomic
    def create_expense(
        group,
        actor,
        payer,
        amount,
        split_type,
        participants,
        splits=None,
        note="",
        category=Expense.Category.OTHER,
    ):
        """创建一笔支出并生成分摊与账本。

        :param group: 目标群组
        :param actor: 录入人（必须是群组成员）
        :param payer: 付款人的 user_id（必须是群组成员）
        :param amount: 支出总额，字符串/数字/Decimal 均可
        :param split_type: EQUAL / EXACT / PERCENTAGE
        :param participants: 参与人 user_id 列表（顺序决定「最后一人」）
        :param splits: EXACT / PERCENTAGE 时的分摊明细，EQUAL 时忽略
        :raises BusinessError: 金额、成员、分摊合计等任何业务校验失败
        """
        GroupService.require_member(group, actor)

        payer_user = ExpenseService._require_group_user(
            group, payer, label="付款人", code="payer_not_member"
        )
        total = ExpenseService._validate_amount(amount)
        participant_ids = ExpenseService._normalize_participants(group, participants)

        shares = ExpenseService._calculate_splits(
            total, split_type, participant_ids, splits
        )

        expense = Expense.objects.create(
            group=group,
            payer=payer_user,
            amount=total,
            split_type=split_type,
            note=(note or "").strip(),
            category=category,
            created_by=actor,
        )
        ExpenseService._create_splits(expense, shares)
        ExpenseService._create_ledger_entries(expense, payer_user.id, shares, total)
        ExpenseService._assert_split_sum(expense, total)

        # 后置生成语义检索用的 embedding：upsert 内部吞掉所有异常，失败不影响支出
        from apps.ai.services import ExpenseEmbeddingService

        ExpenseEmbeddingService.upsert(expense)
        return expense

    @staticmethod
    @transaction.atomic
    def update_expense(
        expense,
        actor,
        amount=None,
        split_type=None,
        participants=None,
        splits=None,
        note=None,
        category=None,
    ):
        """编辑支出：同一事务内删旧账本/旧分摊 → 重新计算 → 重建。

        只允许改动金额、分摊方式、参与人、分摊明细、备注与分类；
        payer 与 group 不可变（改了等于换了一笔支出，应当删除后重建）。
        """
        ExpenseService._assert_can_modify(expense, actor)
        if expense.is_deleted:
            raise BusinessError("该支出已被删除，无法编辑", code="already_deleted")

        existing_splits = list(
            expense.splits.order_by("id").values(
                "user_id", "share_amount", "share_value"
            )
        )

        new_split_type = split_type or expense.split_type
        new_amount = ExpenseService._validate_amount(
            expense.amount if amount is None else amount
        )

        if participants is not None:
            participant_ids = ExpenseService._normalize_participants(
                expense.group, participants
            )
        else:
            participant_ids = [row["user_id"] for row in existing_splits]
            if not participant_ids:
                raise BusinessError(
                    "支出缺少参与人信息，请显式传入 participants",
                    code="participants_required",
                )

        if splits is None:
            splits = ExpenseService._inherited_splits(existing_splits, new_split_type)

        shares = ExpenseService._calculate_splits(
            new_amount, new_split_type, participant_ids, splits
        )

        # 1) 删除旧账本分录，2) 删除旧分摊明细
        LedgerEntry.objects.filter(expense=expense).delete()
        ExpenseSplit.objects.filter(expense=expense).delete()

        # 3~4) 用新参数重建分摊与账本
        ExpenseService._create_splits(expense, shares)
        ExpenseService._create_ledger_entries(
            expense, expense.payer_id, shares, new_amount
        )

        # 5) 更新支出本身
        expense.amount = new_amount
        expense.split_type = new_split_type
        if note is not None:
            expense.note = note.strip()
        if category is not None:
            expense.category = category
        expense.save(update_fields=["amount", "split_type", "note", "category"])

        # 6) 兜底断言：分摊合计必须精确等于支出总额
        ExpenseService._assert_split_sum(expense, new_amount)

        # 后置刷新 embedding（备注 / 分类 / 参与人都可能变了）
        from apps.ai.services import ExpenseEmbeddingService

        ExpenseEmbeddingService.upsert(expense)
        return expense

    @staticmethod
    @transaction.atomic
    def soft_delete_expense(expense, actor):
        """软删除支出：标记 is_deleted 并清空账本分录，保留分摊历史。"""
        ExpenseService._assert_can_modify(expense, actor)
        if expense.is_deleted:
            raise BusinessError("该支出已被删除", code="already_deleted")

        expense.is_deleted = True
        expense.save(update_fields=["is_deleted"])
        LedgerEntry.objects.filter(expense=expense).delete()

    # ------------------------------------------------------------------
    # 分摊算法
    # ------------------------------------------------------------------
    @staticmethod
    def _calculate_splits(amount, split_type, participant_ids, splits):
        """按分摊方式计算份额，返回 [(user_id, share_amount, share_value)]。

        返回值严格保持 ``participant_ids`` 的传入顺序，「最后一人」即列表末尾。
        """
        if split_type == Expense.SplitType.EQUAL:
            return ExpenseService._split_equally(amount, participant_ids)
        if split_type == Expense.SplitType.EXACT:
            return ExpenseService._split_exactly(amount, participant_ids, splits)
        if split_type == Expense.SplitType.PERCENTAGE:
            return ExpenseService._split_by_percentage(amount, participant_ids, splits)
        raise BusinessError(
            "不支持的分摊方式：{}".format(split_type), code="invalid_split_type"
        )

    @staticmethod
    def _split_equally(amount, participant_ids):
        """等额分摊：前 n-1 人取向下取整的份额，余数全部由最后一人承担。"""
        count = len(participant_ids)
        base = (amount / count).quantize(CENT, rounding=ROUND_DOWN)

        shares = []
        for index, user_id in enumerate(participant_ids):
            if index < count - 1:
                share = base
            else:
                share = amount - base * (count - 1)
            shares.append((user_id, share, None))
        return shares

    @staticmethod
    def _split_exactly(amount, participant_ids, splits):
        """精确金额分摊：每人金额由调用方给出，合计必须等于总额。"""
        mapping = ExpenseService._build_value_mapping(
            participant_ids, splits, value_field="share_amount"
        )

        total = sum(mapping.values(), ZERO)
        if total != amount:
            raise BusinessError(
                "分摊金额合计 {} 与支出总额 {} 不一致".format(total, amount),
                code="split_sum_mismatch",
            )
        return [(user_id, mapping[user_id], None) for user_id in participant_ids]

    @staticmethod
    def _split_by_percentage(amount, participant_ids, splits):
        """百分比分摊：百分比合计必须为 100，尾差由最后一人承担。"""
        mapping = ExpenseService._build_value_mapping(
            participant_ids, splits, value_field="share_value"
        )

        total_percent = sum(mapping.values(), ZERO)
        if total_percent != PERCENT_BASE:
            raise BusinessError(
                "百分比合计必须为 100，当前为 {}".format(total_percent),
                code="percentage_sum_invalid",
            )

        last_index = len(participant_ids) - 1
        allocated = ZERO
        shares = []
        for index, user_id in enumerate(participant_ids):
            percent = mapping[user_id]
            if index < last_index:
                share = (amount * percent / PERCENT_BASE).quantize(
                    CENT, rounding=ROUND_DOWN
                )
                allocated += share
            else:
                share = amount - allocated
            shares.append((user_id, share, percent))
        return shares

    @staticmethod
    def _build_value_mapping(participant_ids, splits, value_field):
        """把 splits 列表转成 {user_id: Decimal}，并校验成员集合一致。"""
        if not splits:
            raise BusinessError(
                "当前分摊方式必须提供 splits（每人 {}）".format(value_field),
                code="splits_required",
            )

        mapping = {}
        for item in splits:
            user_id = item["user"]
            value = item.get(value_field)
            if value is None:
                raise BusinessError(
                    "splits 中用户 {} 缺少 {}".format(user_id, value_field),
                    code="splits_required",
                )
            if user_id in mapping:
                raise BusinessError(
                    "splits 中用户 {} 重复出现".format(user_id),
                    code="participant_mismatch",
                )
            if user_id not in participant_ids:
                raise BusinessError(
                    "splits 中的用户 {} 不在 participants 中".format(user_id),
                    code="participant_mismatch",
                )
            mapping[user_id] = to_money(value)

        if set(mapping) != set(participant_ids):
            raise BusinessError(
                "splits 的成员集合与 participants 不一致",
                code="participant_mismatch",
            )
        return mapping

    # ------------------------------------------------------------------
    # 落库与校验
    # ------------------------------------------------------------------
    @staticmethod
    def _create_splits(expense, shares):
        """批量写入分摊明细。"""
        ExpenseSplit.objects.bulk_create(
            [
                ExpenseSplit(
                    expense=expense,
                    user_id=user_id,
                    share_amount=share_amount,
                    share_value=share_value,
                )
                for user_id, share_amount, share_value in shares
            ]
        )

    @staticmethod
    def _create_ledger_entries(expense, payer_id, shares, total):
        """按「payer 垫付 - 自己份额」生成每个参与人的账本分录。

        payer 不在 participants 中时（替别人垫付），额外为 payer 记一条 +总额的分录。
        """
        entries = []
        share_by_user = {user_id: share for user_id, share, _value in shares}

        for user_id, share_amount, _value in shares:
            if user_id == payer_id:
                amount = total - share_amount
            else:
                amount = -share_amount
            entries.append(
                LedgerEntry(expense=expense, user_id=user_id, amount=to_money(amount))
            )

        if payer_id not in share_by_user:
            entries.append(
                LedgerEntry(expense=expense, user_id=payer_id, amount=to_money(total))
            )

        LedgerEntry.objects.bulk_create(entries)

    @staticmethod
    def _assert_split_sum(expense, amount):
        """兜底断言：分摊合计必须与支出总额精确相等。"""
        total = expense.splits.aggregate(total=Sum("share_amount"))["total"] or ZERO
        if to_money(total) != to_money(amount):
            raise BusinessError(
                "分摊合计 {} 与支出总额 {} 不一致".format(
                    to_money(total), to_money(amount)
                ),
                code="split_sum_mismatch",
            )

    @staticmethod
    def _inherited_splits(existing_splits, split_type):
        """未显式传 splits 时，按原有明细构造等价入参。"""
        if split_type == Expense.SplitType.EXACT:
            return [
                {"user": row["user_id"], "share_amount": row["share_amount"]}
                for row in existing_splits
            ]
        if split_type == Expense.SplitType.PERCENTAGE:
            return [
                {"user": row["user_id"], "share_value": row["share_value"]}
                for row in existing_splits
            ]
        return None

    # ------------------------------------------------------------------
    # 权限与成员校验
    # ------------------------------------------------------------------
    @staticmethod
    def _assert_can_modify(expense, actor):
        """校验操作者是群组成员，且为支出录入人或群组管理员。"""
        membership = GroupService.require_member(expense.group, actor)
        if actor.id == expense.created_by_id or membership.is_admin:
            return
        raise ForbiddenError(
            "只有支出录入人或群组管理员可以操作该支出",
            code="expense_forbidden",
        )

    @staticmethod
    def _require_group_user(group, user_id, label, code):
        """确认 user_id 存在且属于该群组，返回 User 实例。"""
        user = User.objects.filter(pk=user_id).first()
        if user is None:
            raise BusinessError(
                "{}（id={}）不存在".format(label, user_id), code=code
            )
        if not GroupMember.objects.filter(group=group, user=user).exists():
            raise BusinessError(
                "{}（id={}）不是该群组成员".format(label, user_id), code=code
            )
        return user

    @staticmethod
    def _normalize_participants(group, participants):
        """去重并保持顺序，同时校验每个参与人都在群组内。"""
        if not participants:
            raise BusinessError("至少需要一位参与人", code="participants_required")

        ordered = []
        for user_id in participants:
            if user_id not in ordered:
                ordered.append(user_id)

        for user_id in ordered:
            ExpenseService._require_group_user(
                group, user_id, label="参与人", code="participant_not_member"
            )
        return ordered

    @staticmethod
    def _validate_amount(amount):
        """金额必须为正，并量化到两位小数。"""
        total = to_money(amount)
        if total <= ZERO:
            raise BusinessError("支出金额必须大于 0", code="invalid_amount")
        return total
