"""AI 解析与草稿落库接口的集成测试（@pytest.mark.django_db）。

全程 mock ``openai.OpenAI``：不配置真实 Key、不发起任何外部请求。
"""

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.ai.services import AIService
from apps.expenses.models import Expense
from apps.groups.services import GroupService
from apps.users.models import User


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    """限流计数保存在 locmem 缓存里，用例之间必须清空。"""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def users(db):
    return {
        name: User.objects.create_user(username=name, password="Str0ngPassw0rd!")
        for name in ("alice", "bob", "carol", "dave")
    }


@pytest.fixture
def group(users):
    group = GroupService.create_group(user=users["alice"], name="AI 测试群")
    GroupService.invite_member(group, actor=users["alice"], username="bob")
    GroupService.invite_member(group, actor=users["alice"], username="carol")
    return group


@pytest.fixture
def alice_client(users):
    client = APIClient()
    client.force_authenticate(user=users["alice"])
    return client


@pytest.fixture
def ai_enabled(settings):
    settings.OPENAI_API_KEY = "sk-test-key"
    settings.AI_ENABLED = True
    return settings


@pytest.fixture
def mock_llm(monkeypatch):
    """替换 openai.OpenAI，让底层 create 返回指定文本或抛出指定异常。"""

    def install(result):
        client = MagicMock()
        if isinstance(result, Exception):
            client.chat.completions.create.side_effect = result
        else:
            client.chat.completions.create.return_value = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=result))]
            )
        monkeypatch.setattr("openai.OpenAI", MagicMock(return_value=client))
        return client

    return install


def _parse_url(group):
    return "/api/groups/{}/expenses/parse/".format(group.id)


def _draft_url(group):
    return "/api/groups/{}/expenses/from-draft/".format(group.id)


def _draft_payload(payer_id, participants, **overrides):
    payload = {
        "payer_id": payer_id,
        "amount": "300.00",
        "participants": participants,
        "split_type": "EQUAL",
        "splits": None,
        "note": "火锅",
        "category": "FOOD",
        "confidence": 0.85,
    }
    payload.update(overrides)
    return payload


def _post_parse(client, group, text="昨天我和张三李四吃火锅，张三垫了300三人AA"):
    return client.post(_parse_url(group), {"text": text}, format="json")


# ----------------------------------------------------------------------
# A. 未启用时的降级
# ----------------------------------------------------------------------
@pytest.mark.django_db
def test_parse_returns_422_when_ai_disabled(settings, alice_client, group):
    """A1/A2：没有 Key 时 is_enabled 为 False，parse 返回 422 + 人工兜底。"""
    settings.OPENAI_API_KEY = ""

    assert AIService.is_enabled() is False

    response = _post_parse(alice_client, group)

    assert response.status_code == 422
    body = response.json()
    assert body["fallback"] == "manual"
    assert "未启用" in body["reason"]


@pytest.mark.django_db
def test_categorize_disabled_does_not_call_llm(settings, monkeypatch):
    """A3：未启用时分类直接回落 OTHER，且不会构造 openai 客户端。"""
    settings.OPENAI_API_KEY = ""
    spy = MagicMock()
    monkeypatch.setattr("openai.OpenAI", spy)

    assert AIService.categorize_expense("火锅") == "OTHER"
    spy.assert_not_called()


# ----------------------------------------------------------------------
# B. 成功路径
# ----------------------------------------------------------------------
@pytest.mark.django_db
def test_parse_equal_draft(ai_enabled, mock_llm, alice_client, group, users):
    """B1：合法 EQUAL 草稿。"""
    ids = [users["alice"].id, users["bob"].id, users["carol"].id]
    mock_llm(json.dumps(_draft_payload(ids[0], ids)))

    response = _post_parse(alice_client, group)

    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["payer_id"] == ids[0]
    assert body["draft"]["amount"] == "300.00"
    assert body["draft"]["participants"] == ids
    assert body["draft"]["split_type"] == "EQUAL"
    assert body["draft"]["splits"] is None
    assert body["draft"]["category"] == "FOOD"
    assert body["confidence"] == pytest.approx(0.85)


@pytest.mark.django_db
def test_parse_exact_draft(ai_enabled, mock_llm, alice_client, group, users):
    """B2：合法 EXACT 草稿。"""
    ids = [users["alice"].id, users["bob"].id, users["carol"].id]
    payload = _draft_payload(
        ids[0],
        ids,
        split_type="EXACT",
        splits=[
            {"user_id": ids[0], "share_amount": "100.00"},
            {"user_id": ids[1], "share_amount": "100.00"},
            {"user_id": ids[2], "share_amount": "100.00"},
        ],
    )
    mock_llm(json.dumps(payload))

    response = _post_parse(alice_client, group)

    assert response.status_code == 200
    splits = response.json()["draft"]["splits"]
    assert [item["share_amount"] for item in splits] == ["100.00", "100.00", "100.00"]


