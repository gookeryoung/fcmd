"""setenv 工具测试（DSL 内建动作声明 commands/setenv.toml）。

验证 ``fcmd setenv`` 的 DSL action 迁移语义：
- 工具注册（单命令 DSL 工具，内置声明）
- 声明契约：``__dsl_action__`` 标记、message 契约、无 cmd（fn 任务形态）
- 执行语义：环境变量设置/覆盖/--default、完成消息插值
- dry-run 不执行、消息不打印
"""

from __future__ import annotations

import os

import pytest

import fcmd as fx
from fcmd.apis._tool_args import ToolSpec
from fcmd.apis.toolkit import _TOOL_REGISTRY, get_tool, run_tool
from fcmd.cli._discovery import ensure_tools_discovered

ensure_tools_discovered()  # 幂等：注册内置 DSL 命令（含 setenv）


# ---------------------------------------------------------------------- #
# 注册与声明验证
# ---------------------------------------------------------------------- #
class TestSetenvRegistration:
    """setenv 经内置 DSL（action 原语）注册。"""

    def test_registered_as_dsl_single_command(self) -> None:
        """setenv 注册为内置 DSL 单命令工具。"""
        assert "setenv" in _TOOL_REGISTRY
        assert fx.list_subcommands("setenv") == []

    def test_action_contract(self) -> None:
        """合成函数携带 __dsl_action__ 标记与 message 契约，cmd 为 None。"""
        spec: ToolSpec = get_tool("setenv")
        assert spec.cmd is None  # fn 任务形态（进程内动作，无子进程命令）
        assert getattr(spec.func, "__dsl_action__", None) == "setenv"
        assert getattr(spec.func, "__dsl_message__", None) == "环境变量 {name} 已设置"
        assert not getattr(spec.func, "__dsl_empty_body__", False)  # 有函数逻辑 → 引擎 fn 任务

    def test_signature_from_action_impl(self) -> None:
        """CLI 参数 schema 拷贝自动作实现签名：name/value positional + --default 开关。"""
        spec = get_tool("setenv")
        params = list(spec.func.__signature__.parameters)  # type: ignore[attr-defined]
        assert params == ["name", "value", "default"]
        assert (spec.param_help or {}).get("name") == "环境变量名"

    def test_uses_cwd_declaration(self) -> None:
        """声明契约：cwd 允许与 action 共存（fn 任务支持）。"""
        spec = get_tool("setenv")
        assert spec.cwd is None  # 内置声明未用 cwd，仅验证 ToolSpec 映射通路


# ---------------------------------------------------------------------- #
# 执行语义
# ---------------------------------------------------------------------- #
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
