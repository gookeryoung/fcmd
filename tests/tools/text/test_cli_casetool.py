"""casetool 工具测试。

验证 ``fcmd casetool`` 的命名风格转换：
- snake/camel/pascal/kebab 四子命令对多种输入风格的转换正确性
- 纯函数实现（``fcmd.infras.text``）的分隔符识别
"""

from __future__ import annotations

import pytest

from fcmd.infras.text import to_camel, to_kebab, to_pascal, to_snake
from tests.helpers import run_and_capture as _run_and_capture


# ====================================================================== #
# 纯函数
# ====================================================================== #
class TestCaseFunctions:
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
    def test_snake(self, inp: str, expected: str) -> None:
        assert to_snake(inp) == expected

    def test_camel(self) -> None:
        assert to_camel("hello world") == "helloWorld"

    def test_pascal(self) -> None:
        assert to_pascal("hello-world") == "HelloWorld"

    def test_kebab(self) -> None:
        assert to_kebab("HelloWorld") == "hello-world"

    def test_empty(self) -> None:
        assert to_snake("") == ""


# ====================================================================== #
# CLI 子命令
# ====================================================================== #
class TestCasetoolCLI:
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
    def test_snake(self, inp: str, expected: str, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("casetool", ["snake", inp], capsys)
        assert code == 0
        assert out.strip() == expected

    def test_snake_empty_input(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("casetool", ["snake", ""], capsys)
        assert code == 0
        assert out.strip() == ""

    @pytest.mark.parametrize(
        "inp, expected",
        [
            ("hello world", "helloWorld"),
            ("hello-world", "helloWorld"),
            ("hello_world", "helloWorld"),
            ("HelloWorld", "helloWorld"),
        ],
    )
    def test_camel(self, inp: str, expected: str, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("casetool", ["camel", inp], capsys)
        assert code == 0
        assert out.strip() == expected

    @pytest.mark.parametrize(
        "inp, expected",
        [
            ("hello world", "HelloWorld"),
            ("hello-world", "HelloWorld"),
            ("hello_world", "HelloWorld"),
            ("helloWorld", "HelloWorld"),
        ],
    )
    def test_pascal(self, inp: str, expected: str, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("casetool", ["pascal", inp], capsys)
        assert code == 0
        assert out.strip() == expected

    @pytest.mark.parametrize(
        "inp, expected",
        [
            ("HelloWorld", "hello-world"),
            ("hello_world", "hello-world"),
            ("hello world", "hello-world"),
            ("hello-world", "hello-world"),
        ],
    )
    def test_kebab(self, inp: str, expected: str, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("casetool", ["kebab", inp], capsys)
        assert code == 0
        assert out.strip() == expected
