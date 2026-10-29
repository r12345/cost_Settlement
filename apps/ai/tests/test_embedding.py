"""支出 embedding（RAG 阶段 A）测试：全程 mock embedding API。"""

from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.ai.models import ExpenseEmbedding
from apps.ai.services import ExpenseEmbeddingService
from apps.expenses.services import ExpenseService
from apps.groups.services import GroupService

DIMENSIONS = 1536


@pytest.fixture
def ai_settings(settings):
    settings.OPENAI_API_KEY = "sk-test-key"
    settings.OPENAI_BASE_URL = ""
    settings.OPENAI_EMBEDDING_MODEL = "test-embedding-model"
    settings.AI_ENABLED = True
    return settings


@pytest.fixture
def mock_embeddings(monkeypatch):
    """把 openai.OpenAI 换成 mock；可指定向量或抛异常。"""

    def install(vector=None, error=None):
        client = MagicMock()
        if error is not None:
            client.embeddings.create.side_effect = error
        else:
            payload = vector if vector is not None else [0.1] * DIMENSIONS
            client.embeddings.create.return_value = SimpleNamespace(
                data=[SimpleNamespace(embedding=payload)]
            )
        monkeypatch.setattr("openai.OpenAI", MagicMock(return_value=client))
        return client

    return install


def _create_expense(group, payer, participants, amount="300.00", note="火锅", category="FOOD"):
    return ExpenseService.create_expense(
        group=group,
        actor=payer,
        payer=payer.id,
        amount=amount,
        split_type="EQUAL",
        participants=[member.id for member in participants],
        note=note,
        category=category,
    )


@pytest.mark.django_db
def test_upsert_creates_embedding(ai_settings, mock_embeddings, group_with_members, user, other_user):
    """创建支出后自动生成 embedding，content 格式符合约定。"""
    mock_embeddings()

    expense = _create_expense(group_with_members, user, [user, other_user], note="火锅")

    record = ExpenseEmbedding.objects.get(expense=expense)
    assert record.group_id == group_with_members.id
    assert record.embedding is not None
    assert record.content == "支出: 火锅 | 分类: FOOD | 付款人: alice | 参与人: alice, bob"


@pytest.mark.django_db
def test_upsert_updates_embedding(ai_settings, mock_embeddings, group_with_members, user, other_user):
    """编辑支出后备注变化 → content 更新、embedding 重新生成。"""
    mock_embeddings()
    expense = _create_expense(group_with_members, user, [user, other_user], note="火锅")
    before = ExpenseEmbedding.objects.get(expense=expense)

    ExpenseService.update_expense(expense, actor=user, note="烧烤")

    after = ExpenseEmbedding.objects.get(expense=expense)
    assert "火锅" in before.content
    assert "烧烤" in after.content
    assert after.content != before.content
    assert after.updated_at >= before.updated_at


@pytest.mark.django_db
def test_upsert_skips_when_ai_disabled(settings, auth_client, group_with_members, user, other_user):
    """未配置 Key 时创建支出仍然成功，但不产生 embedding。"""
    settings.OPENAI_API_KEY = ""

    response = auth_client.post(
        "/api/groups/{}/expenses/".format(group_with_members.id),
        {
            "payer": user.id,
            "amount": "300.00",
            "split_type": "EQUAL",
            "participants": [user.id, other_user.id],
        },
        format="json",
    )

    assert response.status_code == 201
    assert ExpenseEmbedding.objects.count() == 0


@pytest.mark.django_db
def test_upsert_swallows_api_error(ai_settings, mock_embeddings, group_with_members, user, other_user):
    """embedding API 抛异常时，支出创建不受影响。"""
    mock_embeddings(error=RuntimeError("embedding service down"))

    expense = _create_expense(group_with_members, user, [user, other_user])

    assert expense.pk is not None
    assert not ExpenseEmbedding.objects.filter(expense=expense).exists()


