"""writefile 工具测试（DSL 内建动作声明 commands/writefile.toml）。

验证 ``fcmd writefile`` 的 DSL action 迁移语义：
- 工具注册（单命令 DSL 工具，内置声明）
- 声明契约：``__dsl_action__`` 标记、无 cmd（fn 任务形态）
- 执行语义：utf-8/自定义编码写入、覆盖写入
- 失败语义：父目录缺失 → 任务失败汇总 + 退出码 1（行为变化：原版裸 traceback）
- dry-run 不执行
"""

from __future__ import annotations

from pathlib import Path

import pytest

import fcmd as fx
from fcmd.apis._tool_args import ToolSpec
from fcmd.apis.toolkit import _TOOL_REGISTRY, get_tool, run_tool
from fcmd.cli._discovery import ensure_tools_discovered

ensure_tools_discovered()  # 幂等：注册内置 DSL 命令（含 writefile）


# ---------------------------------------------------------------------- #
# 注册与声明验证
# ---------------------------------------------------------------------- #
class TestWritefileRegistration:
    """writefile 经内置 DSL（action 原语）注册。"""

    def test_registered_as_dsl_single_command(self) -> None:
        """writefile 注册为内置 DSL 单命令工具。"""
        assert "writefile" in _TOOL_REGISTRY
        assert fx.list_subcommands("writefile") == []

    def test_action_contract(self) -> None:
        """合成函数携带 __dsl_action__ 标记，cmd 为 None，无 message 契约。"""
        spec: ToolSpec = get_tool("writefile")
        assert spec.cmd is None  # fn 任务形态（进程内动作，无子进程命令）
        assert getattr(spec.func, "__dsl_action__", None) == "writefile"
        assert not getattr(spec.func, "__dsl_empty_body__", False)  # 有函数逻辑 → 引擎 fn 任务
        assert not hasattr(spec.func, "__dsl_message__")  # 原版无完成消息，声明亦无

    def test_signature_from_action_impl(self) -> None:
        """CLI 参数 schema 拷贝自动作实现签名：path/content positional + encoding 选项。"""
        spec = get_tool("writefile")
        params = list(spec.func.__signature__.parameters)  # type: ignore[attr-defined]
        assert params == ["path", "content", "encoding"]
        assert (spec.param_help or {}).get("encoding") == "文件编码（默认 utf-8）"


# ---------------------------------------------------------------------- #
# 执行语义
# ---------------------------------------------------------------------- #
class TestWritefileRun:
    """``fcmd writefile`` 执行语义。"""

    def test_run_writes_utf8(self, tmp_path: Path) -> None:
        """fcmd writefile <path> <content> 默认 utf-8 写入。"""
        f = tmp_path / "note.txt"
        assert run_tool("writefile", [str(f), "Hello World"]) == 0
        assert f.read_text(encoding="utf-8") == "Hello World"

    def test_run_custom_encoding(self, tmp_path: Path) -> None:
        """--encoding 指定编码。"""
        f = tmp_path / "note.txt"
        assert run_tool("writefile", [str(f), "中文内容", "--encoding", "gbk"]) == 0
        assert f.read_text(encoding="gbk") == "中文内容"

    def test_run_overwrites_existing(self, tmp_path: Path) -> None:
        """覆盖已有文件。"""
        f = tmp_path / "note.txt"
        f.write_text("old", encoding="utf-8")
        assert run_tool("writefile", [str(f), "new"]) == 0
        assert f.read_text(encoding="utf-8") == "new"

    def test_failure_exit_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """父目录缺失 → 任务失败汇总 + 退出码 1（行为变化：原版裸 traceback）。"""
        f = tmp_path / "missing_dir" / "note.txt"
        assert run_tool("writefile", [str(f), "x"]) == 1
        out = capsys.readouterr().out
        assert "失败" in out

    def test_dry_run_skips_execution(self, tmp_path: Path) -> None:
        """--dry-run 打印计划不执行：文件不创建。"""
        f = tmp_path / "dry.txt"
        assert run_tool("writefile", [str(f), "x", "--dry-run"]) == 0
        assert not f.exists()
