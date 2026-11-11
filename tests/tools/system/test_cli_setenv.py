"""setenv 工具测试。

验证 ``fcmd setenv``：
- 工具注册（单命令）
- 环境变量设置/覆盖/--default、完成消息插值
- dry-run 不执行、消息不打印
"""

from __future__ import annotations

import os

import pytest

from fcmd.core.toolkit import list_subcommands, list_tools, run_tool


class TestRegistration:
    """setenv 注册为单命令工具。"""

    def test_registered(self) -> None:
        """setenv 已注册且无子命令。"""
        assert "setenv" in list_tools()
        assert list_subcommands("setenv") == []


class TestSetenvRun:
    """``fcmd setenv`` 执行语义。"""

    def test_run_sets_env(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd setenv NAME value 设置环境变量并打印插值完成消息。"""
        monkeypatch.delenv("FCMD_TEST_SETENV_RUN", raising=False)
        code = run_tool("setenv", ["FCMD_TEST_SETENV_RUN", "run_val"])
        assert code == 0
        assert os.environ["FCMD_TEST_SETENV_RUN"] == "run_val"
        assert "环境变量 FCMD_TEST_SETENV_RUN 已设置" in capsys.readouterr().out

    def test_run_overwrites_existing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """默认覆盖已有值。"""
        monkeypatch.setenv("FCMD_TEST_SETENV_RUN", "old")
        assert run_tool("setenv", ["FCMD_TEST_SETENV_RUN", "new"]) == 0
        assert os.environ["FCMD_TEST_SETENV_RUN"] == "new"

    def test_run_default_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--default 不覆盖已有值。"""
        monkeypatch.setenv("FCMD_TEST_SETENV_RUN", "old")
        assert run_tool("setenv", ["FCMD_TEST_SETENV_RUN", "new", "--default"]) == 0
        assert os.environ["FCMD_TEST_SETENV_RUN"] == "old"

    def test_dry_run_skips_execution(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """--dry-run 打印计划不执行：环境变量不变、完成消息不打印。"""
        monkeypatch.delenv("FCMD_TEST_SETENV_RUN", raising=False)
        assert run_tool("setenv", ["FCMD_TEST_SETENV_RUN", "v", "--dry-run"]) == 0
        assert "FCMD_TEST_SETENV_RUN" not in os.environ
        assert "已设置" not in capsys.readouterr().out
