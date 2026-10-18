"""taskkill 工具测试（DSL 内建动作声明 commands/taskkill.toml）。

验证 ``fcmd taskkill`` 的 DSL action 迁移语义：
- 工具注册（单命令 DSL 工具，内置声明，别名 taskk）
- 声明契约：``__dsl_action__`` 标记、无 cmd（fn 任务形态）
- 执行语义（psutil 实现）：名称前缀匹配、terminate → wait_procs → kill、
  逐条目回显、未匹配提示后继续（退出码 0）
- 回归：不再通过子进程调用 ``taskkill``/``pkill``（避免 PATH 递归调用 fcmd 自身）
- dry-run 不执行
"""

from __future__ import annotations

from typing import Any

import psutil
import pytest

from fcmd.cli._discovery import _TOOL_ALIASES, ensure_tools_discovered
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.core.toolkit.args import ToolSpec

ensure_tools_discovered()  # 幂等：注册内置 DSL 命令（含 taskkill）


# ---------------------------------------------------------------------- #
# psutil 测试替身
# ---------------------------------------------------------------------- #
class _FakeProc:
    """``psutil.Process`` 替身：记录 terminate/kill 调用与抛出的异常。"""

    def __init__(
        self,
        pid: int,
        name: str,
        *,
        terminate_exc: BaseException | None = None,
        kill_exc: BaseException | None = None,
    ) -> None:
        self.pid = pid
        self.info: dict[str, Any] = {"pid": pid, "name": name}
        self.terminated = False
        self.killed = False
        self._terminate_exc = terminate_exc
        self._kill_exc = kill_exc

    def terminate(self) -> None:
        if self._terminate_exc is not None:
            raise self._terminate_exc
        self.terminated = True

    def kill(self) -> None:
        if self._kill_exc is not None:
            raise self._kill_exc
        self.killed = True


def _patch_psutil(
    monkeypatch: pytest.MonkeyPatch,
    procs: list[_FakeProc],
    *,
    alive: list[_FakeProc] | None = None,
) -> None:
    """替换 ``psutil.process_iter`` / ``psutil.wait_procs``。

    ``alive`` 为 ``wait_procs`` 超时后仍未退出的进程（默认全部已退出）。
    """
    survivors = list(alive or [])

    def fake_process_iter(attrs: Any = None, ad_value: Any = None) -> list[_FakeProc]:
        assert attrs == ["pid", "name"]
        return list(procs)

    def fake_wait_procs(
        targets: list[_FakeProc], timeout: float | None = None
    ) -> tuple[list[_FakeProc], list[_FakeProc]]:
        assert timeout == 3
        gone = [proc for proc in targets if proc not in survivors]
        return gone, survivors

    monkeypatch.setattr(psutil, "process_iter", fake_process_iter)
    monkeypatch.setattr(psutil, "wait_procs", fake_wait_procs)


# ---------------------------------------------------------------------- #
# 注册与声明验证
# ---------------------------------------------------------------------- #
class TestTaskkillRegistration:
    """taskkill 经内置 DSL（action 原语）注册。"""

    def test_registered_as_dsl_single_command(self) -> None:
        """taskkill 注册为内置 DSL 单命令工具。"""
        assert "taskkill" in _TOOL_REGISTRY
        assert set(_TOOL_REGISTRY["taskkill"]) == {None}

    def test_alias_taskk(self) -> None:
        """别名 taskk 指向 taskkill（pyproject 入口名保持不变）。"""
        assert _TOOL_ALIASES.get("taskk") == "taskkill"

    def test_action_contract(self) -> None:
        """合成函数携带 __dsl_action__ 标记，cmd 为 None（fn 任务形态）。"""
        spec: ToolSpec = _TOOL_REGISTRY["taskkill"][None]
        assert spec.cmd is None
        assert getattr(spec.func, "__dsl_action__", None) == "taskkill"
        assert not getattr(spec.func, "__dsl_empty_body__", False)

    def test_signature_from_action_impl(self) -> None:
        """CLI 参数 schema 拷贝自动作实现签名：names 为多值位置参数。"""
        spec = _TOOL_REGISTRY["taskkill"][None]
        params = list(spec.func.__signature__.parameters)  # type: ignore[attr-defined]
        assert params == ["names"]


