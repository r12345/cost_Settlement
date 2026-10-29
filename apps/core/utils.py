"""金额相关的通用工具：统一用 Decimal，禁止 float。

所有对外输出的金额都需要经过 :func:`to_money` 量化到分，避免出现
``98.99999999999`` 这类浮点残留，也保证 JSON 里始终是两位小数的字符串。
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def to_money(value):
    """把任意数值安全地转成两位小数的 Decimal。

    None、空字符串等无效输入按 0 处理；超出精度的部分四舍五入到分。
    """
    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        amount = value
    else:
        try:
            amount = Decimal(str(value).strip())
        except (InvalidOperation, ValueError, TypeError):
            return ZERO
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_positive_money(value, field="金额"):
    """把用户输入的金额解析为 > 0 的 Decimal，否则抛 ValueError。

    供 serializer 与 service 共用，保证金额永远不可能是 float 或负数。
    """
    amount = to_money(value)
    if amount <= ZERO:
        raise ValueError("{}必须是大于 0 的数值".format(field))
    return amount
