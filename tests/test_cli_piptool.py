"""piptool 工具测试（DSL 声明 commands/piptool.toml）。

验证 ``fcmd piptool`` 的 compute 数据流迁移语义：
- 工具注册（多子命令 DSL 工具，别名 pipt）
- 声明契约：u/r 的 ``__dsl_compute__`` 标记、r 隐藏链 _uninstall → _install
- 执行语义：通配符展开 + 受保护包过滤注入 exec 参数、空结果 SKIPPED、
  compute 同名链上只计算一次
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from fcmd.apis._tool_args import ToolSpec
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import _TOOL_ALIASES, ensure_tools_discovered

ensure_tools_discovered()  # 幂等：注册内置 DSL 命令（含 piptool）


# ---------------------------------------------------------------------- #
# 注册与声明验证
# ---------------------------------------------------------------------- #
class TestPiptoolRegistration:
    """piptool 经内置 DSL（compute 数据流）注册。"""

    def test_registered_as_dsl_tool(self) -> None:
        """piptool 注册为内置 DSL 多子命令工具（含隐藏链）。"""
        assert set(_TOOL_REGISTRY["piptool"]) == {"i", "up", "d", "f", "u", "r", "_uninstall", "_install"}

    def test_alias_pipt(self) -> None:
        """工具级别名 pipt 指向 piptool（历史 pip 入口名）。"""
        assert _TOOL_ALIASES.get("pipt") == "piptool"

    def test_u_compute_contract(self) -> None:
        """u 声明 compute（pip_expand → expanded），cmd 引用注入变量。"""
        spec: ToolSpec = _TOOL_REGISTRY["piptool"]["u"]
        assert spec.cmd == ("pip", "uninstall", "-y", "{expanded}")
        assert getattr(spec.func, "__dsl_compute__", None) == ("pip_expand", "expanded")
        assert list(spec.func.__signature__.parameters) == ["packages"]  # type: ignore[attr-defined]

    def test_r_chain_contracts(self) -> None:
        """r 为聚合入口（无 cmd），隐藏链 _uninstall → _install 保证先卸后装。"""
        r_spec = _TOOL_REGISTRY["piptool"]["r"]
        assert r_spec.cmd is None
        assert r_spec.needs == ("_install",)
        uninstall_spec = _TOOL_REGISTRY["piptool"]["_uninstall"]
        install_spec = _TOOL_REGISTRY["piptool"]["_install"]
        assert uninstall_spec.cmd == ("pip", "uninstall", "-y", "{safe}")
        assert uninstall_spec.hidden
        assert getattr(uninstall_spec.func, "__dsl_compute__", None) == ("pip_filter", "safe")
        assert install_spec.cmd == ("pip", "install", "{safe}")
        assert install_spec.needs == ("_uninstall",)
        assert getattr(install_spec.func, "__dsl_compute__", None) == ("pip_filter", "safe")

    def test_r_offline_on_tokens(self) -> None:
        """r 的 offline 参数经 _install 的 on 契约追加固定 token。"""
        install_spec = _TOOL_REGISTRY["piptool"]["_install"]
        on_map = getattr(install_spec.func, "__dsl_param_on__", {})
        assert on_map == {"offline": ("--no-index", "--find-links", ".")}


# ---------------------------------------------------------------------- #
# 执行语义
# ---------------------------------------------------------------------- #
class TestPiptoolRun:
    """``fcmd piptool`` 执行语义（mock 引擎 subprocess 与 pip 采集）。"""

    @staticmethod
    def _fake_run(captured: list[list[str]]) -> Any:
        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        return fake_run

    def test_u_concrete_package(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """u 具体包名：无通配符不触发 pip list，直接注入 uninstall 参数。"""
        captured: list[list[str]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", self._fake_run(captured))
        assert run_tool("piptool", ["u", "requests"]) == 0
        assert captured == [["pip", "uninstall", "-y", "requests"]]

    def test_u_wildcard_expand_and_protect(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """u 通配符展开 + 受保护包过滤（模式命中 fcmd 时剔除并提示）。"""
        monkeypatch.setattr(
            "fcmd.dsl.actions._pip_installed_packages",
            lambda: ["requests", "requests_toolbelt", "fcmd", "click"],
        )
        captured: list[list[str]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", self._fake_run(captured))
        assert run_tool("piptool", ["u", "requests*", "fc*"]) == 0
        assert captured == [["pip", "uninstall", "-y", "requests", "requests_toolbelt"]]
        assert "跳过受保护的包: fcmd" in capsys.readouterr().out

    def test_u_wildcard_single_capture(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """u 多个通配符模式共享一次 pip list 采集。"""
        calls: list[int] = []
        monkeypatch.setattr(
            "fcmd.dsl.actions._pip_installed_packages",
            lambda: calls.append(1) or ["requests", "click"],  # type: ignore[func-returns-value]
        )
        captured: list[list[str]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", self._fake_run(captured))
        assert run_tool("piptool", ["u", "requests*", "click"]) == 0
        assert len(calls) == 1
        assert captured == [["pip", "uninstall", "-y", "requests", "click"]]

    def test_u_no_match_skipped(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """u 通配符无匹配：空结果 → SKIPPED，退出码 0，不执行命令。"""
        monkeypatch.setattr("fcmd.dsl.actions._pip_installed_packages", lambda: ["click"])
        captured: list[list[str]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", self._fake_run(captured))
        assert run_tool("piptool", ["u", "nomatch*"]) == 0
        assert captured == []

    def test_u_protected_only_skipped(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """u 仅受保护包：过滤后空结果 → SKIPPED，退出码 0。"""
        captured: list[list[str]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", self._fake_run(captured))
        assert run_tool("piptool", ["u", "fcmd"]) == 0
        assert captured == []
        assert "跳过受保护的包: fcmd" in capsys.readouterr().out

    def test_r_uninstall_then_install(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """r 端到端：过滤后先卸载后安装，--offline 追加 on token。"""
        captured: list[list[str]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", self._fake_run(captured))
        assert run_tool("piptool", ["r", "requests", "--offline"]) == 0
        assert captured == [
            ["pip", "uninstall", "-y", "requests"],
            ["pip", "install", "requests", "--no-index", "--find-links", "."],
        ]

    def test_r_compute_runs_once(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """r 隐藏链两处声明同一 compute（as=safe），链上只计算一次（去重）。"""
        from fcmd.dsl.actions import _filter_protected

        calls: list[int] = []

        def counting_filter(packages: list[str]) -> list[str]:
            calls.append(1)
            return _filter_protected(packages)

        monkeypatch.setattr("fcmd.dsl.actions._filter_protected", counting_filter)
        captured: list[list[str]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", self._fake_run(captured))
        assert run_tool("piptool", ["r", "requests"]) == 0
        assert len(calls) == 1
        assert captured == [
            ["pip", "uninstall", "-y", "requests"],
            ["pip", "install", "requests"],
        ]

    def test_r_all_protected_skipped(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """r 全受保护包：空结果 → 链上任务 SKIPPED，退出码 0。"""
        captured: list[list[str]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", self._fake_run(captured))
        assert run_tool("piptool", ["r", "fcmd"]) == 0
        assert captured == []
        assert "跳过受保护的包: fcmd" in capsys.readouterr().out

    def test_alias_entry_resolves(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """别名 pipt 经 resolve_tool 解析后可正常执行。"""
        from fcmd.cli._discovery import resolve_tool

        captured: list[list[str]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", self._fake_run(captured))
        assert run_tool(resolve_tool("pipt"), ["up"]) == 0  # type: ignore[arg-type]
        assert captured == [["python", "-m", "pip", "install", "--upgrade", "pip"]]
