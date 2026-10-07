"""mathtool 工具测试（DSL 内建动作声明 commands/mathtool.toml）。

验证 ``fcmd mathtool`` 的 DSL action 迁移语义：
- 工具注册（多子命令 DSL 工具，内置声明）
- 声明契约：__dsl_action__ 标记、无 cmd（fn 任务形态）
- eval 安全求值（AST 沙箱）、sqrt/pow/factorial 正确与错误分支
"""

from __future__ import annotations

import pytest

from fcmd.apis._tool_args import ToolSpec
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import ensure_tools_discovered

ensure_tools_discovered()


def _run_and_capture(name: str, argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = run_tool(name, argv)
    raw = capsys.readouterr().out
    # 过滤掉控制台帧（以 > 或 OK/FAILED 开头的行），保留动作 print 输出
    lines = [
        ln
        for ln in raw.splitlines()
        if not ln.startswith("> ") and not ln.startswith("OK ") and not ln.startswith("FAILED ")
    ]
    return code, "\n".join(lines)


# ====================================================================== #
# 注册与声明契约
# ====================================================================== #
class TestMathtoolRegistration:
    def test_subcommands(self) -> None:
        assert set(_TOOL_REGISTRY["mathtool"]) == {"eval", "sqrt", "pow", "factorial"}

    @pytest.mark.parametrize(
        "sub, action",
        [
            ("eval", "mathtool_eval"),
            ("sqrt", "mathtool_sqrt"),
            ("pow", "mathtool_pow"),
            ("factorial", "mathtool_factorial"),
        ],
    )
    def test_action_contract(self, sub: str, action: str) -> None:
        spec: ToolSpec = _TOOL_REGISTRY["mathtool"][sub]
        assert spec.cmd is None
        assert getattr(spec.func, "__dsl_action__", None) == action


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
        assert code == 0
        assert "division by zero" in out.lower()

    def test_syntax_error_shows_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["eval", "1 +"], capsys)
        assert code == 0
        assert "表达式语法错误" in out

    def test_no_function_call(self, capsys: pytest.CaptureFixture[str]) -> None:
        """沙箱不允许函数调用（杜绝注入）。"""
        code, out = _run_and_capture("mathtool", ["eval", "__import__('os')"], capsys)
        assert code == 0
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
        assert code == 0
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
        assert code == 0
        assert "factorial 要求非负整数" in out

    def test_non_integer_shows_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("mathtool", ["factorial", "3.5"], capsys)
        # argparse 解析 float 会失败（需要 int）
        assert code != 0 or "factorial" in out
