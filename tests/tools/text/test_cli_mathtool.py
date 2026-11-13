"""mathtool 工具测试。

验证 ``fcmd mathtool``：
- eval 安全求值（AST 沙箱）、sqrt/pow/factorial 正确与错误分支
- 纯函数实现（``fcmd.infras.text``）
"""

from __future__ import annotations

import pytest

from fcmd.infras.text import eval_expr, factorial_value, power, sqrt_value
from tests.helpers import run_and_capture as _run_and_capture


# ====================================================================== #
# 纯函数
# ====================================================================== #
class TestMathFunctions:
    def test_eval_arithmetic(self) -> None:
        assert eval_expr("1 + 2 * 3") == 7

    def test_eval_syntax_error(self) -> None:
        with pytest.raises(ValueError, match="表达式语法错误"):
            eval_expr("1 +")

    def test_sqrt(self) -> None:
        assert sqrt_value(16) == 4.0

    def test_sqrt_negative(self) -> None:
        with pytest.raises(ValueError, match="sqrt 要求非负数"):
            sqrt_value(-1)

    def test_power(self) -> None:
        assert power(2, 10) == 1024

    def test_factorial(self) -> None:
        assert factorial_value(5) == 120

    def test_factorial_negative(self) -> None:
        with pytest.raises(ValueError, match="factorial 要求非负整数"):
            factorial_value(-1)


# ====================================================================== #
# eval —— AST 沙箱安全求值
# ====================================================================== #
class TestMathtoolEval:
    def test_arithmetic(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["eval", "1 + 2 * 3"], capsys)
        assert code == 0
        assert out.strip() == "7"

    def test_power(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["eval", "2 ** 10"], capsys)
        assert code == 0
        assert out.strip() == "1024"

    def test_parens(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["eval", "(1 + 2) * 3"], capsys)
        assert code == 0
        assert out.strip() == "9"

    def test_unary(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["eval", "-5 + 3"], capsys)
        assert code == 0
        assert out.strip() == "-2"

    def test_div_returns_float(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["eval", "10 / 4"], capsys)
        assert code == 0
        assert float(out.strip()) == 2.5

    def test_div_zero_shows_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["eval", "1 / 0"], capsys)
        assert code == 3
        assert "division by zero" in out.lower()

    def test_syntax_error_shows_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["eval", "1 +"], capsys)
        assert code == 3
        assert "表达式语法错误" in out

    def test_no_function_call(self, capsys: pytest.CaptureFixture[str]) -> None:
        """沙箱不允许函数调用（杜绝注入）。"""
        code, out = _run_and_capture("mathtool", ["eval", "__import__('os')"], capsys)
        assert code == 3
        assert "不支持" in out or "语法错误" in out


# ====================================================================== #
# sqrt
# ====================================================================== #
class TestMathtoolSqrt:
    def test_positive(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["sqrt", "16"], capsys)
        assert code == 0
        assert float(out.strip()) == 4.0

    def test_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["sqrt", "0"], capsys)
        assert code == 0
        assert float(out.strip()) == 0.0

    def test_negative_shows_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["sqrt", "-1"], capsys)
        assert code == 3
        assert "sqrt 要求非负数" in out


# ====================================================================== #
# pow
# ====================================================================== #
class TestMathtoolPow:
    def test_integer(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["pow", "2", "10"], capsys)
        assert code == 0
        # float 注解使 argparse 解析为 float，输出为 1024.0
        assert float(out.strip()) == 1024

    def test_fractional_exponent(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["pow", "4", "0.5"], capsys)
        assert code == 0
        assert float(out.strip()) == pytest.approx(2.0)

    def test_negative_exponent(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["pow", "2", "-1"], capsys)
        assert code == 0
        assert float(out.strip()) == pytest.approx(0.5)


# ====================================================================== #
# factorial
# ====================================================================== #
class TestMathtoolFactorial:
    def test_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["factorial", "0"], capsys)
        assert code == 0
        assert out.strip() == "1"

    def test_positive(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["factorial", "5"], capsys)
        assert code == 0
        assert out.strip() == "120"

    def test_negative_shows_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["factorial", "-1"], capsys)
        assert code == 3
        assert "factorial 要求非负整数" in out

    def test_non_integer_shows_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["factorial", "3.5"], capsys)
        # argparse 解析 float 会失败（需要 int）
        assert code != 0 or "factorial" in out
