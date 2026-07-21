"""screenshot 工具测试。

验证 ``fcmd.cli.screenshot`` 模块：
- 工具注册
- get_screenshot_path 路径生成
- take_screenshot_full / take_screenshot_area 跨平台截图
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

import fcmd as fx
import fcmd.cli.screenshot
from fcmd.apis.toolkit import _TOOL_REGISTRY
from fcmd.models import CommandResult


# ============================================================================ #
# 测试辅助
# ============================================================================ #
def _recording_run(calls: list[list[str]]) -> Any:
    """创建记录调用的 fake ``run_command`` 函数，返回成功结果。"""

    def run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
        calls.append(cmd)
        return CommandResult(cmd=list(cmd), returncode=0, stdout="", stderr="")

    return run


# ============================================================================ #
# 注册验证
# ============================================================================ #
class TestToolsRegistration:
    """screenshot 工具注册验证。"""

    def test_all_tools_registered(self) -> None:
        """screenshot 应在 _TOOL_REGISTRY 中注册。"""
        for name in ("screenshot",):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_screenshot_subcommands(self) -> None:
        """screenshot 应有 full/area 子命令。"""
        subs = fx.list_subcommands("screenshot")
        assert "full" in subs
        assert "area" in subs


# ============================================================================ #
# screenshot 测试
# ============================================================================ #
class TestScreenshot:
    """screenshot 工具测试。"""

    def test_get_screenshot_path_auto(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """自动生成带时间戳的文件名。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        path = fcmd.cli.screenshot.get_screenshot_path(None)
        assert path.parent == tmp_path / "Pictures" / "screenshots"
        assert path.name.startswith("screenshot_")
        assert path.suffix == ".png"

    def test_get_screenshot_path_named(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """指定文件名时使用指定名称。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        path = fcmd.cli.screenshot.get_screenshot_path("custom.png")
        assert path.name == "custom.png"

    def test_full_screenshot_windows(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Windows 全屏截图调用 PowerShell。"""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.screenshot.run_command", _recording_run(calls))

        fcmd.cli.screenshot.take_screenshot_full()
        captured = capsys.readouterr()
        assert "截图已保存" in captured.out
        assert any("powershell" in c[0] for c in calls)

    def test_area_screenshot_macos(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """macOS 区域截图调用 screencapture -i。"""
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.screenshot.run_command", _recording_run(calls))

        fcmd.cli.screenshot.take_screenshot_area()
        captured = capsys.readouterr()
        assert "截图已保存" in captured.out
        assert any("screencapture" in c and "-i" in c for c in calls)

    def test_full_screenshot_linux_gnome(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Linux 全屏截图优先 gnome-screenshot。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.screenshot.run_command", _recording_run(calls))

        fcmd.cli.screenshot.take_screenshot_full()
        # gnome-screenshot 成功时不调用 scrot
        assert any("gnome-screenshot" in c for c in calls)
        assert not any("scrot" in c for c in calls)

    def test_full_screenshot_linux_scrot_fallback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Linux gnome-screenshot 失败时回退 scrot。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        call_count = {"n": 0}

        def fake_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
            call_count["n"] += 1
            # 第一次调用 gnome-screenshot 失败，第二次 scrot 成功
            if call_count["n"] == 1:
                return CommandResult(cmd=list(cmd), returncode=1, stdout="", stderr="")
            return CommandResult(cmd=list(cmd), returncode=0, stdout="", stderr="")

        monkeypatch.setattr("fcmd.cli.screenshot.run_command", fake_run)

        fcmd.cli.screenshot.take_screenshot_full()
        assert call_count["n"] == 2  # gnome-screenshot + scrot

    def test_full_screenshot_macos(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """macOS 全屏截图调用 screencapture -x。"""
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.screenshot.run_command", _recording_run(calls))

        fcmd.cli.screenshot.take_screenshot_full()
        captured = capsys.readouterr()
        assert "截图已保存" in captured.out
        assert any("screencapture" in c and "-x" in c for c in calls)

    def test_area_screenshot_windows(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Windows 区域截图退化为全屏（PowerShell）。"""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.screenshot.run_command", _recording_run(calls))

        fcmd.cli.screenshot.take_screenshot_area()
        captured = capsys.readouterr()
        assert "截图已保存" in captured.out
        assert any("powershell" in c[0] for c in calls)

    def test_area_screenshot_linux(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Linux 区域截图调用 gnome-screenshot -a。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.screenshot.run_command", _recording_run(calls))

        fcmd.cli.screenshot.take_screenshot_area()
        captured = capsys.readouterr()
        assert "截图已保存" in captured.out
        assert any("gnome-screenshot" in c and "-a" in c for c in calls)
