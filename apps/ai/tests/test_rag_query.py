"""账本语义问答（RAG 阶段 B）测试：全程 mock embedding + LLM。"""

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.core.cache import cache
from django.db.models import Sum
from rest_framework.test import APIClient

from apps.ai.models import ExpenseEmbedding
from apps.ai.services import AIService, RagQueryService
from apps.core.exceptions import BusinessError
from apps.expenses.models import Expense
from apps.expenses.services import ExpenseService
from apps.groups.services import GroupService
from apps.users.models import User

DIMENSIONS = 1536


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def members(db):
    return {
        name: User.objects.create_user(username=name, password="Str0ngPassw0rd!")
        for name in ("alice", "bob", "carol", "outsider")
    }


@pytest.fixture
def group_data(members):
    """三个人一个群、三笔不同分类的支出，并各写入一条假 embedding。"""
    alice = members["alice"]
    bob = members["bob"]
    carol = members["carol"]
    participants = [alice.id, bob.id, carol.id]

    group = GroupService.create_group(user=alice, name="RAG 测试群")
    for member in (bob, carol):
        GroupService.invite_member(group, actor=alice, username=member.username)

    def add(payer, note, category, amount):
        return ExpenseService.create_expense(
            group=group,
            actor=payer,
            payer=payer.id,
            amount=amount,
            split_type="EQUAL",
            participants=participants,
            note=note,
            category=category,
        )

    hotpot = add(alice, "海底捞火锅", "FOOD", "300.00")
    taxi = add(bob, "打车去机场", "TRANSPORT", "80.00")
    movie = add(carol, "看电影", "ENTERTAINMENT", "120.00")

    for expense in (hotpot, taxi, movie):
        ExpenseEmbedding.objects.create(
            expense=expense,
            group=group,
            content="测试向量",
            embedding=[0.1] * DIMENSIONS,
        )

    return {"group": group, "hotpot": hotpot, "taxi": taxi, "movie": movie}


@pytest.fixture
def alice_client(members):
    """以 group_data 里的 alice 身份发请求（避免与根 conftest 的 user 冲突）。"""
    client = APIClient()
    client.force_authenticate(user=members["alice"])
    return client


@pytest.fixture
def mock_ai(monkeypatch):
    """mock openai.OpenAI：chat 按顺序返回，embeddings 固定返回 1536 维。"""

    def install(chat_responses, embedding_error=None):
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            item
            if isinstance(item, Exception)
            else SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=item))]
            )
            for item in chat_responses
        ]
        if embedding_error is not None:
            client.embeddings.create.side_effect = embedding_error
        else:
            client.embeddings.create.return_value = SimpleNamespace(
                data=[SimpleNamespace(embedding=[0.1] * DIMENSIONS)]
            )
        monkeypatch.setattr("openai.OpenAI", MagicMock(return_value=client))
        return client

    return install


def _query_json(
    intent="search_expenses",
    semantic="餐饮 火锅",
    filters=None,
    target=None,
    confidence=0.9,
):
    return json.dumps(
        {
            "intent": intent,
            "semantic_query": semantic,
            "filters": filters if filters is not None else {},
            "target_username": target,
            "confidence": confidence,
        },
        ensure_ascii=False,
    )


def _enable(settings):
    settings.OPENAI_API_KEY = "sk-chat-key"
    settings.OPENAI_EMBEDDING_API_KEY = "sk-emb-key"
    settings.OPENAI_BASE_URL = ""
    settings.OPENAI_EMBEDDING_BASE_URL = ""
    return settings


# ----------------------------------------------------------------------
# A. parse_query
# ----------------------------------------------------------------------
@pytest.mark.django_db
def test_parse_query_search_intent(settings, mock_ai):
    """检索类问题被解析成 search_expenses，并带出过滤条件。"""
    _enable(settings)
    mock_ai([_query_json(filters={"category": "FOOD"})])

    parsed = AIService.parse_query("上个月吃火锅花了多少钱")

    assert parsed["intent"] == "search_expenses"
    assert parsed["semantic_query"] == "餐饮 火锅"
    assert parsed["filters"] == {"category": "FOOD"}
    assert parsed["target_username"] is None


@pytest.mark.django_db
def test_parse_query_balance_intent(settings, mock_ai):
    """余额类问题被解析成 ask_balance，并带出 target_username。"""
    _enable(settings)
    mock_ai([_query_json(intent="ask_balance", semantic="", target="bob")])

    parsed = AIService.parse_query("bob 欠我多少钱")

    assert parsed["intent"] == "ask_balance"
    assert parsed["target_username"] == "bob"


@pytest.mark.django_db
def test_parse_query_invalid_intent(settings, mock_ai):
    """intent 不在枚举内返回 None。"""
    _enable(settings)
    mock_ai([_query_json(intent="delete_expense")])

    assert AIService.parse_query("把火锅那笔删了") is None