@pytest.mark.django_db
def test_upsert_dimension_mismatch_skips(ai_settings, mock_embeddings, group_with_members, user, other_user):
    """维度不是 1536 时不写入。"""
    mock_embeddings(vector=[0.1] * 3072)

    expense = _create_expense(group_with_members, user, [user, other_user])

    assert not ExpenseEmbedding.objects.filter(expense=expense).exists()


@pytest.mark.django_db
def test_build_content_stable(ai_settings, mock_embeddings, group_with_members, user, other_user):
    """同一笔支出多次构建 content 结果完全一致（参与人按 username 排序）。"""
    mock_embeddings()
    expense = _create_expense(group_with_members, user, [user, other_user], note="火锅")

    first = ExpenseEmbeddingService.build_content(expense)
    second = ExpenseEmbeddingService.build_content(expense)

    assert first == second
    assert first.endswith("参与人: alice, bob")


@pytest.mark.django_db
def test_build_content_empty_note(settings, group_with_members, user, other_user):
    """note 为空时使用「未填写备注」。"""
    settings.OPENAI_API_KEY = ""
    expense = _create_expense(group_with_members, user, [user, other_user], note="")

    content = ExpenseEmbeddingService.build_content(expense)

    assert "未填写备注" in content


# ----------------------------------------------------------------------
# backfill 命令
# ----------------------------------------------------------------------
@pytest.mark.django_db
def test_backfill_creates_missing(settings, mock_embeddings, group_with_members, user, other_user):
    """没有 embedding 的历史支出会被补上。"""
    settings.OPENAI_API_KEY = ""
    expense = _create_expense(group_with_members, user, [user, other_user])
    assert not ExpenseEmbedding.objects.filter(expense=expense).exists()

    settings.OPENAI_API_KEY = "sk-test-key"
    mock_embeddings()
    out = StringIO()

    call_command("backfill_embeddings", stdout=out)

    assert ExpenseEmbedding.objects.filter(expense=expense).exists()
    assert "成功 1" in out.getvalue()


@pytest.mark.django_db
def test_backfill_skips_existing(ai_settings, mock_embeddings, group_with_members, user, other_user):
    """已是最新的 embedding 会被跳过。"""
    mock_embeddings()
    expense = _create_expense(group_with_members, user, [user, other_user])
    assert ExpenseEmbedding.objects.filter(expense=expense).exists()

    out = StringIO()
    call_command("backfill_embeddings", stdout=out)

    assert "跳过 1" in out.getvalue()


@pytest.mark.django_db
def test_backfill_dry_run_does_not_write(settings, mock_embeddings, group_with_members, user, other_user):
    """--dry-run 只统计，不写库。"""
    settings.OPENAI_API_KEY = ""
    _create_expense(group_with_members, user, [user, other_user])

    settings.OPENAI_API_KEY = "sk-test-key"
    mock_embeddings()
    out = StringIO()

    call_command("backfill_embeddings", "--dry-run", stdout=out)

    assert ExpenseEmbedding.objects.count() == 0
    assert "待生成 1" in out.getvalue()


@pytest.mark.django_db
def test_backfill_fails_without_api_key(settings, group_with_members, user, other_user):
    """未配置 Key 时命令直接失败退出。"""
    settings.OPENAI_API_KEY = ""

    with pytest.raises(CommandError) as excinfo:
        call_command("backfill_embeddings")

    assert "OPENAI_API_KEY" in str(excinfo.value)


@pytest.mark.django_db
def test_backfill_group_filter(settings, mock_embeddings, group_with_members, user, other_user):
    """--group-id 只回填指定群组。"""
    settings.OPENAI_API_KEY = ""
    in_group = _create_expense(group_with_members, user, [user, other_user])

    other_group = GroupService.create_group(user=user, name="另一个群")
    GroupService.invite_member(other_group, actor=user, username=other_user.username)
    out_of_group = _create_expense(other_group, user, [user, other_user])

    settings.OPENAI_API_KEY = "sk-test-key"
    mock_embeddings()

    call_command("backfill_embeddings", "--group-id", str(group_with_members.id), stdout=StringIO())

    assert ExpenseEmbedding.objects.filter(expense=in_group).exists()
    assert not ExpenseEmbedding.objects.filter(expense=out_of_group).exists()


