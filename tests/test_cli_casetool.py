"""casetool 工具测试。

验证 ``fcmd.cli.conv.casetool`` 模块：
- 工具注册与四子命令结构（snake/camel/pascal/kebab）
- ``to_snake``/``to_camel``/``to_pascal``/``to_kebab``
- 多种输入格式识别与转换
- CLI 子命令端到端
"""

from __future__ import annotations

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.conv.casetool import (
    to_camel,
    to_kebab,
    to_pascal,
    to_snake,
)


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """casetool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "casetool" in list_tools()

    def test_subcommands(self) -> None:
        """casetool 有 snake/camel/pascal/kebab 四个子命令。"""
        subs = list_subcommands("casetool")
        assert set(subs) == {"snake", "camel", "pascal", "kebab"}


# ============================================================================ #
# to_snake
# ============================================================================ #
class TestToSnake:
    """to_snake 测试。"""

    def test_camel_input(self) -> None:
        """camelCase 输入。"""
        assert to_snake("helloWorld") == "hello_world"

    def test_pascal_input(self) -> None:
        """PascalCase 输入。"""
        assert to_snake("HelloWorld") == "hello_world"

    def test_kebab_input(self) -> None:
        """kebab-case 输入。"""
        assert to_snake("hello-world") == "hello_world"

    def test_space_input(self) -> None:
        """空格分隔输入。"""
        assert to_snake("hello world") == "hello_world"

    def test_mixed_input(self) -> None:
        """混合分隔符输入。"""
        assert to_snake("hello_world-foo bar") == "hello_world_foo_bar"

    def test_acronym_input(self) -> None:
        """含连续大写（HTTPServer → http_server）。"""
        assert to_snake("HTTPServer") == "http_server"

    def test_single_word(self) -> None:
        """单词。"""
        assert to_snake("hello") == "hello"

    def test_empty(self) -> None:
        """空字符串。"""
        assert to_snake("") == ""

    def test_already_snake(self) -> None:
        """已是 snake_case 不变。"""
        assert to_snake("hello_world") == "hello_world"

    def test_numbers(self) -> None:
        """含数字。"""
        assert to_snake("hello2World") == "hello2_world"


# ============================================================================ #
# to_camel
# ============================================================================ #
class TestToCamel:
    """to_camel 测试。"""

    def test_snake_input(self) -> None:
        """snake_case 输入。"""
        assert to_camel("hello_world") == "helloWorld"

    def test_pascal_input(self) -> None:
        """PascalCase 输入（首字母转小写）。"""
        assert to_camel("HelloWorld") == "helloWorld"

    def test_kebab_input(self) -> None:
        """kebab-case 输入。"""
        assert to_camel("hello-world") == "helloWorld"

    def test_space_input(self) -> None:
        """空格分隔输入。"""
        assert to_camel("hello world") == "helloWorld"

    def test_single_word(self) -> None:
        """单词首字母小写。"""
        assert to_camel("hello") == "hello"
        assert to_camel("Hello") == "hello"

    def test_empty(self) -> None:
        """空字符串。"""
        assert to_camel("") == ""

    def test_multiple_words(self) -> None:
        """多词。"""
        assert to_camel("hello world foo") == "helloWorldFoo"


# ============================================================================ #
# to_pascal
# ============================================================================ #
class TestToPascal:
    """to_pascal 测试。"""

    def test_snake_input(self) -> None:
        """snake_case 输入。"""
        assert to_pascal("hello_world") == "HelloWorld"

    def test_camel_input(self) -> None:
        """camelCase 输入（首字母转大写）。"""
        assert to_pascal("helloWorld") == "HelloWorld"

    def test_kebab_input(self) -> None:
        """kebab-case 输入。"""
        assert to_pascal("hello-world") == "HelloWorld"

    def test_space_input(self) -> None:
        """空格分隔输入。"""
        assert to_pascal("hello world") == "HelloWorld"

    def test_single_word(self) -> None:
        """单词首字母大写。"""
        assert to_pascal("hello") == "Hello"
        assert to_pascal("Hello") == "Hello"

    def test_empty(self) -> None:
        """空字符串。"""
        assert to_pascal("") == ""

    def test_multiple_words(self) -> None:
        """多词。"""
        assert to_pascal("hello world foo") == "HelloWorldFoo"


# ============================================================================ #
# to_kebab
# ============================================================================ #
class TestToKebab:
    """to_kebab 测试。"""

    def test_camel_input(self) -> None:
        """camelCase 输入。"""
        assert to_kebab("helloWorld") == "hello-world"

    def test_pascal_input(self) -> None:
        """PascalCase 输入。"""
        assert to_kebab("HelloWorld") == "hello-world"

    def test_snake_input(self) -> None:
        """snake_case 输入。"""
        assert to_kebab("hello_world") == "hello-world"

    def test_space_input(self) -> None:
        """空格分隔输入。"""
        assert to_kebab("hello world") == "hello-world"

    def test_single_word(self) -> None:
        """单词。"""
        assert to_kebab("hello") == "hello"

    def test_empty(self) -> None:
        """空字符串。"""
        assert to_kebab("") == ""

    def test_acronym_input(self) -> None:
        """含连续大写。"""
        assert to_kebab("HTTPServer") == "http-server"


# ============================================================================ #
# CLI 子命令测试
# ============================================================================ #
class TestCasetoolCLI:
    """``casetool`` 通过 ``run_tool`` 调用测试。"""

    def test_snake(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd casetool snake。"""
        code = run_tool("casetool", ["snake", "helloWorld"])
        assert code == 0
        out = capsys.readouterr().out
        assert "hello_world" in out

    def test_camel(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd casetool camel。"""
        code = run_tool("casetool", ["camel", "hello world"])
        assert code == 0
        out = capsys.readouterr().out
        assert "helloWorld" in out

    def test_pascal(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd casetool pascal。"""
        code = run_tool("casetool", ["pascal", "hello-world"])
        assert code == 0
        out = capsys.readouterr().out
        assert "HelloWorld" in out

    def test_kebab(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd casetool kebab。"""
        code = run_tool("casetool", ["kebab", "HelloWorld"])
        assert code == 0
        out = capsys.readouterr().out
        assert "hello-world" in out

    def test_snake_acronym(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd casetool snake HTTPServer。"""
        code = run_tool("casetool", ["snake", "HTTPServer"])
        assert code == 0
        out = capsys.readouterr().out
        assert "http_server" in out

    def test_empty_input(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd casetool snake ''（空字符串）。"""
        code = run_tool("casetool", ["snake", ""])
        assert code == 0
        out = capsys.readouterr().out
        # 空字符串输出空行
        assert "" in out.splitlines()
