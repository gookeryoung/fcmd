"""_env_persist 环境变量持久化辅助测试。

验证 ``fcmd.cli._env_persist`` 模块：
- persist_env 更新当前进程 os.environ
- Windows 分支写注册表 + 广播 WM_SETTINGCHANGE
- Linux/macOS 分支写入/更新 ~/.profile 的 export 行
- shell_profile_path 返回 ~/.profile
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

import fcmd.cli._env_persist as ep


class TestShellProfilePath:
    """shell_profile_path 测试。"""

    def test_returns_home_profile(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """返回 ~/.profile。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert ep.shell_profile_path() == tmp_path / ".profile"


class TestPersistEnvUnix:
    """Linux/macOS 持久化到 ~/.profile 测试。"""

    def test_appends_new_var(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """写入新环境变量：追加 export 行并更新 os.environ。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("MY_VAR", raising=False)

        ep.persist_env("MY_VAR", "hello")

        assert os.environ["MY_VAR"] == "hello"
        content = (tmp_path / ".profile").read_text(encoding="utf-8")
        assert 'export MY_VAR="hello"' in content

    def test_updates_existing_var(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """已存在同名 export 行时替换而非重复追加。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        profile = tmp_path / ".profile"
        profile.write_text('export MY_VAR="old"\nexport OTHER="keep"\n', encoding="utf-8")

        ep.persist_env("MY_VAR", "new")

        content = profile.read_text(encoding="utf-8")
        assert 'export MY_VAR="new"' in content
        assert 'export MY_VAR="old"' not in content
        assert 'export OTHER="keep"' in content
        # 未重复追加：仅一行 MY_VAR
        assert content.count("MY_VAR") == 1

    def test_appends_without_trailing_newline(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """已有内容无末尾换行时先补换行再追加。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        profile = tmp_path / ".profile"
        profile.write_text("export EXISTING=1", encoding="utf-8")

        ep.persist_env("NEW_VAR", "v")

        content = profile.read_text(encoding="utf-8")
        assert content == 'export EXISTING=1\nexport NEW_VAR="v"\n'

    def test_reads_unreadable_profile(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """读取失败时视为空内容，仍能写入。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        def fake_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
            raise OSError("cannot read")

        monkeypatch.setattr(Path, "read_text", fake_read_text)
        written: dict[str, str] = {}

        def fake_write_text(self: Path, data: str, *args: Any, **kwargs: Any) -> int:
            written["data"] = data
            return len(data)

        monkeypatch.setattr(Path, "write_text", fake_write_text)

        ep.persist_env("V", "1")
        assert 'export V="1"' in written["data"]


class TestPersistEnvWindows:
    """Windows 持久化到注册表测试（仅 Windows 运行，winreg 平台限定）。"""

    @pytest.mark.skipif(sys.platform != "win32", reason="winreg 仅 Windows 可用")
    def test_writes_registry_and_broadcasts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """写注册表 REG_EXPAND_SZ 并广播 WM_SETTINGCHANGE，同时更新 os.environ。"""
        import ctypes
        import winreg

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("WIN_VAR", raising=False)

        set_calls: list[tuple[str, Any, int]] = []

        class _FakeKey:
            def __enter__(self) -> _FakeKey:
                return self

            def __exit__(self, *exc: Any) -> None:
                return None

        def fake_open_key(root: int, sub: str, res: int, access: int) -> _FakeKey:
            assert root == winreg.HKEY_CURRENT_USER
            assert sub == "Environment"
            return _FakeKey()

        def fake_set_value(key: Any, name: str, res: int, typ: int, value: Any) -> None:
            set_calls.append((name, value, typ))

        broadcasts: list[Any] = []

        class _FakeUser32:
            def SendMessageTimeoutW(self, *args: Any) -> int:
                broadcasts.append(args)
                return 1

        class _FakeWindll:
            user32 = _FakeUser32()

        monkeypatch.setattr(winreg, "OpenKey", fake_open_key)
        monkeypatch.setattr(winreg, "SetValueEx", fake_set_value)
        monkeypatch.setattr(ctypes, "windll", _FakeWindll(), raising=False)

        ep.persist_env("WIN_VAR", "C:/tools")

        assert os.environ["WIN_VAR"] == "C:/tools"
        assert set_calls == [("WIN_VAR", "C:/tools", winreg.REG_EXPAND_SZ)]
        assert len(broadcasts) == 1