@pytest.mark.django_db
def test_parse_percentage_draft(ai_enabled, mock_llm, alice_client, group, users):
    """B3：合法 PERCENTAGE 草稿。"""
    ids = [users["alice"].id, users["bob"].id, users["carol"].id]
    payload = _draft_payload(
        ids[0],
        ids,
        split_type="PERCENTAGE",
        splits=[
            {"user_id": ids[0], "share_value": "50.00"},
            {"user_id": ids[1], "share_value": "30.00"},
            {"user_id": ids[2], "share_value": "20.00"},
        ],
    )
    mock_llm(json.dumps(payload))

    response = _post_parse(alice_client, group)

    assert response.status_code == 200
    splits = response.json()["draft"]["splits"]
    assert [item["share_value"] for item in splits] == ["50.00", "30.00", "20.00"]


# ----------------------------------------------------------------------
# C. schema 校验
# ----------------------------------------------------------------------
@pytest.mark.django_db
def test_parse_invalid_json_returns_422(
    ai_enabled, mock_llm, alice_client, group
):
    """C1：非法 JSON。"""
    mock_llm("我觉得是三个人 AA")

    response = _post_parse(alice_client, group)

    assert response.status_code == 422
    assert "无法从文本中解析" in response.json()["reason"]


@pytest.mark.django_db
def test_parse_unknown_payer_returns_422(
    ai_enabled, mock_llm, alice_client, group, users
):
    """C2：payer_id 不在成员列表。"""
    ids = [users["alice"].id, users["bob"].id]
    mock_llm(json.dumps(_draft_payload(999999, ids)))

    assert _post_parse(alice_client, group).status_code == 422


@pytest.mark.django_db
def test_parse_unknown_participant_returns_422(
    ai_enabled, mock_llm, alice_client, group, users
):
    """C3：participants 含非成员。"""
    ids = [users["alice"].id, users["bob"].id, 999999]
    mock_llm(json.dumps(_draft_payload(ids[0], ids)))

    assert _post_parse(alice_client, group).status_code == 422


@pytest.mark.django_db
@pytest.mark.parametrize("amount", ["-100.00", "abc", "0"])
def test_parse_invalid_amount_returns_422(
    ai_enabled, mock_llm, alice_client, group, users, amount
):
    """C4：金额为负 / 非数字 / 0。"""
    ids = [users["alice"].id, users["bob"].id]
    mock_llm(json.dumps(_draft_payload(ids[0], ids, amount=amount)))

    assert _post_parse(alice_client, group).status_code == 422


@pytest.mark.django_db
def test_parse_low_confidence_returns_422(
    ai_enabled, mock_llm, alice_client, group, users
):
    """C5：confidence 低于 0.6。"""
    ids = [users["alice"].id, users["bob"].id]
    mock_llm(json.dumps(_draft_payload(ids[0], ids, confidence=0.5)))

    assert _post_parse(alice_client, group).status_code == 422


@pytest.mark.django_db
def test_parse_invalid_split_type_returns_422(
    ai_enabled, mock_llm, alice_client, group, users
):
    """C6：split_type 不在枚举。"""
    ids = [users["alice"].id, users["bob"].id]
    mock_llm(json.dumps(_draft_payload(ids[0], ids, split_type="FIXED")))

    assert _post_parse(alice_client, group).status_code == 422


@pytest.mark.django_db
def test_parse_exact_sum_mismatch_returns_422(
    ai_enabled, mock_llm, alice_client, group, users
):
    """C7：EXACT 份额合计不等于总额。"""
    ids = [users["alice"].id, users["bob"].id]
    payload = _draft_payload(
        ids[0],
        ids,
        split_type="EXACT",
        splits=[
            {"user_id": ids[0], "share_amount": "100.00"},
            {"user_id": ids[1], "share_amount": "199.99"},
        ],
    )
    mock_llm(json.dumps(payload))

    assert _post_parse(alice_client, group).status_code == 422


@pytest.mark.django_db
def test_parse_percentage_sum_mismatch_returns_422(
    ai_enabled, mock_llm, alice_client, group, users
):
    """C8：PERCENTAGE 百分比合计不等于 100。"""
    ids = [users["alice"].id, users["bob"].id]
    payload = _draft_payload(
        ids[0],
        ids,
        split_type="PERCENTAGE",
        splits=[
            {"user_id": ids[0], "share_value": "60.00"},
            {"user_id": ids[1], "share_value": "39.99"},
        ],
    )
    mock_llm(json.dumps(payload))

    assert _post_parse(alice_client, group).status_code == 422


@pytest.mark.django_db
def test_parse_timeout_returns_reason(
    ai_enabled, mock_llm, alice_client, group
):
    """C9：超时 → 422 且 reason 提示超时。"""
    mock_llm(TimeoutError("request timed out"))

    response = _post_parse(alice_client, group)

    assert response.status_code == 422
    assert "超时" in response.json()["reason"]


@pytest.mark.django_db
def test_parse_exception_hides_details(
    ai_enabled, mock_llm, alice_client, group
):
    """C10：任意异常 → 422，且 reason 不泄露异常文本。"""
    mock_llm(RuntimeError("secret internal stack"))

    response = _post_parse(alice_client, group)

    assert response.status_code == 422
    reason = response.json()["reason"]
    assert "secret" not in reason
    assert "Traceback" not in reason


