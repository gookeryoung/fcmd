"""casetool 工具测试（DSL 内建动作声明 commands/casetool.toml）。

验证 ``fcmd casetool`` 的 DSL action 迁移语义：
- 工具注册（多子命令 DSL 工具，内置声明）
- 声明契约：__dsl_action__ 标记、无 cmd（fn 任务形态）
- snake/camel/pascal/kebab 四子命令对多种输入风格的转换正确性
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
class TestCasetoolRegistration:
    def test_subcommands(self) -> None:
        assert set(_TOOL_REGISTRY["casetool"]) == {"snake", "camel", "pascal", "kebab"}

    @pytest.mark.parametrize(
        "sub, action",
        [
            ("snake", "casetool_snake"),
            ("camel", "casetool_camel"),
            ("pascal", "casetool_pascal"),
            ("kebab", "casetool_kebab"),
        ],
    )
    def test_action_contract(self, sub: str, action: str) -> None:
        spec: ToolSpec = _TOOL_REGISTRY["casetool"][sub]
        assert spec.cmd is None
        assert getattr(spec.func, "__dsl_action__", None) == action


# ====================================================================== #
# snake_case
# ====================================================================== #
class TestCasetoolSnake:
    @pytest.mark.parametrize(
        "inp, expected",
        [
            ("HelloWorld", "hello_world"),
            ("hello-world", "hello_world"),
            ("hello_world", "hello_world"),
            ("HELLO", "hello"),
            ("HTTPServer", "http_server"),
            ("httpServer", "http_server"),
        ],
    )
    def test_convert(self, inp: str, expected: str, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("casetool", ["snake", inp], capsys)
        assert code == 0
        assert out.strip() == expected

    def test_empty_input(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("casetool", ["snake", ""], capsys)
        assert code == 0
        assert out.strip() == ""


# ====================================================================== #
# camelCase
# ====================================================================== #
class TestCasetoolCamel:
    @pytest.mark.parametrize(
        "inp, expected",
        [
            ("hello world", "helloWorld"),
            ("hello-world", "helloWorld"),
            ("hello_world", "helloWorld"),
            ("HelloWorld", "helloWorld"),
        ],
    )
    def test_convert(self, inp: str, expected: str, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("casetool", ["camel", inp], capsys)
        assert code == 0
        assert out.strip() == expected


# ====================================================================== #
# PascalCase
# ====================================================================== #
class TestCasetoolPascal:
    @pytest.mark.parametrize(
        "inp, expected",
        [
            ("hello world", "HelloWorld"),
            ("hello-world", "HelloWorld"),
            ("hello_world", "HelloWorld"),
            ("helloWorld", "HelloWorld"),
        ],
    )
    def test_convert(self, inp: str, expected: str, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("casetool", ["pascal", inp], capsys)
        assert code == 0
        assert out.strip() == expected


# ====================================================================== #
# kebab-case
# ====================================================================== #
class TestCasetoolKebab:
    @pytest.mark.parametrize(
        "inp, expected",
        [
            ("HelloWorld", "hello-world"),
            ("hello_world", "hello-world"),
            ("hello world", "hello-world"),
            ("hello-world", "hello-world"),
        ],
    )
    def test_convert(self, inp: str, expected: str, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("casetool", ["kebab", inp], capsys)
        assert code == 0
        assert out.strip() == expected