@pytest.mark.django_db
def test_parse_query_empty_semantic(settings, mock_ai):
    """search_expenses 的 semantic_query 为空返回 None。"""
    _enable(settings)
    mock_ai([_query_json(semantic="")])

    assert AIService.parse_query("花了多少钱") is None


@pytest.mark.django_db
def test_parse_query_invalid_category(settings, mock_ai):
    """filters.category 不在枚举内返回 None。"""
    _enable(settings)
    mock_ai([_query_json(filters={"category": "SNACK"})])

    assert AIService.parse_query("零食花了多少") is None


@pytest.mark.django_db
def test_parse_query_low_confidence(settings, mock_ai):
    """confidence 低于 0.6 返回 None。"""
    _enable(settings)
    mock_ai([_query_json(confidence=0.3)])

    assert AIService.parse_query("那个啥花了多少") is None


@pytest.mark.django_db
def test_parse_query_invalid_json(settings, mock_ai):
    """非 JSON 返回 None。"""
    _enable(settings)
    mock_ai(["我不太明白你的问题"])

    assert AIService.parse_query("随便问问") is None


# ----------------------------------------------------------------------
# B. RagQueryService
# ----------------------------------------------------------------------
@pytest.mark.django_db
def test_answer_ask_balance_returns_deterministic(settings, mock_ai, group_data, members):
    """余额问题直接由 GroupService 计算，不经过 LLM。"""
    _enable(settings)
    client = mock_ai([_query_json(intent="ask_balance", semantic="")])
    group = group_data["group"]
    alice = members["alice"]

    result = RagQueryService.answer(group, alice, "我还欠谁钱")

    expected = GroupService.get_user_net_balance(group, alice)
    assert result["intent"] == "ask_balance"
    assert result["sources"] == []
    assert "{} 在群组内的净余额为 {} 元".format(alice.username, expected) == result["answer"]
    # 只调用了一次 LLM（解析意图），没有第二次生成
    assert client.chat.completions.create.call_count == 1


@pytest.mark.django_db
def test_answer_search_returns_sources(settings, mock_ai, group_data, members):
    """检索类问题返回 sources 与 LLM 答案。"""
    _enable(settings)
    mock_ai([_query_json(), "你们吃了火锅，共 300 元。"])

    result = RagQueryService.answer(group_data["group"], members["alice"], "吃火锅花了多少")

    assert result["intent"] == "search_expenses"
    assert result["answer"] == "你们吃了火锅，共 300 元。"
    assert len(result["sources"]) == 3
    first = result["sources"][0]
    assert set(first) == {
        "expense_id",
        "note",
        "amount",
        "category",
        "created_at",
        "payer_username",
    }


@pytest.mark.django_db
def test_answer_search_filters_category(settings, mock_ai, group_data, members):
    """filters.category 会下推到 ORM。"""
    _enable(settings)
    mock_ai([_query_json(filters={"category": "FOOD"}), "餐饮花了 300 元。"])

    result = RagQueryService.answer(
        group_data["group"], members["alice"], "餐饮花了多少"
    )

    assert [item["expense_id"] for item in result["sources"]] == [group_data["hotpot"].id]


@pytest.mark.django_db
def test_answer_search_respects_group_scope(settings, mock_ai, group_data, members):
    """只能检索到当前群组的支出。"""
    _enable(settings)
    alice = members["alice"]
    other_group = GroupService.create_group(user=alice, name="另一个群")
    GroupService.invite_member(other_group, actor=alice, username="bob")
    outsider_expense = ExpenseService.create_expense(
        group=other_group,
        actor=alice,
        payer=alice.id,
        amount="999.00",
        split_type="EQUAL",
        participants=[alice.id, members["bob"].id],
        note="别群的海底捞",
        category="FOOD",
    )
    ExpenseEmbedding.objects.create(
        expense=outsider_expense,
        group=other_group,
        content="别的群",
        embedding=[0.1] * DIMENSIONS,
    )
    mock_ai([_query_json(), "答案"])

    result = RagQueryService.answer(group_data["group"], alice, "吃火锅花了多少")

    ids = {item["expense_id"] for item in result["sources"]}
    assert outsider_expense.id not in ids
    assert ids == {
        group_data["hotpot"].id,
        group_data["taxi"].id,
        group_data["movie"].id,
    }


@pytest.mark.django_db
def test_answer_search_excludes_deleted(settings, mock_ai, group_data, members):
    """软删除的支出不会出现在检索结果里。"""
    _enable(settings)
    ExpenseService.soft_delete_expense(group_data["taxi"], actor=members["bob"])
    mock_ai([_query_json(), "答案"])

    result = RagQueryService.answer(
        group_data["group"], members["alice"], "打车花了多少"
    )

    ids = {item["expense_id"] for item in result["sources"]}
    assert group_data["taxi"].id not in ids
    assert group_data["hotpot"].id in ids


