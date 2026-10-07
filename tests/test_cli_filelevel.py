"""filelevel 工具测试（DSL 内建动作声明 commands/filelevel.toml）。

验证 ``fcmd filelevel`` 的 DSL action 迁移语义：
- 工具注册（多子命令 DSL 工具，内置声明）
- 声明契约：``__dsl_action__`` 标记、无 cmd（fn 任务形态）
- 执行语义：等级标记先清后加、仅移除括号包裹的标记、批量处理
- 失败语义：无效等级 → 任务失败汇总 + 退出码 1（行为变化：原版逐文件
  打印后退出码 0）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apis._tool_args import ToolSpec
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import ensure_tools_discovered

ensure_tools_discovered()  # 幂等：注册内置 DSL 命令（含 filelevel）


# ---------------------------------------------------------------------- #
# 注册与声明验证
# ---------------------------------------------------------------------- #
class TestFilelevelRegistration:
    """filelevel 经内置 DSL（action 原语）注册。"""

    def test_registered_as_dsl_multi_subcommand(self) -> None:
        """filelevel 注册为内置 DSL 多子命令工具（set）。"""
        assert set(_TOOL_REGISTRY["filelevel"]) == {"set"}

    def test_action_contract(self) -> None:
        """set 子命令合成函数携带 __dsl_action__ 标记，cmd 为 None（fn 任务形态）。"""
        spec: ToolSpec = _TOOL_REGISTRY["filelevel"]["set"]
        assert spec.cmd is None
        assert getattr(spec.func, "__dsl_action__", None) == "filelevel_set"
        assert not getattr(spec.func, "__dsl_empty_body__", False)

    def test_signature_from_action_impl(self) -> None:
        """CLI 参数 schema 拷贝自动作实现签名：files positional + level 选项。"""
        spec = _TOOL_REGISTRY["filelevel"]["set"]
        params = list(spec.func.__signature__.parameters)  # type: ignore[attr-defined]
        assert params == ["files", "level"]
        assert spec.func.__signature__.parameters["level"].default == 0  # type: ignore[attr-defined]


# ---------------------------------------------------------------------- #
# 执行语义
# ---------------------------------------------------------------------- #
class TestFilelevelRun:
    """``fcmd filelevel set`` 执行语义。"""

    def test_set_level_clears_default(self, tmp_path: Path) -> None:
        """level 缺省为 0，即清除已有等级标记。"""
        f = tmp_path / "report(INT).pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filelevel", ["set", str(f)]) == 0
        assert (tmp_path / "report.pdf").exists()

    def test_set_level_1_adds_pub(self, tmp_path: Path) -> None:
        """level=1 添加 PUB 标记。"""
        f = tmp_path / "report.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filelevel", ["set", str(f), "--level", "1"]) == 0
        assert (tmp_path / "report(PUB).pdf").exists()

    def test_set_level_2_replaces_mark(self, tmp_path: Path) -> None:
        """level=2 先清旧标记再加 INT。"""
        f = tmp_path / "report(PUB).pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filelevel", ["set", str(f), "--level", "2"]) == 0
        assert (tmp_path / "report(INT).pdf").exists()
        assert not f.exists()

    def test_set_level_3_con(self, tmp_path: Path) -> None:
        """level=3 添加 CON 标记。"""
        f = tmp_path / "a.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filelevel", ["set", str(f), "--level", "3"]) == 0
        assert (tmp_path / "a(CON).pdf").exists()

    def test_remove_only_bracket_wrapped_marks(self, tmp_path: Path) -> None:
        """仅移除括号包裹的标记，裸字符串标记保留。"""
        f = tmp_path / "report-PUB.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filelevel", ["set", str(f), "--level", "0"]) == 0
        # -PUB 后跟的是 . 不是右括号字符，属裸标记，保留
        assert f.exists()

    def test_remove_digit_marks(self, tmp_path: Path) -> None:
        """数字标记（1-9）一并清除。"""
        f = tmp_path / "file(2).pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filelevel", ["set", str(f), "--level", "0"]) == 0
        assert (tmp_path / "file.pdf").exists()

    def test_no_change_keeps_file(self, tmp_path: Path) -> None:
        """无标记且 level=0 时文件不动。"""
        f = tmp_path / "plain.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filelevel", ["set", str(f)]) == 0
        assert f.exists()

    def test_multiple_files(self, tmp_path: Path) -> None:
        """多文件批量处理。"""
        f1 = tmp_path / "a.pdf"
        f2 = tmp_path / "b.pdf"
        f1.write_text("x", encoding="utf-8")
        f2.write_text("x", encoding="utf-8")
        assert run_tool("filelevel", ["set", str(f1), str(f2), "--level", "4"]) == 0
        assert (tmp_path / "a(CLA).pdf").exists()
        assert (tmp_path / "b(CLA).pdf").exists()

    def test_missing_file_prints_hint(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """文件不存在时提示后继续，退出码 0（行为保持）。"""
        assert run_tool("filelevel", ["set", str(tmp_path / "missing.pdf")]) == 0
        assert "文件不存在" in capsys.readouterr().out

    def test_invalid_level_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """无效等级 → 任务失败汇总 + 退出码 1（行为变化：原版逐文件打印后退出码 0）。"""
        f = tmp_path / "a.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filelevel", ["set", str(f), "--level", "5"]) == 1
        assert "失败" in capsys.readouterr().out
        assert f.exists()
