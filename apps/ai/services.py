"""AI 能力封装：LLM 解析 / 分类（AIService）与支出向量（ExpenseEmbeddingService）。

四条硬约束：

1. 模块顶层不 import 任何业务 Model，需要时在方法内部 import，避免循环依赖；
2. AI 只生成「支出草稿」与「支出向量」，绝不改写业务数据；落库由 View 层转交
   ``ExpenseService``；
3. 未配置 ``OPENAI_API_KEY`` 时整体禁用；任何异常（超时 / 网络 / 非法 JSON /
   schema 不通过 / SDK 抛错）都吞掉并降级，绝不向上抛；
4. embedding 失败绝不影响支出创建，只记 warning。
"""

import json
import logging
from contextvars import ContextVar
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction

from apps.core.exceptions import BusinessError
from apps.core.utils import ZERO, to_money

logger = logging.getLogger(__name__)

CATEGORIES = ("FOOD", "TRANSPORT", "ACCOMMODATION", "ENTERTAINMENT", "OTHER")
SPLIT_TYPES = ("EQUAL", "EXACT", "PERCENTAGE")
DEFAULT_CATEGORY = "OTHER"

CATEGORY_LABELS = {
    "FOOD": "餐饮",
    "TRANSPORT": "交通",
    "ACCOMMODATION": "住宿",
    "ENTERTAINMENT": "娱乐",
    "OTHER": "其他",
}

MAX_NOTE_LENGTH = 255
MAX_TEXT_LENGTH = 2000
MAX_QUERY_LENGTH = 200
PERCENT_BASE = Decimal("100")

QUERY_INTENTS = ("search_expenses", "ask_balance")

FAILURE_DISABLED = "disabled"
FAILURE_TIMEOUT = "timeout"
FAILURE_INVALID = "invalid"

SYSTEM_PROMPT = (
    "你是一个记账解析器，只输出 JSON，不输出任何解释文字、Markdown 代码块或多余标点。"
    "你只能使用调用方给出的成员 id，禁止编造不存在的用户。"
)

QUERY_SYSTEM_PROMPT = (
    "你是一个账本查询意图解析器，只输出 JSON，不输出任何解释文字。"
    "你只负责判断意图与抽取过滤条件，不负责回答金额，也不可编造数据。"
)

ANSWER_SYSTEM_PROMPT = (
    "你是记账助手，只能依据给定的检索结果回答。"
    "不得编造记录，也不得自行计算或改写金额；不知道就直说不知道。"
)

CATEGORY_SYSTEM_PROMPT = (
    "你是一个支出分类器。可选分类只有五个："
    "FOOD（餐饮）、TRANSPORT（交通）、ACCOMMODATION（住宿）、"
    "ENTERTAINMENT（娱乐）、OTHER（其他）。"
    "只回答一个分类英文单词，不要标点、不要解释、不要输出其他任何文字。"
)


class _InvalidDraft(Exception):
    """内部信号：LLM 返回的结构不满足 schema。"""


def _is_int(value):
    """排除 bool（bool 是 int 的子类），只接受真正的整数。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_decimal(value, *, positive=True):
    """把任意输入安全解析成两位小数的 Decimal，失败返回 None。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        return None

    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None
    if amount != amount.quantize(Decimal("0.01")):
        return None
    if positive and amount <= ZERO:
        return None
    return amount.quantize(Decimal("0.01"))


