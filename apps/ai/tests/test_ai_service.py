"""AIService 单元测试。

全程 mock ``openai.OpenAI``，绝不发起真实网络请求；不依赖环境里是否存在 API Key，
因此在无 Key 的 CI 环境同样可以全量运行。
"""

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from apps.ai.services import AIService

MEMBERS = {"张三": 1, "李四": 2, "王五": 3}


def _completion(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def _payload(**overrides):
    payload = {
        "payer_id": 1,
        "amount": "300.00",
        "participants": [1, 2, 3],
        "split_type": "EQUAL",
        "splits": None,
        "note": "火锅",
        "category": "FOOD",
        "confidence": 0.85,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def ai_enabled(settings):
    settings.OPENAI_API_KEY = "sk-test-key"
    settings.OPENAI_BASE_URL = ""
    settings.OPENAI_MODEL = "test-model"
    settings.OPENAI_TIMEOUT = 10
    settings.AI_ENABLED = True
    return settings


@pytest.fixture
def mock_llm(monkeypatch):
    """把 openai.OpenAI 替换成 mock，让 client.chat.completions.create 返回或抛出指定值。"""

    def install(result):
        client = MagicMock()
        if isinstance(result, Exception):
            client.chat.completions.create.side_effect = result
        else:
            client.chat.completions.create.return_value = _completion(result)
        monkeypatch.setattr("openai.OpenAI", MagicMock(return_value=client))
        return client

    return install


def test_is_enabled_false_without_key(settings):
    """未配置 API Key 时不可用。"""
    settings.OPENAI_API_KEY = ""
    settings.AI_ENABLED = True

    assert AIService.is_enabled() is False


def test_is_enabled_true_with_key(ai_enabled):
    """配置了 Key 且总开关打开时可用。"""
    assert AIService.is_enabled() is True


def test_is_enabled_respects_master_switch(ai_enabled):
    """总开关关闭时，即使有 Key 也不可用。"""
    ai_enabled.AI_ENABLED = False

    assert AIService.is_enabled() is False


def test_parse_expense_success_equal(ai_enabled, mock_llm):
    """合法 EQUAL 草稿被完整解析。"""
    client = mock_llm(json.dumps(_payload()))

    draft = AIService.parse_expense("张三垫了300三人AA", MEMBERS)

    assert draft is not None
    assert draft["payer_id"] == 1
    assert draft["amount"] == "300.00"
    assert draft["participants"] == [1, 2, 3]
    assert draft["split_type"] == "EQUAL"
    assert draft["splits"] is None
    assert draft["category"] == "FOOD"
    assert draft["confidence"] == pytest.approx(0.85)

    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["model"] == "test-model"


def test_parse_expense_success_exact(ai_enabled, mock_llm):
    """合法 EXACT 草稿（份额合计等于总额）。"""
    mock_llm(
        json.dumps(
            _payload(
                split_type="EXACT",
                splits=[
                    {"user_id": 1, "share_amount": "100.00"},
                    {"user_id": 2, "share_amount": "100.00"},
                    {"user_id": 3, "share_amount": "100.00"},
                ],
            )
        )
    )

    draft = AIService.parse_expense("三人各出100", MEMBERS)

    assert draft is not None
    assert draft["split_type"] == "EXACT"
    assert [item["share_amount"] for item in draft["splits"]] == [
        "100.00",
        "100.00",
        "100.00",
    ]


def test_parse_expense_success_percentage(ai_enabled, mock_llm):
    """合法 PERCENTAGE 草稿（百分比合计 100）。"""
    mock_llm(
        json.dumps(
            _payload(
                split_type="PERCENTAGE",
                splits=[
                    {"user_id": 1, "share_value": "50.00"},
                    {"user_id": 2, "share_value": "30.00"},
                    {"user_id": 3, "share_value": "20.00"},
                ],
            )
        )
    )

    draft = AIService.parse_expense("张三一半，李四三成，王五两成", MEMBERS)

    assert draft is not None
    assert draft["split_type"] == "PERCENTAGE"
    assert [item["share_value"] for item in draft["splits"]] == [
        "50.00",
        "30.00",
        "20.00",
    ]


def test_parse_expense_invalid_json(ai_enabled, mock_llm):
    """LLM 返回非法 JSON 时返回 None。"""
    mock_llm("这不是 JSON")

    assert AIService.parse_expense("随便说说", MEMBERS) is None
    assert AIService.get_last_failure() == "invalid"


def test_parse_expense_unknown_payer(ai_enabled, mock_llm):
    """payer_id 不在群组成员里时返回 None。"""
    mock_llm(json.dumps(_payload(payer_id=999)))

    assert AIService.parse_expense("张三垫了300", MEMBERS) is None


def test_parse_expense_unknown_participant(ai_enabled, mock_llm):
    """participants 含非成员时返回 None。"""
    mock_llm(json.dumps(_payload(participants=[1, 2, 999])))

    assert AIService.parse_expense("三人AA", MEMBERS) is None


@pytest.mark.parametrize("amount", ["-100.00", "0", "0.00", "abc", None, 100.005])
def test_parse_expense_invalid_amount(ai_enabled, mock_llm, amount):
    """金额非法（负数 / 0 / 非数字 / 超过两位小数）时返回 None。"""
    mock_llm(json.dumps(_payload(amount=amount)))

    assert AIService.parse_expense("张三垫了300", MEMBERS) is None


def test_parse_expense_low_confidence(ai_enabled, mock_llm):
    """置信度低于 0.6 视为不可信。"""
    mock_llm(json.dumps(_payload(confidence=0.5)))

    assert AIService.parse_expense("他们去吃饭了", MEMBERS) is None


def test_parse_expense_invalid_split_type(ai_enabled, mock_llm):
    """split_type 不在枚举内时返回 None。"""
    mock_llm(json.dumps(_payload(split_type="FIXED")))

    assert AIService.parse_expense("张三垫了300", MEMBERS) is None


def test_parse_expense_exact_sum_mismatch(ai_enabled, mock_llm):
    """EXACT 的份额合计不等于总额时返回 None。"""
    mock_llm(
        json.dumps(
            _payload(
                split_type="EXACT",
                splits=[
                    {"user_id": 1, "share_amount": "100.00"},
                    {"user_id": 2, "share_amount": "100.00"},
                    {"user_id": 3, "share_amount": "99.99"},
                ],
            )
        )
    )

    assert AIService.parse_expense("三人各出100", MEMBERS) is None


def test_parse_expense_percentage_sum_mismatch(ai_enabled, mock_llm):
    """PERCENTAGE 的百分比合计不等于 100 时返回 None。"""
    mock_llm(
        json.dumps(
            _payload(
                split_type="PERCENTAGE",
                splits=[
                    {"user_id": 1, "share_value": "50.00"},
                    {"user_id": 2, "share_value": "30.00"},
                    {"user_id": 3, "share_value": "19.99"},
                ],
            )
        )
    )

    assert AIService.parse_expense("按比例分", MEMBERS) is None


def test_parse_expense_timeout_returns_none(ai_enabled, mock_llm):
    """LLM 超时返回 None，并记录 timeout 失败类型。"""
    mock_llm(TimeoutError("timed out"))

    assert AIService.parse_expense("张三垫了300", MEMBERS) is None
    assert AIService.get_last_failure() == "timeout"


def test_parse_expense_any_exception_returns_none(ai_enabled, mock_llm):
    """SDK 抛任意异常都吞掉并返回 None。"""
    mock_llm(RuntimeError("connection reset"))

    assert AIService.parse_expense("张三垫了300", MEMBERS) is None
    assert AIService.get_last_failure() == "invalid"


def test_parse_expense_disabled_returns_none(settings, monkeypatch):
    """未启用时直接返回 None，且根本不会构造 openai 客户端。"""
    settings.OPENAI_API_KEY = ""
    spy = MagicMock()
    monkeypatch.setattr("openai.OpenAI", spy)

    assert AIService.parse_expense("张三垫了300", MEMBERS) is None
    assert AIService.get_last_failure() == "disabled"
    spy.assert_not_called()


def test_parse_expense_missing_field(ai_enabled, mock_llm):
    """缺少必填字段时返回 None。"""
    payload = _payload()
    payload.pop("participants")
    mock_llm(json.dumps(payload))

    assert AIService.parse_expense("张三垫了300", MEMBERS) is None


def test_parse_expense_note_truncated(ai_enabled, mock_llm):
    """note 超过 255 字符时截断。"""
    mock_llm(json.dumps(_payload(note="火" * 400)))

    draft = AIService.parse_expense("张三垫了300", MEMBERS)

    assert draft is not None
    assert len(draft["note"]) == 255


def test_parse_expense_unknown_category_falls_back(ai_enabled, mock_llm):
    """category 不在枚举内时回落 OTHER。"""
    mock_llm(json.dumps(_payload(category="SNACK")))

    draft = AIService.parse_expense("张三垫了300", MEMBERS)

    assert draft is not None
    assert draft["category"] == "OTHER"


def test_client_uses_configured_timeout_and_base_url(ai_enabled, monkeypatch):
    """客户端用配置的 timeout / base_url 构造。"""
    ai_enabled.OPENAI_BASE_URL = "https://api.deepseek.com/v1"
    constructor = MagicMock()
    constructor.return_value.chat.completions.create.return_value = _completion("ok")
    monkeypatch.setattr("openai.OpenAI", constructor)

    AIService._chat([{"role": "user", "content": "hi"}])

    kwargs = constructor.call_args.kwargs
    assert kwargs["timeout"] == 10
    assert kwargs["base_url"] == "https://api.deepseek.com/v1"


def test_categorize_valid(ai_enabled, mock_llm):
    """枚举内的值原样返回。"""
    mock_llm("FOOD")

    assert AIService.categorize_expense("火锅") == "FOOD"


def test_categorize_lowercase_normalized(ai_enabled, mock_llm):
    """小写会被 strip + upper 归一化。"""
    mock_llm("food")

    assert AIService.categorize_expense("火锅") == "FOOD"


def test_categorize_trailing_newline(ai_enabled, mock_llm):
    """带换行的返回值也能归一化。"""
    mock_llm("FOOD\n")

    assert AIService.categorize_expense("火锅") == "FOOD"


def test_categorize_out_of_enum_falls_back(ai_enabled, mock_llm):
    """枚举外的值回落 OTHER。"""
    mock_llm("DELIVERY")

    assert AIService.categorize_expense("外卖") == "OTHER"


def test_categorize_exception_falls_back(ai_enabled, mock_llm):
    """LLM 抛异常时回落 OTHER。"""
    mock_llm(RuntimeError("boom"))

    assert AIService.categorize_expense("外卖") == "OTHER"


def test_categorize_disabled_returns_other(settings, monkeypatch):
    """未启用时直接返回 OTHER，且不会发请求。"""
    settings.OPENAI_API_KEY = ""
    spy = MagicMock()
    monkeypatch.setattr("openai.OpenAI", spy)

    assert AIService.categorize_expense("火锅") == "OTHER"
    spy.assert_not_called()
