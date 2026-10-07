"""filerename 工具测试（DSL 内建动作声明 commands/filerename.toml）。

验证 ``fcmd filerename`` 的 DSL action 迁移语义：
- 工具注册（多子命令 DSL 工具，内置声明）
- 声明契约：``__dsl_action__`` 标记、无 cmd（fn 任务形态）
- 执行语义：replace/insert/case 三模式、--preview、冲突跳过、多文件批量
- 失败语义：无效正则/无效模式 → 任务失败汇总 + 退出码 1（行为变化：原版
  打印后退出码 0）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apis._tool_args import ToolSpec
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import ensure_tools_discovered

ensure_tools_discovered()  # 幂等：注册内置 DSL 命令（含 filerename）


# ---------------------------------------------------------------------- #
# 注册与声明验证
# ---------------------------------------------------------------------- #
class TestFilerenameRegistration:
    """filerename 经内置 DSL（action 原语）注册。"""

    def test_registered_as_dsl_multi_subcommand(self) -> None:
        """filerename 注册为内置 DSL 多子命令工具（replace/insert/case）。"""
        assert set(_TOOL_REGISTRY["filerename"]) == {"replace", "insert", "case"}

    def test_action_contract(self) -> None:
        """各子命令合成函数携带 __dsl_action__ 标记，cmd 为 None（fn 任务形态）。"""
        expected = {"replace": "filerename_replace", "insert": "filerename_insert", "case": "filerename_case"}
        for sub, action_name in expected.items():
            spec: ToolSpec = _TOOL_REGISTRY["filerename"][sub]
            assert spec.cmd is None
            assert getattr(spec.func, "__dsl_action__", None) == action_name
            assert not getattr(spec.func, "__dsl_empty_body__", False)

    def test_signature_from_action_impl(self) -> None:
        """CLI 参数 schema 拷贝自动作实现签名（param_help 来自动作描述符）。"""
        spec = _TOOL_REGISTRY["filerename"]["replace"]
        params = list(spec.func.__signature__.parameters)  # type: ignore[attr-defined]
        assert params == ["files", "pattern", "replacement", "preview"]
        assert (spec.param_help or {}).get("preview") == "仅预览不实际执行"


# ---------------------------------------------------------------------- #
# replace 执行语义
# ---------------------------------------------------------------------- #
class TestFilerenameReplaceRun:
    """``fcmd filerename replace`` 执行语义。"""

    def test_replace_success(self, tmp_path: Path) -> None:
        """正则替换主干，保留扩展名。"""
        f = tmp_path / "my old file.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["replace", str(f), r"\s+", "--replacement", "_"]) == 0
        assert (tmp_path / "my_old_file.txt").exists()
        assert not f.exists()

    def test_replace_preview(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--preview 仅预览不执行。"""
        f = tmp_path / "old.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["replace", str(f), "old", "--replacement", "new", "--preview"]) == 0
        assert "[预览]" in capsys.readouterr().out
        assert f.exists()

    def test_replace_no_match_keeps_file(self, tmp_path: Path) -> None:
        """主干不匹配时不执行重命名。"""
        f = tmp_path / "plain.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["replace", str(f), r"\d+", "--replacement", "x"]) == 0
        assert f.exists()

    def test_replace_backreference(self, tmp_path: Path) -> None:
        """替换字符串支持反向引用。"""
        f = tmp_path / "2026_report.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["replace", str(f), r"(\d+)_(\w+)", "--replacement", r"\2_\1"]) == 0
        assert (tmp_path / "report_2026.txt").exists()

    def test_replace_delete_match(self, tmp_path: Path) -> None:
        """replacement 缺省为空串即删除匹配部分。"""
        f = tmp_path / "data_draft.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["replace", str(f), "_draft"]) == 0
        assert (tmp_path / "data.txt").exists()

    def test_replace_skip_existing_target(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """目标已存在时跳过并提示（不覆盖）。"""
        f1 = tmp_path / "a old.txt"
        f2 = tmp_path / "a_old.txt"
        f1.write_text("1", encoding="utf-8")
        f2.write_text("2", encoding="utf-8")
        assert run_tool("filerename", ["replace", str(f1), r"\s+", "--replacement", "_"]) == 0
        assert f1.exists()
        assert f2.read_text(encoding="utf-8") == "2"
        assert "跳过" in capsys.readouterr().out

    def test_replace_missing_file_prints_hint(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """文件不存在时提示后继续，退出码 0（行为保持）。"""
        assert run_tool("filerename", ["replace", str(tmp_path / "missing.txt"), "x"]) == 0
        assert "文件不存在" in capsys.readouterr().out

    def test_replace_multiple_files(self, tmp_path: Path) -> None:
        """多文件批量替换。"""
        f1 = tmp_path / "a b.txt"
        f2 = tmp_path / "c d.txt"
        f1.write_text("x", encoding="utf-8")
        f2.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["replace", str(f1), str(f2), r"\s+", "--replacement", "_"]) == 0
        assert (tmp_path / "a_b.txt").exists()
        assert (tmp_path / "c_d.txt").exists()

    def test_replace_invalid_regex_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """无效正则 → 任务失败汇总 + 退出码 1（行为变化：原版打印后退出码 0）。"""
        f = tmp_path / "a.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["replace", str(f), "[invalid"]) == 1
        assert "失败" in capsys.readouterr().out
        assert f.exists()


# ---------------------------------------------------------------------- #
# insert 执行语义
# ---------------------------------------------------------------------- #
class TestFilerenameInsertRun:
    """``fcmd filerename insert`` 执行语义。"""

    def test_insert_at_start(self, tmp_path: Path) -> None:
        """开头插入前缀。"""
        f = tmp_path / "file.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["insert", str(f), "NEW_", "--position", "0"]) == 0
        assert (tmp_path / "NEW_file.txt").exists()

    def test_insert_in_middle(self, tmp_path: Path) -> None:
        """中间位置插入。"""
        f = tmp_path / "abcd.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["insert", str(f), "_v2", "--position", "2"]) == 0
        assert (tmp_path / "ab_v2cd.txt").exists()

    def test_insert_position_clamped(self, tmp_path: Path) -> None:
        """位置超出范围自动截断到边界。"""
        f = tmp_path / "abc.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["insert", str(f), "_end", "--position", "100"]) == 0
        assert (tmp_path / "abc_end.txt").exists()

    def test_insert_empty_text_noop(self, tmp_path: Path) -> None:
        """空文本不执行重命名。"""
        f = tmp_path / "keep.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["insert", str(f), ""]) == 0
        assert f.exists()

    def test_insert_preview(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--preview 仅预览不执行。"""
        f = tmp_path / "file.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["insert", str(f), "P_", "--preview"]) == 0
        assert "[预览]" in capsys.readouterr().out
        assert f.exists()


# ---------------------------------------------------------------------- #
# case 执行语义
# ---------------------------------------------------------------------- #
class TestFilerenameCaseRun:
    """``fcmd filerename case`` 执行语义。"""

    def test_case_lower(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """转小写（默认模式），扩展名保留。"""
        f = tmp_path / "File.TXT"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["case", str(f)]) == 0
        # Windows 大小写不敏感文件系统上仅大小写不同的重命名经回显断言
        assert "重命名: File.TXT -> file.TXT" in capsys.readouterr().out
        assert (tmp_path / "file.TXT").exists()

    def test_case_upper(self, tmp_path: Path) -> None:
        """--mode upper 转大写。"""
        f = tmp_path / "abc.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["case", str(f), "--mode", "upper"]) == 0
        assert (tmp_path / "ABC.txt").exists()

    def test_case_title(self, tmp_path: Path) -> None:
        """--mode title 转标题大小写。"""
        f = tmp_path / "my report.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["case", str(f), "--mode", "title"]) == 0
        assert (tmp_path / "My Report.pdf").exists()

    def test_case_no_change_needed(self, tmp_path: Path) -> None:
        """已是目标大小写时静默跳过。"""
        f = tmp_path / "abc.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["case", str(f), "--mode", "lower"]) == 0
        assert f.exists()

    def test_case_invalid_mode_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """无效模式 → 任务失败汇总 + 退出码 1（行为变化：原版打印后退出码 0）。"""
        f = tmp_path / "abc.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["case", str(f), "--mode", "snake"]) == 1
        assert "失败" in capsys.readouterr().out

    def test_case_preview(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--preview 仅预览不执行。"""
        f = tmp_path / "ABC.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["case", str(f), "--mode", "lower", "--preview"]) == 0
        assert "[预览]" in capsys.readouterr().out
        assert f.exists()