class AIService:
    """LLM 能力的唯一入口。"""

    _last_failure = ContextVar("ai_last_failure", default=None)

    REASON_TEXT = {
        FAILURE_DISABLED: "AI 功能未启用",
        FAILURE_TIMEOUT: "AI 服务响应超时",
        FAILURE_INVALID: "无法从文本中解析出支出信息，请手动填写",
    }

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    @staticmethod
    def is_enabled():
        """是否可用：既要有总开关，也要配了 API Key。"""
        return bool(getattr(settings, "AI_ENABLED", False) and settings.OPENAI_API_KEY)

    @staticmethod
    def get_last_failure():
        """最近一次失败的类型：disabled / timeout / invalid / None。"""
        return AIService._last_failure.get()

    @staticmethod
    def failure_reason():
        """把失败类型翻译成给用户看的中文原因（不含异常文本）。"""
        code = AIService.get_last_failure() or FAILURE_INVALID
        return AIService.REASON_TEXT.get(code, AIService.REASON_TEXT[FAILURE_INVALID])

    # ------------------------------------------------------------------
    # 自然语言记账
    # ------------------------------------------------------------------
    @staticmethod
    def parse_expense(text, member_map):
        """把一句自然语言解析成支出草稿；任何失败都返回 None。

        :param text: 用户原话
        :param member_map: ``{username: user_id}``，当前群组的全部成员
        :return: 合法草稿 dict，或 None
        """
        AIService._last_failure.set(None)

        if not AIService.is_enabled():
            AIService._last_failure.set(FAILURE_DISABLED)
            return None

        text = (text or "").strip()
        if not text or not member_map:
            AIService._last_failure.set(FAILURE_INVALID)
            return None

        try:
            content = AIService._chat(
                AIService._build_parse_messages(text, member_map), json_mode=True
            )
        except Exception as exc:  # noqa: BLE001 - AI 异常必须全部降级
            if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
                AIService._last_failure.set(FAILURE_TIMEOUT)
            else:
                AIService._last_failure.set(FAILURE_INVALID)
            logger.warning("AI 解析失败：%s", type(exc).__name__)
            return None

        try:
            draft = AIService._validate_draft(content, member_map)
        except Exception as exc:  # noqa: BLE001 - 校验过程中的任何意外都降级
            logger.warning("AI 草稿校验异常：%s", type(exc).__name__)
            draft = None

        AIService._last_failure.set(None if draft else FAILURE_INVALID)
        return draft

    @staticmethod
    def categorize_expense(note):
        """把备注归到五个分类之一；失败/未启用/枚举外一律回落到 OTHER。"""
        if not AIService.is_enabled():
            return DEFAULT_CATEGORY

        note = (note or "").strip()
        if not note:
            return DEFAULT_CATEGORY

        try:
            content = AIService._chat(AIService._build_category_messages(note))
        except Exception as exc:  # noqa: BLE001
            logger.warning("AI 分类失败：%s", type(exc).__name__)
            return DEFAULT_CATEGORY

        if not isinstance(content, str):
            return DEFAULT_CATEGORY

        candidate = content.strip().strip("\"'“”").strip().upper()
        return candidate if candidate in CATEGORIES else DEFAULT_CATEGORY

    # ------------------------------------------------------------------
    # 提示词
    # ------------------------------------------------------------------
    @staticmethod
    def _build_parse_messages(text, member_map):
        """构造解析支出的 messages：成员清单注入用户提示词，约束只能选名单内的人。"""
        members = "\n".join(
            "- {} -> {}".format(username, user_id)
            for username, user_id in member_map.items()
        )
        user_prompt = (
            "当前群组成员（用户名 -> 用户 id）：\n"
            "{members}\n\n"
            "请从下面这句话里解析出一笔支出，并且只输出一个 JSON 对象，字段如下：\n"
            "{{\n"
            '  "payer_id": 1,                 // 付款人的用户 id，必须来自上面的列表\n'
            '  "amount": "300.00",            // 金额字符串，正数，最多两位小数\n'
            '  "participants": [1, 2, 3],     // 参与分摊的用户 id，非空，必须来自上面的列表\n'
            '  "split_type": "EQUAL",         // EQUAL | EXACT | PERCENTAGE\n'
            '  "splits": null,                // EQUAL 时必须为 null；\n'
            "                                 // EXACT 时填 [{{\"user_id\": 1, \"share_amount\": \"100.00\"}}]，\n"
            "                                 // PERCENTAGE 时填 [{{\"user_id\": 1, \"share_value\": \"50.00\"}}]\n"
            '  "note": "聚餐",                // 支出摘要，最多 255 字\n'
            '  "category": "FOOD",            // FOOD | TRANSPORT | ACCOMMODATION | ENTERTAINMENT | OTHER\n'
            '  "confidence": 0.85             // 0-1 的浮点数，表示解析把握度\n'
            "}}\n\n"
            "规则：\n"
            "1. payer_id 与 participants 只能从上面的成员列表里选，绝对不要编造 id；\n"
            "2. 文本没有说明分摊方式时用 EQUAL；明确说了「谁多少」用 EXACT，"
            "说了百分比用 PERCENTAGE；\n"
            "3. split_type 为 EQUAL 时 splits 必须是 null；\n"
            "4. EXACT 的 share_amount 合计必须严格等于 amount；\n"
            "5. PERCENTAGE 的 share_value 合计必须严格等于 100；\n"
            "6. 文本模糊、信息不足时，把 confidence 调低（例如 0.3），不要靠猜；\n"
            "7. 只输出 JSON，不要任何解释文字。\n\n"
            "文本：{text}"
        ).format(members=members, text=text)

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    @staticmethod
    def _build_category_messages(note):
        """构造分类的 messages：只要求返回一个枚举单词。"""
        user_prompt = (
            "请判断这条支出备注属于哪个分类：{note}\n"
            "可选值只有：FOOD / TRANSPORT / ACCOMMODATION / ENTERTAINMENT / OTHER。"
            "只回答其中一个英文单词。"
        ).format(note=note)
        return [
            {"role": "system", "content": CATEGORY_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    # ------------------------------------------------------------------
    # 账本问答：问题意图解析
    # ------------------------------------------------------------------
    @staticmethod
    def parse_query(text):
        """把自然语言问题解析成结构化查询意图；失败返回 None。

        返回：``{"intent", "semantic_query", "filters", "target_username", "confidence"}``
        """
        AIService._last_failure.set(None)

        if not AIService.is_enabled():
            AIService._last_failure.set(FAILURE_DISABLED)
            return None

        text = (text or "").strip()
        if not text:
            AIService._last_failure.set(FAILURE_INVALID)
            return None

        try:
            content = AIService._chat(
                AIService._build_query_messages(text), json_mode=True
            )
        except Exception as exc:  # noqa: BLE001 - 与 parse_expense 一致的降级
            if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
                AIService._last_failure.set(FAILURE_TIMEOUT)
            else:
                AIService._last_failure.set(FAILURE_INVALID)
            logger.warning("AI 问题解析失败：%s", type(exc).__name__)
            return None

        try:
            parsed = AIService._validate_query(content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("AI 问题结构校验异常：%s", type(exc).__name__)
            parsed = None

        AIService._last_failure.set(None if parsed else FAILURE_INVALID)
        return parsed

    @staticmethod
    def _build_query_messages(text):
        """构造问题解析的 messages：约束 intent 语义与 semantic_query 的写法。"""
        user_prompt = (
            "请把下面这句关于账本的问题解析成 JSON，字段如下：\n"
            "{{\n"
            '  "intent": "search_expenses",   // search_expenses | ask_balance\n'
            '  "semantic_query": "餐饮 火锅 聚餐",\n'
            '  "filters": {{"category": "FOOD", "date_from": "2026-09-01", '
            '"date_to": "2026-10-01", "payer_username": "zhangsan"}},\n'
            '  "target_username": null,\n'
            '  "confidence": 0.85\n'
            "}}\n\n"
            "intent 判断规则：\n"
            "1. 「花了多少」「有没有」「哪些」「买过什么」这类想找具体支出的问题 → search_expenses；\n"
            "2. 「谁欠我」「我还欠谁」「我的余额」「某某欠多少」这类问欠款余额的问题 → ask_balance，"
            "并把被问的人填进 target_username（问自己就填 null）。\n"
            "semantic_query 是给向量检索用的自然语言描述，不是关键词拆词；"
            "用户问「吃火锅」时可以写成「餐饮 火锅 聚餐」。\n"
            "filters 是可选的硬过滤条件：category 只能是 FOOD / TRANSPORT / "
            "ACCOMMODATION / ENTERTAINMENT / OTHER；日期用 YYYY-MM-DD 格式；"
            "payer_username 是付款人的用户名。没有条件就填空对象 {{}}。\n"
            "confidence 是解析把握度，文本模糊时给低分（例如 0.3）。\n"
            "只输出 JSON，不要任何解释文字。\n\n"
            "问题：{text}"
        ).format(text=text)

        return [
            {"role": "system", "content": QUERY_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    @staticmethod
    def _validate_query(content):
        """校验问题解析结果，任一不通过返回 None。"""
        if not isinstance(content, str) or not content.strip():
            return None

        try:
            payload = json.loads(content)
        except (ValueError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None

        intent = payload.get("intent")
        if intent not in QUERY_INTENTS:
            return None

        semantic_query = payload.get("semantic_query") or ""
        if not isinstance(semantic_query, str):
            return None
        semantic_query = semantic_query.strip()
        if intent == "search_expenses":
            if not semantic_query or len(semantic_query) > MAX_QUERY_LENGTH:
                return None

        filters = AIService._validate_query_filters(payload.get("filters"))
        if filters is None:
            return None

        target_username = payload.get("target_username")
        if target_username is not None and not isinstance(target_username, str):
            return None
        target_username = (target_username or "").strip() or None
        if intent != "ask_balance":
            target_username = None

        confidence = AIService._validate_confidence(payload.get("confidence"))
        if confidence is None:
            return None

        return {
            "intent": intent,
            "semantic_query": semantic_query,
            "filters": filters,
            "target_username": target_username,
            "confidence": confidence,
        }

    @staticmethod
    def _validate_query_filters(raw_filters):
        """校验 filters 子结构；非法返回 None，缺省返回空 dict。"""
        if raw_filters is None:
            return {}
        if not isinstance(raw_filters, dict):
            return None

        filters = {}

        category = raw_filters.get("category")
        if category:
            if not isinstance(category, str):
                return None
            category = category.strip().upper()
            if category not in CATEGORIES:
                return None
            filters["category"] = category

        payer_username = raw_filters.get("payer_username")
        if payer_username:
            if not isinstance(payer_username, str):
                return None
            filters["payer_username"] = payer_username.strip()

        for key in ("date_from", "date_to"):
            value = raw_filters.get(key)
            if not value:
                continue
            if not isinstance(value, str):
                return None
            try:
                date.fromisoformat(value.strip())
            except (TypeError, ValueError):
                return None
            filters[key] = value.strip()

        return filters

    # ------------------------------------------------------------------
    # LLM 调用
    # ------------------------------------------------------------------
    @staticmethod
    def _create_client():
        """按环境变量构造 OpenAI 兼容客户端；未安装 SDK 时抛 ImportError。"""
        from openai import OpenAI

        options = {
            "api_key": settings.OPENAI_API_KEY,
            "timeout": settings.OPENAI_TIMEOUT,
            "max_retries": 0,
        }
        if settings.OPENAI_BASE_URL:
            options["base_url"] = settings.OPENAI_BASE_URL
        return OpenAI(**options)

    @staticmethod
    def _chat(messages, json_mode=False):
        """发起一次 chat completion，返回纯文本内容。"""
        client = AIService._create_client()
        kwargs = {
            "model": settings.OPENAI_MODEL,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 1024,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        completion = client.chat.completions.create(**kwargs)
        return completion.choices[0].message.content

    # ------------------------------------------------------------------
    # 草稿校验
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_draft(content, member_map):
        """逐条校验 LLM 返回的 JSON，任一不通过返回 None。"""
        if not isinstance(content, str) or not content.strip():
            return None

        try:
            payload = json.loads(content)
        except (ValueError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None

        for field in ("payer_id", "amount", "participants", "split_type", "confidence"):
            if field not in payload:
                return None

        allowed_ids = set(member_map.values())

        payer_id = payload["payer_id"]
        if not _is_int(payer_id) or payer_id not in allowed_ids:
            return None

        amount = _parse_decimal(payload["amount"], positive=True)
        if amount is None:
            return None

        participants = payload["participants"]
        if not isinstance(participants, list) or not participants:
            return None

        ordered_participants = []
        for user_id in participants:
            if not _is_int(user_id) or user_id not in allowed_ids:
                return None
            if user_id not in ordered_participants:
                ordered_participants.append(user_id)
        if not ordered_participants:
            return None

        split_type = payload["split_type"]
        if not isinstance(split_type, str):
            return None
        split_type = split_type.strip().upper()
        if split_type not in SPLIT_TYPES:
            return None

        confidence = AIService._validate_confidence(payload["confidence"])
        if confidence is None:
            return None

        try:
            splits = AIService._validate_splits(
                payload.get("splits"), split_type, ordered_participants, amount
            )
        except _InvalidDraft:
            return None

        note = payload.get("note", "")
        note = note.strip()[:MAX_NOTE_LENGTH] if isinstance(note, str) else ""

        category = payload.get("category", DEFAULT_CATEGORY)
        if isinstance(category, str):
            category = category.strip().upper()
        if category not in CATEGORIES:
            category = DEFAULT_CATEGORY

        return {
            "payer_id": payer_id,
            "amount": str(amount),
            "participants": ordered_participants,
            "split_type": split_type,
            "splits": splits,
            "note": note,
            "category": category,
            "confidence": confidence,
        }

    @staticmethod
    def _validate_confidence(value):
        """confidence 必须在 [阈值, 1.0] 之间。"""
        if isinstance(value, bool) or value is None:
            return None
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return None
        threshold = float(getattr(settings, "AI_CONFIDENCE_THRESHOLD", 0.6))
        if confidence < threshold or confidence > 1.0:
            return None
        return confidence

    @staticmethod
    def _validate_splits(raw_splits, split_type, participants, amount):
        """校验 EXACT / PERCENTAGE 的 splits，并转成草稿格式（金额字符串）。"""
        if split_type == "EQUAL":
            return None

        if not isinstance(raw_splits, list) or not raw_splits:
            raise _InvalidDraft()

        value_field = "share_amount" if split_type == "EXACT" else "share_value"
        mapping = {}
        for item in raw_splits:
            if not isinstance(item, dict):
                raise _InvalidDraft()
            user_id = item.get("user_id")
            if not _is_int(user_id) or user_id not in participants:
                raise _InvalidDraft()
            if user_id in mapping:
                raise _InvalidDraft()
            value = _parse_decimal(item.get(value_field), positive=split_type == "EXACT")
            if value is None:
                raise _InvalidDraft()
            mapping[user_id] = value

        if set(mapping) != set(participants):
            raise _InvalidDraft()

        total = sum(mapping.values(), ZERO)
        if split_type == "EXACT" and total != amount:
            raise _InvalidDraft()
        if split_type == "PERCENTAGE" and total != PERCENT_BASE:
            raise _InvalidDraft()

        return [
            {"user_id": user_id, value_field: str(mapping[user_id])}
            for user_id in participants
        ]


class ExpenseEmbeddingService:
    """支出 embedding 的生成与维护（语义检索的数据源）。

    与 :class:`AIService` 保持一致的三条原则：

    1. 模块顶层不 import 任何业务 Model，需要时在方法内部 import，避免循环依赖；
    2. AI 未启用时整体跳过，不发起任何外部请求；
    3. 任何失败（网络 / 超时 / 维度不符 / 落库异常）都只记 ``logger.warning``，
       绝不向上抛，因此可以安全地放在 ``ExpenseService`` 的事务里后置调用。
    """

    DIMENSIONS = 1536
    EMPTY_NOTE = "未填写备注"

    @staticmethod
    def is_enabled():
        """复用 AIService 的启用判定（总开关 + API Key）。"""
        return AIService.is_enabled()

    @staticmethod
    def build_content(expense):
        """拼出用于 embedding 的文本。

        格式固定为 ``支出: {note} | 分类: {category} | 付款人: {payer} | 参与人: {p1, p2}``，
        参与人按 username 字典序排列，保证同一笔支出重复生成时文本完全一致。
        """
        note = (expense.note or "").strip() or ExpenseEmbeddingService.EMPTY_NOTE
        participants = sorted(
            split.user.username
            for split in expense.splits.select_related("user")
        )
        return "支出: {} | 分类: {} | 付款人: {} | 参与人: {}".format(
            note,
            expense.category,
            expense.payer.username,
            ", ".join(participants),
        )

    @staticmethod
    def upsert(expense):
        """生成或更新该支出的 embedding；任何失败都吞掉。"""
        if not ExpenseEmbeddingService.is_enabled():
            return

        try:
            content = ExpenseEmbeddingService.build_content(expense)
            vector = ExpenseEmbeddingService._create_embedding(content)
            if vector is None:
                return
            ExpenseEmbeddingService._save(expense, content, vector)
        except Exception as exc:  # noqa: BLE001 - 绝不影响支出主流程
            logger.warning("支出 embedding 生成失败：%s", type(exc).__name__)

    @staticmethod
    def delete_for_expense(expense):
        """显式清理某笔支出的 embedding（硬删除时由外键级联兜底）。"""
        from apps.ai.models import ExpenseEmbedding

        ExpenseEmbedding.objects.filter(expense_id=expense.pk).delete()

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    @staticmethod
    def _create_embedding(content):
        """调用兼容接口生成向量；维度不符返回 None。

        优先使用 embedding 的独立配置；未配置时回落到 chat 的配置。
        """
        from openai import OpenAI

        options = {
            "api_key": settings.OPENAI_EMBEDDING_API_KEY or settings.OPENAI_API_KEY,
            "timeout": 10,
            "max_retries": 0,
        }
        base_url = settings.OPENAI_EMBEDDING_BASE_URL or settings.OPENAI_BASE_URL
        if base_url:
            options["base_url"] = base_url

        client = OpenAI(**options)
        response = ExpenseEmbeddingService._request_embedding(client, content)
        vector = response.data[0].embedding

        if not isinstance(vector, (list, tuple)) or len(vector) != ExpenseEmbeddingService.DIMENSIONS:
            logger.warning(
                "embedding 维度不符：期望 %s，实际 %s，已跳过写入",
                ExpenseEmbeddingService.DIMENSIONS,
                len(vector) if hasattr(vector, "__len__") else "未知",
            )
            return None
        return list(vector)

    @staticmethod
    def _request_embedding(client, content):
        """请求向量：显式要求 1536 维，顶层参数不被支持时降级到 extra_body。

        通义 text-embedding-v4 默认返回 1024 维，必须显式传 ``dimensions=1536``
        才能和 ``VectorField(1536)`` 匹配；部分兼容网关不认顶层参数，则用
        ``extra_body`` 传同名字段。
        """
        dims = ExpenseEmbeddingService.DIMENSIONS
        try:
            return client.embeddings.create(
                model=settings.OPENAI_EMBEDDING_MODEL,
                input=[content],
                dimensions=dims,
            )
        except Exception as exc:  # noqa: BLE001 - 只对「参数不被支持」降级，其余照抛
            if not ExpenseEmbeddingService._is_dimension_unsupported(exc):
                raise
            logger.warning("dimensions 顶层参数不被支持，已降级到 extra_body")
            return client.embeddings.create(
                model=settings.OPENAI_EMBEDDING_MODEL,
                input=[content],
                extra_body={"dimensions": dims},
            )

    @staticmethod
    def _is_dimension_unsupported(exc):
        """判断异常是否因为服务端不认 dimensions 参数。"""
        if isinstance(exc, TypeError):
            return True
        if getattr(exc, "status_code", None) == 400:
            return True
        return type(exc).__name__ in {"BadRequestError", "UnprocessableEntityError"}

    @staticmethod
    def _save(expense, content, vector):
        """落库；用 savepoint 包住，失败时只回滚这一条，不污染外层事务。"""
        from apps.ai.models import ExpenseEmbedding

        with transaction.atomic():
            ExpenseEmbedding.objects.update_or_create(
                expense_id=expense.pk,
                defaults={
                    "group_id": expense.group_id,
                    "content": content,
                    "embedding": vector,
                },
            )


class RagQueryService:
    """账本语义检索 + 问答（RAG 阶段 B）。

    分工明确：

    * **发现**：pgvector 余弦距离检索，配合 ORM 的结构化过滤；
    * **数值**：金额汇总用 ``Sum()``、余额用 ``GroupService``，LLM 不参与计算；
    * **解释**：LLM 只把检索结果改写成自然语言，且失败时降级为固定文案。
    """

    MAX_SOURCES = 10
    TOTAL_KEYWORDS = ("总共", "一共", "合计", "总计", "共花", "总额")

    @staticmethod
    def answer(group, user, question):
        """回答问题，返回 ``{"answer", "intent", "sources"}``。"""
        from apps.groups.services import GroupService

        GroupService.require_member(group, user)

        if not AIService.is_enabled():
            raise BusinessError("AI 功能未启用", code="ai_disabled")

        parsed = AIService.parse_query(question)
        if parsed is None:
            raise BusinessError("无法理解该问题，请换个问法", code="query_parse_failed")

        if parsed["intent"] == "ask_balance":
            return RagQueryService._answer_balance(group, user, parsed)
        return RagQueryService._answer_search(group, parsed, question)

    # ------------------------------------------------------------------
    # 余额类问题：完全确定性，不经过 LLM
    # ------------------------------------------------------------------
    @staticmethod
    def _answer_balance(group, user, parsed):
        from apps.groups.models import GroupMember
        from apps.groups.services import GroupService
        from apps.users.models import User

        target_username = parsed.get("target_username")
        if target_username:
            target_user = User.objects.filter(username=target_username).first()
            if target_user is None or not GroupMember.objects.filter(
                group=group, user=target_user
            ).exists():
                raise BusinessError(
                    "用户 {} 不在该群组内".format(target_username),
                    code="target_not_member",
                )
        else:
            target_user = user

        balance = GroupService.get_user_net_balance(group, target_user)
        answer = "{} 在群组内的净余额为 {} 元".format(target_user.username, balance)
        return {"answer": answer, "intent": "ask_balance", "sources": []}

    # ------------------------------------------------------------------
    # 检索类问题：向量检索 + ORM 汇总 + LLM 解释
    # ------------------------------------------------------------------
    @staticmethod
    def _answer_search(group, parsed, question):
        from django.db.models import Sum
        from pgvector.django import CosineDistance

        from apps.ai.models import ExpenseEmbedding

        vector = RagQueryService._embed_text(parsed["semantic_query"])
        if vector is None:
            raise BusinessError("语义检索服务暂时不可用", code="embedding_failed")

        queryset = ExpenseEmbedding.objects.filter(
            group=group,
            expense__is_deleted=False,
            embedding__isnull=False,
        ).select_related("expense", "expense__payer")

        filters = parsed.get("filters") or {}
        if filters.get("category"):
            queryset = queryset.filter(expense__category=filters["category"])
        if filters.get("payer_username"):
            queryset = queryset.filter(
                expense__payer__username=filters["payer_username"]
            )
        if filters.get("date_from"):
            queryset = queryset.filter(
                expense__created_at__date__gte=filters["date_from"]
            )
        if filters.get("date_to"):
            queryset = queryset.filter(
                expense__created_at__date__lte=filters["date_to"]
            )

        rows = list(
            queryset.annotate(distance=CosineDistance("embedding", vector)).order_by(
                "distance"
            )[: RagQueryService.MAX_SOURCES]
        )

        sources = [
            {
                "expense_id": row.expense_id,
                "note": row.expense.note,
                "amount": str(to_money(row.expense.amount)),
                "category": row.expense.category,
                "created_at": row.expense.created_at.isoformat(),
                "payer_username": row.expense.payer.username,
            }
            for row in rows
        ]

        total = None
        if RagQueryService._needs_total(question):
            aggregate = queryset.aggregate(total=Sum("expense__amount"))["total"]
            total = to_money(aggregate)

        answer = RagQueryService._compose_answer(question, sources, total)
        return {"answer": answer, "intent": "search_expenses", "sources": sources}

    @staticmethod
    def _embed_text(text):
        """生成查询向量；任何失败返回 None（由调用方转成 embedding_failed）。"""
        try:
            return ExpenseEmbeddingService._create_embedding(text)
        except Exception as exc:  # noqa: BLE001 - 失败由上层降级
            logger.warning("查询向量生成失败：%s", type(exc).__name__)
            return None

    @staticmethod
    def _needs_total(question):
        """问题里是否要求金额汇总。"""
        text = question or ""
        return any(keyword in text for keyword in RagQueryService.TOTAL_KEYWORDS)

    @staticmethod
    def _compose_answer(question, sources, total):
        """让 LLM 基于检索结果写答案；失败时返回固定降级文案。"""
        fallback = "找到 {} 条相关记录，但无法生成解释，请查看下方原始记录".format(
            len(sources)
        )
        try:
            content = AIService._chat(
                RagQueryService._build_answer_messages(question, sources, total)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("AI 答案生成失败：%s", type(exc).__name__)
            return fallback

        if not isinstance(content, str) or not content.strip():
            return fallback
        return content.strip()

    @staticmethod
    def _build_answer_messages(question, sources, total):
        """构造答案生成的 messages：只给检索结果与确定性汇总，禁止 LLM 自行计算。"""
        records = json.dumps(sources, ensure_ascii=False, indent=2)
        if total is None:
            total_line = "本次没有做金额汇总。"
        else:
            total_line = "数据库精确汇总：符合条件的记录合计 {} 元。".format(total)

        user_prompt = (
            "用户问题：{question}\n\n"
            "从账本中检索到的相关支出记录（最多 10 条，JSON）：\n{records}\n\n"
            "{total_line}\n\n"
            "回答要求：\n"
            "1. 只基于上面提供的记录回答，不知道就说不知道，不要编造记录或金额；\n"
            "2. 金额必须引用上面的汇总数字或记录中的数字，不要自己计算或改写；\n"
            "3. 用中文回答，控制在 3 句话以内。"
        ).format(question=question, records=records, total_line=total_line)

        return [
            {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
