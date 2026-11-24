"""文件校验器：Luhn / 身份证 / 统一社会信用代码 / IBAN。"""

from __future__ import annotations

import pytest

from fcmd.apps.filescan._validators import (
    VALIDATOR_CN_ID,
    VALIDATOR_CREDIT_CODE,
    VALIDATOR_IBAN,
    VALIDATOR_LUHN,
    VALIDATOR_NONE,
    validate_value,
)


@pytest.mark.parametrize(
    ("value", "validator", "expected"),
    [
        ("4539578763621486", VALIDATOR_LUHN, True),
        ("4539 5787 6362 1487", VALIDATOR_LUHN, False),
        ("11010519491231002X", VALIDATOR_CN_ID, True),
        ("110105194912310021", VALIDATOR_CN_ID, False),
        ("91350100M000100Y43", VALIDATOR_CREDIT_CODE, True),
        ("91350211M000100Y43", VALIDATOR_CREDIT_CODE, False),
        ("GB82WEST12345698765432", VALIDATOR_IBAN, True),
        ("GB82WEST12345698765431", VALIDATOR_IBAN, False),
        ("anything", VALIDATOR_NONE, True),
        ("anything", "unknown", False),
    ],
)
def test_validate_value(value: str, validator: str, expected: bool) -> None:
    assert validate_value(value, validator) is expected
