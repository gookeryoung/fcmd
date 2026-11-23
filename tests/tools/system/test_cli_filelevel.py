"""filelevel 工具测试。

验证 ``fcmd filelevel``：
- 工具注册（set 子命令）
- 等级标记先清后加、仅移除括号包裹的标记、批量处理
- 无效等级 → 任务失败汇总 + 退出码 1
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.core.toolkit import list_subcommands, list_tools, run_tool
from fcmd.infras.fs import config_path, default_config


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """等级配置写到测试隔离目录，避免读 / 写开发机的 ~/.fcmd。"""
    home = tmp_path / "fcmd-home"
    monkeypatch.setenv("FCMD_HOME", str(home))
    return home


class TestRegistration:
    """filelevel 注册为多子命令工具。"""

    def test_registered(self) -> None:
        """filelevel 已注册且含 set 子命令。"""
        assert "filelevel" in list_tools()
        assert set(list_subcommands("filelevel")) == {"set", "config"}


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

    def test_config_shows_defaults_and_path(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """默认只展示：等级映射与配置文件路径都能看到。"""
        assert run_tool("filelevel", ["config"]) == 0
        out = capsys.readouterr().out
        assert str(config_path()) in out
        assert "1: PUB,NOR" in out
        assert "括号:" in out
        assert not config_path().exists()

    def test_config_init_writes_defaults(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--init 在配置缺失时落一份默认配置。"""
        assert run_tool("filelevel", ["config", "--init"]) == 0
        assert config_path().exists()
        assert "已写入默认配置" in capsys.readouterr().out
        # 再次 --init 不覆盖已有文件。
        assert run_tool("filelevel", ["config", "--init"]) == 0
        assert "已存在" in capsys.readouterr().out

    def test_config_level_override_changes_set_behavior(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """改等级映射后，下一次 set 立即用新标记。"""
        assert run_tool("filelevel", ["config", "--level", "1=PUBLIC"]) == 0
        f = tmp_path / "report.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filelevel", ["set", str(f), "--level", "1"]) == 0
        assert (tmp_path / "report(PUBLIC).pdf").exists()
        assert "已保存配置" in capsys.readouterr().out

    def test_config_brackets_override_changes_clearing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """改括号字符集后，新括号包裹的标记也会被清除。"""
        assert run_tool("filelevel", ["config", "--bracket-left", " ({", "--bracket-right", " )}"]) == 0
        f = tmp_path / "report{PUB}.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filelevel", ["set", str(f), "--level", "0"]) == 0
        assert (tmp_path / "report.pdf").exists()
        assert "已保存配置" in capsys.readouterr().out

    def test_config_reset_restores_defaults(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--reset 覆盖自定义等级，恢复内置默认值。"""
        assert run_tool("filelevel", ["config", "--level", "1=SECRET"]) == 0
        assert run_tool("filelevel", ["config", "--reset"]) == 0
        from fcmd.infras.jsonio import read_json_file

        raw = read_json_file(config_path())
        assert raw["levels"] == default_config().levels
        assert "已重置" in capsys.readouterr().out

    def test_config_rejects_unknown_level(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """只能修改已有等级，越界等级返回失败。"""
        assert run_tool("filelevel", ["config", "--level", "9=X"]) == 1
        assert "未定义" in capsys.readouterr().out

    def test_broken_config_falls_back_with_warning(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """配置文件损坏时回落默认值并给提示，不影响 set。"""
        config_path().parent.mkdir(parents=True, exist_ok=True)
        config_path().write_text("{not json", encoding="utf-8")
        assert run_tool("filelevel", ["config"]) == 0
        assert "内置默认值" in capsys.readouterr().out
        f = tmp_path / "a.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filelevel", ["set", str(f), "--level", "2"]) == 0
        assert (tmp_path / "a(INT).pdf").exists()

    def test_invalid_level_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """无效等级 → 任务失败汇总 + 退出码 1。"""
        f = tmp_path / "a.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filelevel", ["set", str(f), "--level", "5"]) == 1
        assert "失败" in capsys.readouterr().out
        assert f.exists()
