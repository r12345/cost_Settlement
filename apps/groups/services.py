"""群组与成员的领域逻辑。

所有跨对象校验、事务边界都在本模块；视图层只负责认证、取对象与返回响应。

净余额口径：群组内某用户的净余额 = 该用户所有未删除支出的 LedgerEntry 之和。
正数表示应收（别人欠他），负数表示应付（他欠别人）。
"""

from collections import defaultdict

from django.db import transaction
from django.db.models import Sum

from apps.core.exceptions import BusinessError, ForbiddenError, NotFoundError
from apps.core.utils import ZERO, to_money
from apps.expenses.models import ExpenseSplit, LedgerEntry
from apps.groups.models import Group, GroupMember
from apps.settlements.models import Settlement
from apps.users.models import User


class GroupService:
    """群组生命周期与成员管理的业务入口。"""

    # ------------------------------------------------------------------
    # 查询与权限
    # ------------------------------------------------------------------
    @staticmethod
    def require_member(group, user):
        """确认 user 是群组成员，返回其成员记录；否则抛 404。"""
        membership = GroupMember.objects.filter(group=group, user=user).first()
        if membership is None:
            raise NotFoundError("你不是该群组的成员")
        return membership

    @staticmethod
    def get_group_for_member(group_id, user):
        """按 id 取群组并校验成员身份；不存在或非成员统一抛 404。"""
        group = Group.objects.filter(pk=group_id).first()
        if group is None:
            raise NotFoundError("群组不存在，或你不是该群组成员")
        GroupService.require_member(group, user)
        return group

    @staticmethod
    def require_admin(group, user):
        """确认 user 是群组管理员，返回其成员记录；否则抛 403。"""
        membership = GroupService.require_member(group, user)
        if not membership.is_admin:
            raise ForbiddenError("只有群组管理员可以执行该操作", code="admin_required")
        return membership

    # ------------------------------------------------------------------
    # 群组
    # ------------------------------------------------------------------
    @staticmethod
    @transaction.atomic
    def create_group(user, name, description=""):
        """创建群组，并把创建者写入为 admin 成员。"""
        name = (name or "").strip()
        if not name:
            raise BusinessError("群组名称不能为空", code="group_name_required")

        group = Group.objects.create(
            name=name,
            description=(description or "").strip(),
            created_by=user,
        )
        GroupMember.objects.create(
            group=group, user=user, role=GroupMember.Role.ADMIN
        )
        return group

    @staticmethod
    @transaction.atomic
    def update_group(group, actor, name=None, description=None):
        """修改群组名称/描述，仅管理员可操作。"""
        GroupService.require_admin(group, actor)

        update_fields = []
        if name is not None:
            name = name.strip()
            if not name:
                raise BusinessError("群组名称不能为空", code="group_name_required")
            group.name = name
            update_fields.append("name")
        if description is not None:
            group.description = description.strip()
            update_fields.append("description")

        if update_fields:
            group.save(update_fields=update_fields)
        return group

    @staticmethod
    @transaction.atomic
    def delete_group(group, actor):
        """删除群组，仅管理员可操作，且所有成员净余额必须为 0。

        删除会级联清理该群组的成员关系、支出、分摊、账本与结算记录，
        因此必须先确保没有未结清的欠款，避免账目凭空消失。
        """
        GroupService.require_admin(group, actor)
        GroupService._assert_all_settled(group, action="删除群组")
        group.delete()

    # ------------------------------------------------------------------
    # 成员
    # ------------------------------------------------------------------
    @staticmethod
    @transaction.atomic
    def invite_member(group, actor, username):
        """邀请已注册用户加入群组，返回新的成员记录。"""
        GroupService.require_member(group, actor)

        username = (username or "").strip()
        user = User.objects.filter(username=username).first()
        if user is None:
            raise BusinessError("用户 {} 不存在".format(username), code="user_not_found")
        if GroupMember.objects.filter(group=group, user=user).exists():
            raise BusinessError(
                "用户 {} 已经是群组成员".format(username), code="already_member"
            )
        return GroupMember.objects.create(
            group=group, user=user, role=GroupMember.Role.MEMBER
        )

    @staticmethod
    @transaction.atomic
    def remove_member(group, actor, user_id):
        """管理员移除成员，要求该成员净余额为 0，返回被移除的用户 id。"""
        GroupService.require_admin(group, actor)

        if actor.id == user_id:
            raise BusinessError(
                "移除自己请使用退出群组接口", code="use_leave_endpoint"
            )

        membership = (
            GroupMember.objects.filter(group=group, user_id=user_id)
            .select_related("user")
            .first()
        )
        if membership is None:
            raise NotFoundError("该用户不是群组成员")

        GroupService._assert_balance_settled(
            group, membership.user, action="移除成员"
        )
        membership.delete()
        return user_id

    @staticmethod
    @transaction.atomic
    def leave_group(group, user):
        """退出群组，要求净余额为 0。

        最后一名成员退出时群组会被删除；管理员退出且仍有其他成员时，
        最早加入的成员自动接任管理员，避免群组出现无人管理的状态。
        """
        membership = GroupService.require_member(group, user)
        GroupService._assert_balance_settled(group, user, action="退出群组")
        membership.delete()

        remaining = GroupMember.objects.filter(group=group).select_related("user")
        if not remaining.exists():
            group.delete()
            return

        if remaining.filter(role=GroupMember.Role.ADMIN).exists():
            return

        successor = remaining.order_by("joined_at", "id").first()
        if successor is not None:
            successor.role = GroupMember.Role.ADMIN
            successor.save(update_fields=["role"])

    # ------------------------------------------------------------------
    # 余额
    # ------------------------------------------------------------------
    @staticmethod
    def get_user_net_balance(group, user):
        """返回某用户在该群组的净余额（Decimal，两位小数）。

        口径 = 支出账本 + 已登记结算：该用户转出的金额加回，收到的金额扣掉。
        因此「A 欠 B 100」在 A 实际转账 100 之后，双方净余额都归零。
        """
        ledger_total = LedgerEntry.objects.filter(
            expense__group=group, expense__is_deleted=False, user=user
        ).aggregate(total=Sum("amount"))["total"]
        paid_total = Settlement.objects.filter(group=group, from_user=user).aggregate(
            total=Sum("amount")
        )["total"]
        received_total = Settlement.objects.filter(
            group=group, to_user=user
        ).aggregate(total=Sum("amount"))["total"]

        return to_money(
            (ledger_total or ZERO) + (paid_total or ZERO) - (received_total or ZERO)
        )

    @staticmethod
    def get_net_balances(group):
        """返回群组内所有有账务往来的成员净余额 ``{user_id: Decimal}``。"""
        return GroupService._net_balance_map(group)

    @staticmethod
    def get_balances(group, viewer):
        """返回 viewer 视角的群组余额报告。

        包含每个成员的净余额（账本聚合）以及 viewer 与每个其他成员之间的
        两两明细（基于未删除支出的分摊记录计算），供前端渲染「谁欠谁」。
        """
        members = list(
            group.members.select_related("user").order_by("joined_at", "id")
        )
        net_balances = GroupService._net_balance_map(group)
        pairwise = GroupService._pairwise_map(group)

        balances = []
        for member in members:
            balances.append(
                {
                    "user_id": member.user_id,
                    "username": member.user.username,
                    "nickname": member.user.nickname,
                    "avatar": member.user.avatar,
                    "role": member.role,
                    "net_balance": net_balances.get(member.user_id, ZERO),
                }
            )

        relationships = []
        for member in members:
            if member.user_id == viewer.id:
                continue
            owed_to_me = pairwise.get((viewer.id, member.user_id), ZERO)
            i_owe = pairwise.get((member.user_id, viewer.id), ZERO)
            net = to_money(owed_to_me - i_owe)
            if net == ZERO:
                continue
            relationships.append(
                {
                    "user_id": member.user_id,
                    "username": member.user.username,
                    "nickname": member.user.nickname,
                    "avatar": member.user.avatar,
                    "net_balance": net,
                    "i_owe": i_owe,
                    "owes_me": owed_to_me,
                }
            )

        return {
            "group_id": group.id,
            "my_net_balance": net_balances.get(viewer.id, ZERO),
            "is_settled": all(item["net_balance"] == ZERO for item in balances),
            "balances": balances,
            "relationships": relationships,
        }

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    @staticmethod
    def _net_balance_map(group):
        """{user_id: 净余额}，含支出账本与已登记结算。

        结算不是账本分录，而是独立的聚合项：转出方余额加回，接收方扣减。
        """
        totals = defaultdict(lambda: ZERO)

        ledger_rows = (
            LedgerEntry.objects.filter(
                expense__group=group, expense__is_deleted=False
            )
            .values("user_id")
            .annotate(total=Sum("amount"))
        )
        for row in ledger_rows:
            totals[row["user_id"]] += row["total"] or ZERO

        paid_rows = (
            Settlement.objects.filter(group=group)
            .values("from_user_id")
            .annotate(total=Sum("amount"))
        )
        for row in paid_rows:
            totals[row["from_user_id"]] += row["total"] or ZERO

        received_rows = (
            Settlement.objects.filter(group=group)
            .values("to_user_id")
            .annotate(total=Sum("amount"))
        )
        for row in received_rows:
            totals[row["to_user_id"]] -= row["total"] or ZERO

        return {user_id: to_money(total) for user_id, total in totals.items()}

    @staticmethod
    def _pairwise_map(group):
        """{(债权人 id, 债务人 id): 金额}。

        由「付款人 + 每个参与人的分摊额」推导两两欠款，再做双向净额化，
        并扣除双方已经登记的实际转账：A 转给 B 的钱会直接冲减 A 对 B 的欠款。
        因此每个用户对之间最多只剩一个方向。
        """
        rows = (
            ExpenseSplit.objects.filter(
                expense__group=group, expense__is_deleted=False
            )
            .values_list("expense__payer_id", "user_id", "share_amount")
        )
        gross = defaultdict(lambda: ZERO)
        for payer_id, user_id, share_amount in rows:
            if payer_id == user_id or share_amount is None:
                continue
            gross[(payer_id, user_id)] += to_money(share_amount)

        settled = defaultdict(lambda: ZERO)
        settlement_rows = Settlement.objects.filter(group=group).values(
            "from_user_id", "to_user_id", "amount"
        )
        for row in settlement_rows:
            settled[(row["from_user_id"], row["to_user_id"])] += row["amount"]

        keys = set(gross) | set(settled)
        pairwise = {}
        for creditor_id, debtor_id in keys | {(b, a) for a, b in keys}:
            if creditor_id == debtor_id:
                continue
            amount = (
                gross.get((creditor_id, debtor_id), ZERO)
                - gross.get((debtor_id, creditor_id), ZERO)
                - settled.get((debtor_id, creditor_id), ZERO)
                + settled.get((creditor_id, debtor_id), ZERO)
            )
            if amount > ZERO:
                pairwise[(creditor_id, debtor_id)] = to_money(amount)
        return pairwise

    @staticmethod
    def _assert_balance_settled(group, user, action):
        """净余额不为 0 时阻止退出/移除。"""
        balance = GroupService.get_user_net_balance(group, user)
        if balance != ZERO:
            raise BusinessError(
                "{}失败：{} 在该群组的净余额为 {} 元（正数为应收，负数为应付），"
                "请先结清后再操作".format(action, user.display_name, balance),
                code="unsettled_balance",
            )

    @staticmethod
    def _assert_all_settled(group, action):
        """群组内所有成员净余额都为 0 才允许继续。"""
        outstanding = []
        for member in group.members.select_related("user"):
            balance = GroupService.get_user_net_balance(group, member.user)
            if balance != ZERO:
                outstanding.append("{} {}".format(member.user.display_name, balance))
        if outstanding:
            raise BusinessError(
                "{}失败：以下成员的净余额未结清（{}）".format(
                    action, "，".join(outstanding)
                ),
                code="unsettled_balance",
            )