# ----------------------------------------------------------------------
# E. from-draft 安全边界
# ----------------------------------------------------------------------
@pytest.mark.django_db
def test_from_draft_creates_expense_via_service(
    ai_enabled, mock_llm, alice_client, group, users
):
    """E1：草稿落库走 ExpenseService，账本与手动创建一致。"""
    ids = [users["alice"].id, users["bob"].id, users["carol"].id]
    mock_llm(json.dumps(_draft_payload(ids[0], ids)))
    draft = _post_parse(alice_client, group).json()["draft"]

    response = alice_client.post(_draft_url(group), draft, format="json")

    assert response.status_code == 201
    body = response.json()
    assert body["amount"] == "300.00"
    assert len(body["splits"]) == 3

    expense = Expense.objects.get(pk=body["id"])
    ledger = {row.user_id: row.amount for row in expense.ledger_entries.all()}
    assert ledger[ids[0]] == Decimal("200.00")
    assert ledger[ids[1]] == Decimal("-100.00")


@pytest.mark.django_db
def test_from_draft_tampered_amount_still_validated(
    ai_enabled, mock_llm, alice_client, group, users
):
    """E2：篡改金额仍能创建（用户有权限），但账本按新金额完整重算。"""
    ids = [users["alice"].id, users["bob"].id, users["carol"].id]
    mock_llm(json.dumps(_draft_payload(ids[0], ids)))
    draft = _post_parse(alice_client, group).json()["draft"]
    draft["amount"] = "9999.00"

    response = alice_client.post(_draft_url(group), draft, format="json")

    assert response.status_code == 201
    expense = Expense.objects.get(pk=response.json()["id"])
    assert expense.amount == Decimal("9999.00")

    shares = sorted(split.share_amount for split in expense.splits.all())
    assert shares == [Decimal("3333.00"), Decimal("3333.00"), Decimal("3333.00")]
    ledger = {row.user_id: row.amount for row in expense.ledger_entries.all()}
    assert ledger[ids[0]] == Decimal("6666.00")


@pytest.mark.django_db
def test_from_draft_rejects_non_member_participant(
    ai_enabled, mock_llm, alice_client, group, users
):
    """E3：把非群组成员塞进 participants，被 ExpenseService 拦下。"""
    ids = [users["alice"].id, users["bob"].id, users["dave"].id]
    mock_llm(json.dumps(_draft_payload(ids[0], [ids[0], ids[1]])))
    draft = _post_parse(alice_client, group).json()["draft"]
    draft["participants"] = ids

    response = alice_client.post(_draft_url(group), draft, format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "participant_not_member"


@pytest.mark.django_db
def test_from_draft_exact_without_splits_rejected(
    ai_enabled, mock_llm, alice_client, group, users
):
    """E4：改成 EXACT 却不给 splits，被 Service 拦下。"""
    ids = [users["alice"].id, users["bob"].id, users["carol"].id]
    mock_llm(json.dumps(_draft_payload(ids[0], ids)))
    draft = _post_parse(alice_client, group).json()["draft"]
    draft["split_type"] = "EXACT"
    draft["splits"] = None

    response = alice_client.post(_draft_url(group), draft, format="json")

    assert response.status_code == 400
    assert response.json()["code"] in {
        "splits_required",
        "split_sum_mismatch",
        "participant_mismatch",
    }


@pytest.mark.django_db
def test_from_draft_ignores_extra_fields(
    ai_enabled, mock_llm, alice_client, group, users
):
    """E5/E6：confidence 被忽略，body 里的 group_id 不能覆盖 URL 里的群组。"""
    ids = [users["alice"].id, users["bob"].id, users["carol"].id]
    mock_llm(json.dumps(_draft_payload(ids[0], ids)))
    draft = _post_parse(alice_client, group).json()["draft"]

    other_group = GroupService.create_group(user=users["alice"], name="另一个群")
    draft["group_id"] = other_group.id
    draft["confidence"] = 0.99

    response = alice_client.post(_draft_url(group), draft, format="json")

    assert response.status_code == 201
    body = response.json()
    assert "confidence" not in body
    assert body["group"] == group.id

    expense = Expense.objects.get(pk=body["id"])
    assert expense.group_id == group.id


# ----------------------------------------------------------------------
# F. 限流
# ----------------------------------------------------------------------
@pytest.mark.django_db
def test_parse_is_throttled_after_ten_calls(settings, alice_client, group):
    """F1：同一用户第 11 次调用返回 429。"""
    settings.OPENAI_API_KEY = ""

    first_ten = [_post_parse(alice_client, group).status_code for _ in range(10)]
    assert first_ten == [422] * 10

    assert _post_parse(alice_client, group).status_code == 429


@pytest.mark.django_db
def test_parse_throttle_is_per_user(settings, users, group):
    """F2：限流按用户维度独立计数。"""
    settings.OPENAI_API_KEY = ""

    for name in ("alice", "bob"):
        client = APIClient()
        client.force_authenticate(user=users[name])
        codes = [_post_parse(client, group).status_code for _ in range(10)]
        assert codes == [422] * 10
