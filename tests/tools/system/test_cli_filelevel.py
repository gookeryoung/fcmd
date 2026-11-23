"""filelevel 工具测试。

验证 ``fcmd filelevel``：
- 工具注册（仅 set 子命令）
- 等级标记先清后加、仅移除括号包裹的标记、批量处理
- 等级 / 括号配置从 ``${FCMD_HOME}/filelevel.json`` 直接读取
- 无效等级 → 任务失败汇总 + 退出码 1
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.core.toolkit import list_subcommands, list_tools, run_tool
from fcmd.infras.fs import config_path, default_config, load_filelevel_config


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """等级配置写到测试隔离目录，避免读 / 写开发机的 ~/.fcmd。"""
    home = tmp_path / "fcmd-home"
    monkeypatch.setenv("FCMD_HOME", str(home))
    return home


def _write_config(levels: dict[str, str] | None = None, brackets: dict[str, str] | None = None) -> None:
    payload = {
        "schema": "filelevel-config/1",
        "levels": levels if levels is not None else dict(default_config().levels),
        "brackets": brackets if brackets is not None else dict(default_config().brackets),
    }
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class TestRegistration:
    """filelevel 注册为多子命令工具。"""

    def test_registered(self) -> None:
        """filelevel 已注册且只有 set 子命令（配置直接读本地文件，不提供 config 命令）。"""
        assert "filelevel" in list_tools()
        assert set(list_subcommands("filelevel")) == {"set"}


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
        """文件不存在时提示后继续，退出码 0。"""
        assert run_tool("filelevel", ["set", str(tmp_path / "missing.pdf")]) == 0
        assert "文件不存在" in capsys.readouterr().out

    def test_invalid_level_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """无效等级 → 任务失败汇总 + 退出码 1。"""
        f = tmp_path / "a.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filelevel", ["set", str(f), "--level", "5"]) == 1
        assert "失败" in capsys.readouterr().out
        assert f.exists()


class TestLocalConfig:
    """配置直接读本地文件，没有 config 子命令。"""

    def test_config_path_uses_fcmd_home(self, _isolated_home: Path) -> None:
        assert config_path() == _isolated_home / "filelevel.json"

    def test_custom_level_mapping_takes_effect(self, tmp_path: Path) -> None:
        """手工改等级映射后，下一次 set 立即使用新标记。"""
        levels = {"0": "", "1": "PUBLIC", "2": "INTERNAL"}
        _write_config(levels=levels)
        f = tmp_path / "report.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filelevel", ["set", str(f), "--level", "1"]) == 0
        assert (tmp_path / "report(PUBLIC).pdf").exists()
        assert run_tool("filelevel", ["set", str(tmp_path / "report(PUBLIC).pdf"), "--level", "2"]) == 0
        assert (tmp_path / "report(INTERNAL).pdf").exists()

    def test_custom_brackets_are_used_when_clearing(self, tmp_path: Path) -> None:
        """改括号字符集后，新括号包裹的标记也会被清除。"""
        _write_config(brackets={"left": " ({", "right": " )}"})
        f = tmp_path / "report{PUB}.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filelevel", ["set", str(f), "--level", "0"]) == 0
        assert (tmp_path / "report.pdf").exists()

    def test_broken_config_falls_back_to_defaults(self, tmp_path: Path) -> None:
        """配置文件损坏时回落默认值，set 仍可正常工作。"""
        config_path().parent.mkdir(parents=True, exist_ok=True)
        config_path().write_text("{not json", encoding="utf-8")
        f = tmp_path / "a.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filelevel", ["set", str(f), "--level", "2"]) == 0
        assert (tmp_path / "a(INT).pdf").exists()

    def test_invalid_levels_fall_back_with_warning(self) -> None:
        """等级缺级 / 非整数时整份回落默认值并给出提示。"""
        _write_config(levels={"0": "", "1": "PUB", "3": "CON"})
        config, warnings = load_filelevel_config()
        assert config.levels == default_config().levels
        assert any("等级不连续" in warning for warning in warnings)

    def test_missing_config_is_created_with_defaults(self) -> None:
        """第一次读取时文件不存在 → 按默认值生成；之后直接读它。"""
        assert not config_path().exists()
        config, warnings = load_filelevel_config()
        assert config == default_config()
        assert warnings == ()
        assert config_path().exists()
        raw = json.loads(config_path().read_text(encoding="utf-8"))
        assert raw["schema"] == "filelevel-config/1"
        assert raw["levels"] == default_config().levels
        assert raw["brackets"] == default_config().brackets
