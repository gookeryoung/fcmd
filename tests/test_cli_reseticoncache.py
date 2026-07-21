"""reseticoncache 工具测试。

验证 ``fcmd.cli.reseticoncache`` 模块：
- 工具注册
- reset_icon_cache_run 重置 Windows 图标缓存
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

import fcmd as fx
import fcmd.cli.reseticoncache
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
    """reseticoncache 工具注册验证。"""

    def test_all_tools_registered(self) -> None:
        """reseticoncache 应在 _TOOL_REGISTRY 中注册。"""
        for name in ("reseticoncache",):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_reseticoncache_single_command(self) -> None:
        """reseticoncache 是单命令工具。"""
        assert fx.list_subcommands("reseticoncache") == []


# ============================================================================ #
# reseticoncache 测试
# ============================================================================ #
class TestResetIconCache:
    """reseticoncache 工具测试。"""

    def test_non_windows_skip(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """非 Windows 平台打印提示并跳过。"""
        monkeypatch.setattr(sys, "platform", "linux")
        fcmd.cli.reseticoncache.reset_icon_cache_run()
        captured = capsys.readouterr()
        assert "仅在 Windows" in captured.out

    def test_windows_no_localappdata(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """Windows 但 LOCALAPPDATA 未设置时提示并跳过。"""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        fcmd.cli.reseticoncache.reset_icon_cache_run()
        captured = capsys.readouterr()
        assert "LOCALAPPDATA" in captured.out

    def test_windows_calls_commands(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Windows 下调用 taskkill/del/start 命令序列。"""
        monkeypatch.setattr(sys, "platform", "win32")
        local_appdata = tmp_path / "AppData" / "Local"
        local_appdata.mkdir(parents=True)
        (local_appdata / "IconCache.db").write_text("fake")
        explorer_dir = local_appdata / "Microsoft" / "Windows" / "Explorer"
        explorer_dir.mkdir(parents=True)
        monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.reseticoncache.run_command", _recording_run(calls))

        fcmd.cli.reseticoncache.reset_icon_cache_run()
        captured = capsys.readouterr()
        assert "图标缓存已重置" in captured.out
        # 应调用 taskkill、del（IconCache.db）、del（iconcache*）、start explorer
        assert any("taskkill" in c for c in calls)
        assert any("start" in c and "explorer.exe" in c for c in calls)