@pytest.mark.django_db
def test_answer_llm_failure_returns_sources(settings, mock_ai, group_data, members):
    """LLM 生成答案失败时降级，但仍然返回 sources。"""
    _enable(settings)
    mock_ai([_query_json(), RuntimeError("llm down")])

    result = RagQueryService.answer(group_data["group"], members["alice"], "吃火锅花了多少")

    assert result["answer"] == "找到 3 条相关记录，但无法生成解释，请查看下方原始记录"
    assert len(result["sources"]) == 3


@pytest.mark.django_db
def test_answer_non_member_raises(settings, mock_ai, group_data, members):
    """非群组成员调用直接 404。"""
    _enable(settings)
    mock_ai([_query_json(), "答案"])

    with pytest.raises(BusinessError) as excinfo:
        RagQueryService.answer(
            group_data["group"], members["outsider"], "吃火锅花了多少"
        )

    assert excinfo.value.code == "not_found"
    assert excinfo.value.status_code == 404


@pytest.mark.django_db
def test_answer_ai_disabled_raises(settings, group_data, members):
    """AI 未启用时抛 ai_disabled。"""
    settings.OPENAI_API_KEY = ""

    with pytest.raises(BusinessError) as excinfo:
        RagQueryService.answer(group_data["group"], members["alice"], "吃火锅花了多少")

    assert excinfo.value.code == "ai_disabled"


@pytest.mark.django_db
def test_answer_embedding_failure_raises(settings, mock_ai, group_data, members):
    """查询向量生成失败时抛 embedding_failed。"""
    _enable(settings)
    mock_ai([_query_json()], embedding_error=RuntimeError("embedding down"))

    with pytest.raises(BusinessError) as excinfo:
        RagQueryService.answer(group_data["group"], members["alice"], "吃火锅花了多少")

    assert excinfo.value.code == "embedding_failed"


@pytest.mark.django_db
def test_answer_sum_calculated_by_orm_not_llm(settings, mock_ai, group_data, members):
    """涉及「总共」时，汇总金额由 ORM 计算并写进 prompt。"""
    _enable(settings)
    client = mock_ai([_query_json(filters={"category": "FOOD"}), "共 300.00 元。"])

    RagQueryService.answer(group_data["group"], members["alice"], "这周餐饮总共多少")

    expected = Expense.objects.filter(
        group=group_data["group"], is_deleted=False, category="FOOD"
    ).aggregate(total=Sum("amount"))["total"]
    prompt = client.chat.completions.create.call_args_list[1].kwargs["messages"][1]["content"]
    assert "数据库精确汇总" in prompt
    assert str(expected) in prompt
    assert Decimal("300.00") == expected


# ----------------------------------------------------------------------
# C. 端点
# ----------------------------------------------------------------------
@pytest.mark.django_db
def test_query_endpoint_success(settings, mock_ai, group_data, alice_client):
    """POST /query/ 返回 answer / intent / sources。"""
    _enable(settings)
    mock_ai([_query_json(), "你们吃了火锅，共 300 元。"])

    response = alice_client.post(
        "/api/groups/{}/query/".format(group_data["group"].id),
        {"question": "吃火锅花了多少"},
        format="json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "search_expenses"
    assert body["answer"] == "你们吃了火锅，共 300 元。"
    assert len(body["sources"]) == 3


@pytest.mark.django_db
def test_query_endpoint_ai_disabled_returns_422(settings, group_data, alice_client):
    """AI 未启用时返回 422 + manual 兜底。"""
    settings.OPENAI_API_KEY = ""

    response = alice_client.post(
        "/api/groups/{}/query/".format(group_data["group"].id),
        {"question": "吃火锅花了多少"},
        format="json",
    )

    assert response.status_code == 422
    body = response.json()
    assert body["fallback"] == "manual"
    assert body["reason"] == "AI 功能未启用"


@pytest.mark.django_db
def test_query_endpoint_rate_limit(settings, group_data, alice_client):
    """复用 ai_parse 限流：第 11 次返回 429。"""
    settings.OPENAI_API_KEY = ""
    url = "/api/groups/{}/query/".format(group_data["group"].id)

    codes = [
        alice_client.post(url, {"question": "花了多少"}, format="json").status_code
        for _ in range(10)
    ]
    assert codes == [422] * 10
    assert alice_client.post(url, {"question": "花了多少"}, format="json").status_code == 429


@pytest.mark.django_db
def test_query_endpoint_non_member_returns_404(settings, group_data, members):
    """非群组成员调用返回 404。"""
    _enable(settings)
    client = APIClient()
    client.force_authenticate(user=members["outsider"])

    response = client.post(
        "/api/groups/{}/query/".format(group_data["group"].id),
        {"question": "吃火锅花了多少"},
        format="json",
    )

    assert response.status_code == 404
