"""filescan 正则校验器：命中片段通过校验才算命中，降低格式类规则误报。

支持 Luhn（银行卡）、中国身份证校验码、统一社会信用代码与 IBAN（mod-97）。
校验只做格式 / 校验位判断，不代表号码真实存在。
"""

from __future__ import annotations

VALIDATOR_NONE = ""
VALIDATOR_LUHN = "luhn"
VALIDATOR_CN_ID = "cn_id"
VALIDATOR_CREDIT_CODE = "credit_code"
VALIDATOR_IBAN = "iban"
VALIDATORS: tuple[str, ...] = (
    VALIDATOR_NONE,
    VALIDATOR_LUHN,
    VALIDATOR_CN_ID,
    VALIDATOR_CREDIT_CODE,
    VALIDATOR_IBAN,
)
VALIDATOR_LABELS: dict[str, str] = {
    VALIDATOR_NONE: "不校验",
    VALIDATOR_LUHN: "Luhn（银行卡）",
    VALIDATOR_CN_ID: "身份证校验码",
    VALIDATOR_CREDIT_CODE: "统一社会信用代码",
    VALIDATOR_IBAN: "IBAN mod-97",
}
"""校验器 id → 中文标签（GUI 下拉与规则摘要共用）。"""

_CN_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_CN_ID_CHECK = "10X98765432"
_CREDIT_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_CREDIT_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)


def _digits(value: str) -> str:
    return "".join(char for char in value if char.isdigit())


def _luhn(value: str) -> bool:
    digits = _digits(value)
    if not 12 <= len(digits) <= 19:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _cn_id(value: str) -> bool:
    text = value.strip().upper()
    if len(text) != 18 or not text[:17].isdigit():
        return False
    total = sum(int(char) * weight for char, weight in zip(text[:17], _CN_ID_WEIGHTS, strict=True))
    return _CN_ID_CHECK[total % 11] == text[17]


def _credit_code(value: str) -> bool:
    text = value.strip().upper()
    if len(text) != 18 or any(char not in _CREDIT_CHARS for char in text):
        return False
    total = sum(_CREDIT_CHARS.index(char) * weight for char, weight in zip(text[:17], _CREDIT_WEIGHTS, strict=True))
    return _CREDIT_CHARS[(31 - total % 31) % 31] == text[17]


def _iban(value: str) -> bool:
    text = "".join(char for char in value.upper() if char.isalnum())
    if not 15 <= len(text) <= 34 or not text[:2].isalpha():
        return False
    rearranged = text[4:] + text[:4]
    digits = "".join(str(int(char, 36)) for char in rearranged)
    return int(digits) % 97 == 1


def validate_value(value: str, validator: str) -> bool:
    """按校验器判断片段是否有效；未知校验器视为不通过（加载时已拦截）。"""
    if validator == VALIDATOR_NONE:
        return True
    if validator == VALIDATOR_LUHN:
        return _luhn(value)
    if validator == VALIDATOR_CN_ID:
        return _cn_id(value)
    if validator == VALIDATOR_CREDIT_CODE:
        return _credit_code(value)
    if validator == VALIDATOR_IBAN:
        return _iban(value)
    return False