# ---------------------------------------------------------------------- #
# 执行语义
# ---------------------------------------------------------------------- #
class TestTaskkillRun:
    """``fcmd taskkill`` 执行语义（psutil）。"""

    def test_terminates_matching_prefix(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """按名称前缀匹配：命中进程逐个 terminate，未命中的不触碰。"""
        hit1 = _FakeProc(101, "chrome.exe")
        hit2 = _FakeProc(102, "chrome.exe")
        other = _FakeProc(103, "python")
        _patch_psutil(monkeypatch, [hit1, hit2, other])

        assert run_tool("taskkill", ["chrome.exe"]) == 0

        assert hit1.terminated and hit2.terminated
        assert not other.terminated
        out = capsys.readouterr().out
        assert "终止进程: chrome.exe" in out
        assert "PID=101 名称=chrome.exe -> terminate" in out
        assert "PID=102 名称=chrome.exe -> terminate" in out

    def test_never_calls_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """回归：不再构造子进程调用，杜绝 PATH 递归调用 fcmd 自身 entry。"""

        def _boom(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("taskkill 不应发起子进程调用")

        _patch_psutil(monkeypatch, [_FakeProc(1, "node")])
        monkeypatch.setattr("subprocess.run", _boom)
        monkeypatch.setattr("subprocess.Popen", _boom)
        assert run_tool("taskkill", ["node"]) == 0

    def test_no_match_continues(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """未匹配任何进程时打印提示后继续，退出码 0。"""
        _patch_psutil(monkeypatch, [])
        assert run_tool("taskkill", ["nonexistent_proc"]) == 0
        out = capsys.readouterr().out
        assert "未找到匹配进程: nonexistent_proc" in out

    def test_multiple_names(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """批量终止：逐个名称查询并回显。"""
        _patch_psutil(monkeypatch, [_FakeProc(1, "chrome.exe"), _FakeProc(2, "python")])
        assert run_tool("taskkill", ["chrome.exe", "python"]) == 0
        out = capsys.readouterr().out
        assert "终止进程: chrome.exe" in out
        assert "终止进程: python" in out

    def test_alive_after_timeout_is_killed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """等待 3 秒仍未退出的进程转入 kill。"""
        stubborn = _FakeProc(200, "svchost")
        _patch_psutil(monkeypatch, [stubborn], alive=[stubborn])
        assert run_tool("taskkill", ["svchost"]) == 0
        assert stubborn.terminated
        assert stubborn.killed
        assert "PID=200 已强制终止" in capsys.readouterr().out

    def test_terminate_no_such_process(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """terminate 时进程已退出（NoSuchProcess）不视为失败。"""
        gone = _FakeProc(300, "chrome.exe", terminate_exc=psutil.NoSuchProcess(300))
        _patch_psutil(monkeypatch, [gone])
        assert run_tool("taskkill", ["chrome.exe"]) == 0
        assert "PID=300 已退出" in capsys.readouterr().out

    def test_terminate_access_denied(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """terminate 权限不足时提示并跳过。"""
        guarded = _FakeProc(400, "chrome.exe", terminate_exc=psutil.AccessDenied(400))
        _patch_psutil(monkeypatch, [guarded])
        assert run_tool("taskkill", ["chrome.exe"]) == 0
        assert "PID=400 权限不足，跳过" in capsys.readouterr().out

    def test_kill_access_denied(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """kill 权限不足时提示无法强制终止。"""
        guarded = _FakeProc(500, "svchost", kill_exc=psutil.AccessDenied(500))
        _patch_psutil(monkeypatch, [guarded], alive=[guarded])
        assert run_tool("taskkill", ["svchost"]) == 0
        assert "PID=500 权限不足，无法强制终止" in capsys.readouterr().out

    def test_kill_no_such_process_silent(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """kill 前进程已退出（NoSuchProcess）静默跳过，不打印强制终止。"""
        vanished = _FakeProc(600, "svchost", kill_exc=psutil.NoSuchProcess(600))
        _patch_psutil(monkeypatch, [vanished], alive=[vanished])
        assert run_tool("taskkill", ["svchost"]) == 0
        assert "已强制终止" not in capsys.readouterr().out

    def test_dry_run_skips_execution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--dry-run 打印计划不执行：不查询进程。"""

        def _boom(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("dry-run 不应查询进程列表")

        monkeypatch.setattr(psutil, "process_iter", _boom)
        assert run_tool("taskkill", ["chrome.exe", "--dry-run"]) == 0
