"""mathtool 工具测试。

验证 ``fcmd.cli.calc.mathtool`` 模块：
- 工具注册与四子命令结构（eval/sqrt/pow/factorial）
- ``eval_expr`` 安全求值（含 AST 解析、运算符支持、注入防护）
- ``sqrt``/``pow_``/``factorial`` 公共函数
- CLI 子命令端到端与错误分支
"""

from __future__ import annotations

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.calc.mathtool import (
    eval_expr,
    factorial,
    pow_,
    sqrt,
)


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """mathtool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "mathtool" in list_tools()

    def test_subcommands(self) -> None:
        """mathtool 有 eval/sqrt/pow/factorial 四个子命令。"""
        subs = list_subcommands("mathtool")
        assert set(subs) == {"eval", "sqrt", "pow", "factorial"}


# ============================================================================ #
# eval_expr
# ============================================================================ #
class TestEvalExpr:
    """eval_expr 安全求值测试。"""

    def test_basic_addition(self) -> None:
        """加法。"""
        assert eval_expr("1 + 2") == 3

    def test_precedence(self) -> None:
        """运算优先级。"""
        assert eval_expr("1 + 2 * 3") == 7
        assert eval_expr("(1 + 2) * 3") == 9

    def test_power(self) -> None:
        """幂运算。"""
        assert eval_expr("2 ** 10") == 1024

    def test_division(self) -> None:
        """真除法。"""
        assert eval_expr("7 / 2") == 3.5

    def test_floor_division(self) -> None:
        """整除。"""
        assert eval_expr("7 // 2") == 3

    def test_modulo(self) -> None:
        """取模。"""
        assert eval_expr("7 % 3") == 1

    def test_unary_negation(self) -> None:
        """一元负号。"""
        assert eval_expr("-5") == -5
        assert eval_expr("-(3 + 2)") == -5

    def test_unary_plus(self) -> None:
        """一元正号。"""
        assert eval_expr("+5") == 5

    def test_float_literal(self) -> None:
        """浮点字面量。"""
        assert eval_expr("3.14") == 3.14

    def test_whitespace(self) -> None:
        """表达式含空白。"""
        assert eval_expr("  1  +  2  ") == 3

    def test_zero_division_raises(self) -> None:
        """除零抛 ZeroDivisionError。"""
        with pytest.raises(ZeroDivisionError):
            eval_expr("1 / 0")

    def test_syntax_error_raises(self) -> None:
        """语法错误抛 ValueError。"""
        with pytest.raises(ValueError, match="表达式语法错误"):
            eval_expr("1 +")
        with pytest.raises(ValueError, match="表达式语法错误"):
            eval_expr("* 5")

    def test_unsupported_syntax_raises(self) -> None:
        """不支持的语法元素抛 ValueError。"""
        with pytest.raises(ValueError, match="不支持的语法元素"):
            eval_expr("__import__('os')")
        with pytest.raises(ValueError, match="不支持的语法元素"):
            eval_expr("foo(1)")

    def test_no_attribute_access(self) -> None:
        """禁止属性访问（代码注入防护）。"""
        with pytest.raises(ValueError, match="不支持的语法元素"):
            eval_expr("(1).__class__")

    def test_no_variable(self) -> None:
        """禁止变量名。"""
        with pytest.raises(ValueError, match="不支持的语法元素"):
            eval_expr("x + 1")

    def test_no_string_constant(self) -> None:
        """禁止字符串常量。"""
        with pytest.raises(ValueError, match="不支持的常量类型"):
            eval_expr("'hello'")

    def test_no_comparison(self) -> None:
        """禁止比较运算。"""
        with pytest.raises(ValueError, match="不支持的语法元素"):
            eval_expr("1 < 2")


# ============================================================================ #
# sqrt
# ============================================================================ #
class TestSqrt:
    """sqrt 平方根测试。"""

    def test_zero(self) -> None:
        """0 的平方根。"""
        assert sqrt(0) == 0.0

    def test_perfect_square(self) -> None:
        """完全平方数。"""
        assert sqrt(16) == 4.0
        assert sqrt(25) == 5.0

    def test_float(self) -> None:
        """浮点输入。"""
        assert sqrt(2.0) == pytest.approx(1.4142135623730951)

    def test_negative_raises(self) -> None:
        """负数抛 ValueError。"""
        with pytest.raises(ValueError, match="sqrt 要求非负数"):
            sqrt(-1)


# ============================================================================ #
# pow_
# ============================================================================ #
class TestPow:
    """pow_ 幂运算测试。"""

    def test_positive_exp(self) -> None:
        """正指数。"""
        assert pow_(2, 10) == 1024

    def test_zero_exp(self) -> None:
        """零指数（任何数的 0 次方为 1）。"""
        assert pow_(5, 0) == 1

    def test_negative_exp(self) -> None:
        """负指数返回 float。"""
        assert pow_(2, -1) == 0.5

    def test_float_base(self) -> None:
        """浮点底数。"""
        assert pow_(2.5, 2) == 6.25


# ============================================================================ #
# factorial
# ============================================================================ #
class TestFactorial:
    """factorial 阶乘测试。"""

    def test_zero(self) -> None:
        """0! == 1。"""
        assert factorial(0) == 1

    def test_one(self) -> None:
        """1! == 1。"""
        assert factorial(1) == 1

    def test_known_values(self) -> None:
        """已知阶乘值。"""
        assert factorial(5) == 120
        assert factorial(10) == 3628800

    def test_negative_raises(self) -> None:
        """负数抛 ValueError。"""
        with pytest.raises(ValueError, match="factorial 要求非负整数"):
            factorial(-1)

    def test_bool_rejected(self) -> None:
        """布尔值被拒绝（虽然 bool 是 int 子类）。"""
        with pytest.raises(ValueError, match="factorial 要求非负整数"):
            factorial(True)  # type: ignore[arg-type]


# ============================================================================ #
# CLI 子命令测试
# ============================================================================ #
class TestMathtoolCLI:
    """``mathtool`` 通过 ``run_tool`` 调用测试。"""

    def test_eval_basic(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd mathtool eval "1 + 2 * 3"。"""
        code = run_tool("mathtool", ["eval", "1 + 2 * 3"])
        assert code == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert "7" in lines

    def test_eval_power(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd mathtool eval "2 ** 10"。"""
        code = run_tool("mathtool", ["eval", "2 ** 10"])
        assert code == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert "1024" in lines

    def test_eval_syntax_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """eval 语法错误提示。"""
        code = run_tool("mathtool", ["eval", "1 +"])
        assert code == 0
        out = capsys.readouterr().out
        assert "表达式语法错误" in out

    def test_eval_zero_division(self, capsys: pytest.CaptureFixture[str]) -> None:
        """eval 除零提示。"""
        code = run_tool("mathtool", ["eval", "1 / 0"])
        assert code == 0
        out = capsys.readouterr().out
        # ZeroDivisionError 提示信息
        assert "division" in out.lower() or "除" in out

    def test_sqrt_valid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd mathtool sqrt 16。"""
        code = run_tool("mathtool", ["sqrt", "16"])
        assert code == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert "4.0" in lines

    def test_sqrt_negative(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sqrt 负数提示。"""
        code = run_tool("mathtool", ["sqrt", "-1"])
        assert code == 0
        out = capsys.readouterr().out
        assert "sqrt 要求非负数" in out

    def test_pow(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd mathtool pow 2 10。"""
        code = run_tool("mathtool", ["pow", "2", "10"])
        assert code == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        # CLI float 入参 → 返回 float（1024.0）
        assert any("1024" in line for line in lines)

    def test_factorial(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd mathtool factorial 5。"""
        code = run_tool("mathtool", ["factorial", "5"])
        assert code == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert "120" in lines

    def test_factorial_negative(self, capsys: pytest.CaptureFixture[str]) -> None:
        """factorial 负数提示。"""
        code = run_tool("mathtool", ["factorial", "-1"])
        assert code == 0
        out = capsys.readouterr().out
        assert "factorial 要求非负整数" in out
