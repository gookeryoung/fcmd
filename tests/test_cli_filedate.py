"""filedate 工具测试（DSL 内建动作声明 commands/filedate.toml）。

验证 ``fcmd filedate`` 的 DSL action 迁移语义：
- 工具注册（多子命令 DSL 工具，内置声明）
- 声明契约：``__dsl_action__`` 标记、无 cmd（fn 任务形态）、list[Path] 注解
- 执行语义：添加/更新日期前缀、清除前缀、点文件与缺失文件跳过
- dry-run 不执行
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from fcmd.apis._tool_args import ToolSpec
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import ensure_tools_discovered

ensure_tools_discovered()  # 幂等：注册内置 DSL 命令（含 filedate）


def _today() -> str:
    """当前日期 ``YYYYMMDD``（测试文件为新建，mtime/ctime 即现在）。"""
    return time.strftime("%Y%m%d")


# ---------------------------------------------------------------------- #
# 注册与声明验证
# ---------------------------------------------------------------------- #
class TestFiledateRegistration:
    """filedate 经内置 DSL（action 原语）注册。"""

    def test_registered_as_dsl_multi_subcommand(self) -> None:
        """filedate 注册为内置 DSL 多子命令工具（add/clear）。"""
        assert set(_TOOL_REGISTRY["filedate"]) == {"add", "clear"}

    def test_action_contract(self) -> None:
        """各子命令合成函数携带 __dsl_action__ 标记，cmd 为 None（fn 任务形态）。"""
        for sub, action_name in (("add", "dateprefix_add"), ("clear", "dateprefix_clear")):
            spec: ToolSpec = _TOOL_REGISTRY["filedate"][sub]
            assert spec.cmd is None
            assert getattr(spec.func, "__dsl_action__", None) == action_name
            assert not getattr(spec.func, "__dsl_empty_body__", False)

    def test_signature_from_action_impl(self) -> None:
        """CLI 参数 schema 拷贝自动作实现签名：files 为 positional 多值 list[Path]。"""
        spec = _TOOL_REGISTRY["filedate"]["add"]
        params = list(spec.func.__signature__.parameters)  # type: ignore[attr-defined]
        assert params == ["files"]
        assert spec.func.__annotations__["files"] == list[Path]


# ---------------------------------------------------------------------- #
# 执行语义
# ---------------------------------------------------------------------- #
class TestFiledateRun:
    """``fcmd filedate`` 执行语义。"""

    def test_add_prefix(self, tmp_path: Path) -> None:
        """add 为文件名添加日期前缀。"""
        f = tmp_path / "report.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filedate", ["add", str(f)]) == 0
        assert (tmp_path / f"{_today()}_report.pdf").exists()

    def test_add_updates_existing_prefix(self, tmp_path: Path) -> None:
        """add 先清旧前缀再加新前缀（更新日期，不叠加）。"""
        f = tmp_path / "20260101_report.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filedate", ["add", str(f)]) == 0
        assert (tmp_path / f"{_today()}_report.pdf").exists()
        assert not f.exists()

    def test_add_skips_dotfile(self, tmp_path: Path) -> None:
        """add 跳过点文件。"""
        f = tmp_path / ".hidden.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filedate", ["add", str(f)]) == 0
        assert f.exists()

    def test_add_skips_missing_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """add 对缺失文件静默跳过，退出码 0（行为保持）。"""
        assert run_tool("filedate", ["add", str(tmp_path / "missing.txt")]) == 0
        assert "失败" not in capsys.readouterr().out

    def test_clear_prefix(self, tmp_path: Path) -> None:
        """clear 移除日期前缀（含分隔符）。"""
        f = tmp_path / "20260115_report.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filedate", ["clear", str(f)]) == 0
        assert (tmp_path / "report.pdf").exists()

    def test_clear_without_prefix_prints_hint(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """clear 无前缀时打印提示，文件不动，退出码 0（行为保持）。"""
        f = tmp_path / "plain.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filedate", ["clear", str(f)]) == 0
        assert "无日期前缀" in capsys.readouterr().out
        assert f.exists()

    def test_clear_multiple_files(self, tmp_path: Path) -> None:
        """clear 支持多文件批量。"""
        f1 = tmp_path / "20260101_a.txt"
        f2 = tmp_path / "2026-01-02_b.txt"
        f1.write_text("x", encoding="utf-8")
        f2.write_text("x", encoding="utf-8")
        assert run_tool("filedate", ["clear", str(f1), str(f2)]) == 0
        assert (tmp_path / "a.txt").exists()
        assert (tmp_path / "b.txt").exists()

    def test_dry_run_skips_execution(self, tmp_path: Path) -> None:
        """--dry-run 打印计划不执行：文件不重命名。"""
        f = tmp_path / "report.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filedate", ["add", str(f), "--dry-run"]) == 0
        assert f.exists()
