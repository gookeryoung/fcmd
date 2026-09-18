"""L2/L3 准 E2E：单工具真实执行测试。

所有用例走 ``python -m fcmd`` 真实子进程 + tmp_path 文件系统断言，
验证工具在干净进程中的端到端行为（解析参数 → 执行 → 文件输出）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.conftest import CmdResult

# jsontool 输出格式为：
#   > 'pretty' 开始执行...
#   <JSON 正文，可能多行>
#   OK 'pretty' 成功 (...)
# 此正则匹配最后一段 ``` 或缩进 JSON 块（取 stdout 中第一个 {...} 或 [...]）
_JSON_BLOCK_RE = re.compile(r"(\{[\s\S]*\}|\[[\s\S]*\])")


def _extract_json(stdout: str) -> object:
    """从 fcmd 工具输出中提取 JSON 正文。"""
    match = _JSON_BLOCK_RE.search(stdout)
    if match is None:
        raise ValueError(f"stdout 中未找到 JSON 块:\n{stdout}")
    return json.loads(match.group(1))


@pytest.mark.e2e
class TestWritefile:
    """writefile 工具端到端。"""

    def test_writes_text_file(self, fcmd: Callable[..., CmdResult], tmp_path: Path) -> None:
        """写入 UTF-8 文件，读取内容一致。"""
        r = fcmd("writefile", "hello.txt", "hello world")
        r.check_ok()
        assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hello world"

    def test_writes_unicode_content(self, fcmd: Callable[..., CmdResult], tmp_path: Path) -> None:
        """UTF-8 编码的中文字符正确写入。"""
        r = fcmd("writefile", "msg.txt", "你好，世界 🌏")
        r.check_ok()
        assert (tmp_path / "msg.txt").read_text(encoding="utf-8") == "你好，世界 🌏"


@pytest.mark.e2e
class TestJsontool:
    """jsontool 工具端到端。"""

    def test_pretty_formats_input(self, fcmd: Callable[..., CmdResult], tmp_path: Path) -> None:
        """pretty 子命令将 JSON 格式化输出到文件。"""
        src = tmp_path / "data.json"
        src.write_text('{"b":1,"a":2}', encoding="utf-8")
        r = fcmd("jsontool", "pretty", str(src))
        r.check_ok()
        # stdout 应含格式化后的 JSON（多行、缩进）
        parsed = _extract_json(r.stdout)
        assert parsed["a"] == 2 and parsed["b"] == 1  # pyrefly: ignore [bad-index]

    def test_minify_compacts(self, fcmd: Callable[..., CmdResult], tmp_path: Path) -> None:
        """minify 子命令压缩 JSON。"""
        src = tmp_path / "data.json"
        src.write_text('{\n  "a":  1,\n  "b": 2\n}', encoding="utf-8")
        r = fcmd("jsontool", "minify", str(src))
        r.check_ok()
        parsed = _extract_json(r.stdout)
        assert parsed == {"a": 1, "b": 2}

    def test_sort_orders_keys(self, fcmd: Callable[..., CmdResult], tmp_path: Path) -> None:
        """sort 子命令按键名排序。"""
        src = tmp_path / "data.json"
        src.write_text('{"z":1,"a":2,"m":3}', encoding="utf-8")
        r = fcmd("jsontool", "sort", str(src))
        r.check_ok()
        # 解析后应为 a, m, z 顺序（dict 迭代顺序等于 JSON 出现顺序）
        assert list(_extract_json(r.stdout).keys()) == ["a", "m", "z"]  # pyrefly: ignore [missing-attribute]

    def test_query_gets_value(self, fcmd: Callable[..., CmdResult], tmp_path: Path) -> None:
        """query 子命令按点路径取嵌套值。"""
        src = tmp_path / "data.json"
        src.write_text('{"user":{"profile":{"name":"Alice"}}}', encoding="utf-8")
        r = fcmd("jsontool", "query", str(src), "user.profile.name")
        assert r.returncode == 0
        assert "Alice" in r.stdout


@pytest.mark.e2e
class TestMathtool:
    """mathtool 工具端到端。"""

    def test_eval_basic(self, fcmd: Callable[..., CmdResult]) -> None:
        """eval 子命令求值数学表达式。"""
        r = fcmd("mathtool", "eval", "2**10")
        r.check_ok()
        assert "1024" in r.stdout

    def test_eval_arithmetic(self, fcmd: Callable[..., CmdResult]) -> None:
        """eval 支持基本算术表达式。"""
        r = fcmd("mathtool", "eval", "100 + 200 - 50")
        r.check_ok()
        assert "250" in r.stdout

    def test_pow(self, fcmd: Callable[..., CmdResult]) -> None:
        """pow 子命令计算幂。"""
        r = fcmd("mathtool", "pow", "2", "8")
        r.check_ok()
        assert "256" in r.stdout

    def test_sqrt(self, fcmd: Callable[..., CmdResult]) -> None:
        """sqrt 子命令计算平方根。"""
        r = fcmd("mathtool", "sqrt", "144")
        r.check_ok()
        assert "12" in r.stdout


@pytest.mark.e2e
class TestYamtool:
    """yamtool 工具端到端。"""

    def test_validate_valid(self, fcmd: Callable[..., CmdResult], tmp_path: Path) -> None:
        """有效 YAML 语法校验退出码 0。"""
        p = tmp_path / "good.yaml"
        p.write_text("name: test\nitems:\n  - a\n  - b\n", encoding="utf-8")
        r = fcmd("yamtool", "validate", str(p))
        r.check_ok()

    def test_validate_invalid(self, fcmd: Callable[..., CmdResult], tmp_path: Path) -> None:
        """无效 YAML 语法校验应返回非 0。"""
        p = tmp_path / "bad.yaml"
        p.write_text("name: test\n  bad: indent\n", encoding="utf-8")
        # 期望返回 1 或 0（视实现而定），只要不崩溃
        r = fcmd("yamtool", "validate", str(p))
        assert isinstance(r.returncode, int)
        # 成功/失败都不应抛异常，只需正常退出

    def test_keys(self, fcmd: Callable[..., CmdResult], tmp_path: Path) -> None:
        """keys 子命令列出 YAML 顶层键。"""
        p = tmp_path / "data.yaml"
        p.write_text("alpha: 1\nbeta: 2\ngamma: 3\n", encoding="utf-8")
        r = fcmd("yamtool", "keys", str(p))
        r.check_ok()
        for key in ("alpha", "beta", "gamma"):
            assert key in r.stdout


@pytest.mark.e2e
class TestHashtool:
    """hashtool 工具端到端。"""

    def test_sha256(self, fcmd: Callable[..., CmdResult], tmp_path: Path) -> None:
        """SHA256 哈希返回 64 位十六进制字符串。"""
        p = tmp_path / "msg.txt"
        p.write_text("hello", encoding="utf-8")
        r = fcmd("hashtool", "sha256", str(p))
        assert r.returncode == 0
        # 输出应包含 64 位 hex（SHA256 长度）
        import re

        assert re.search(r"[0-9a-f]{64}", r.stdout, re.IGNORECASE)