# ----------------------------------------------------------------------
# embedding 独立配置（chat 用 DeepSeek、embedding 用通义）
# ----------------------------------------------------------------------
def _install_client(monkeypatch, vector=None):
    """把 openai.OpenAI 换成记录构造参数的 mock，返回 (constructor, client)。"""
    constructor = MagicMock()
    client = MagicMock()
    client.embeddings.create.return_value = SimpleNamespace(
        data=[
            SimpleNamespace(
                embedding=vector if vector is not None else [0.1] * DIMENSIONS
            )
        ]
    )
    constructor.return_value = client
    monkeypatch.setattr("openai.OpenAI", constructor)
    return constructor, client


@pytest.mark.django_db
def test_embedding_uses_independent_config(
    settings, monkeypatch, group_with_members, user, other_user
):
    """embedding 使用独立配置，而不是 chat 的 key / base_url。"""
    settings.OPENAI_API_KEY = "sk-chat-key"
    settings.OPENAI_BASE_URL = "https://chat.example/v1"
    settings.OPENAI_EMBEDDING_API_KEY = "sk-emb"
    settings.OPENAI_EMBEDDING_BASE_URL = "https://dashscope.example/v1"
    settings.OPENAI_EMBEDDING_MODEL = "text-embedding-v4"
    constructor, _client = _install_client(monkeypatch)

    _create_expense(group_with_members, user, [user, other_user])

    kwargs = constructor.call_args.kwargs
    assert kwargs["api_key"] == "sk-emb"
    assert kwargs["base_url"] == "https://dashscope.example/v1"


@pytest.mark.django_db
def test_embedding_falls_back_to_chat_config(
    settings, monkeypatch, group_with_members, user, other_user
):
    """未配置独立 key 时回落到 chat 的 key / base_url。"""
    settings.OPENAI_API_KEY = "sk-chat-key"
    settings.OPENAI_BASE_URL = "https://chat.example/v1"
    settings.OPENAI_EMBEDDING_API_KEY = ""
    settings.OPENAI_EMBEDDING_BASE_URL = ""
    constructor, _client = _install_client(monkeypatch)

    _create_expense(group_with_members, user, [user, other_user])

    kwargs = constructor.call_args.kwargs
    assert kwargs["api_key"] == "sk-chat-key"
    assert kwargs["base_url"] == "https://chat.example/v1"


@pytest.mark.django_db
def test_embedding_passes_dimensions_1536(
    settings, monkeypatch, group_with_members, user, other_user
):
    """调用 embeddings.create 时显式要求 1536 维。"""
    settings.OPENAI_API_KEY = "sk-chat-key"
    settings.OPENAI_EMBEDDING_API_KEY = "sk-emb"
    _constructor, client = _install_client(monkeypatch)

    _create_expense(group_with_members, user, [user, other_user])

    kwargs = client.embeddings.create.call_args.kwargs
    assert kwargs["dimensions"] == 1536
    assert "extra_body" not in kwargs


@pytest.mark.django_db
def test_embedding_falls_back_to_extra_body(
    settings, monkeypatch, group_with_members, user, other_user
):
    """服务端不认顶层 dimensions 时自动降级到 extra_body。"""
    settings.OPENAI_API_KEY = "sk-chat-key"
    settings.OPENAI_EMBEDDING_API_KEY = "sk-emb"
    _constructor, client = _install_client(monkeypatch)
    client.embeddings.create.side_effect = [
        TypeError("unexpected keyword argument 'dimensions'"),
        SimpleNamespace(data=[SimpleNamespace(embedding=[0.1] * DIMENSIONS)]),
    ]

    expense = _create_expense(group_with_members, user, [user, other_user])

    assert ExpenseEmbedding.objects.filter(expense=expense).exists()
    second_call = client.embeddings.create.call_args_list[-1].kwargs
    assert second_call["extra_body"] == {"dimensions": 1536}
