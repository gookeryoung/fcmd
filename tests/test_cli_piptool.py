"""piptool 工具测试。

验证 ``fcmd.cli.dev.piptool`` 模块（u/r，含通配符展开与受保护包过滤）与
DSL 子命令 i/up/d/f（``src/fcmd/commands/piptool.toml``，逐子命令合并注册）：
- 工具注册
- 辅助函数
- 命令构造
- CLI 调度
"""

from __future__ import annotations

from typing import Any

import pytest

import fcmd as fx
import fcmd.cli.dev.piptool
from fcmd.apis._tool_exec import _build_task_spec
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import ensure_tools_discovered
from fcmd.cli.dev.piptool import (
    _expand_wildcard_packages,
    _filter_protected_packages,
    _get_installed_packages,
    pip_reinstall,
    pip_uninstall,
)
from fcmd.models import CommandResult

ensure_tools_discovered()


# ============================================================================ #
# 测试辅助：创建 fake run_command 函数（避免 lambda ARG005）
# ============================================================================ #
def _fake_run(result: CommandResult) -> Any:
    """创建总是返回 ``result`` 的 fake ``run_command`` 函数。"""

    def run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
        return result

    return run


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
    """piptool 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """piptool 应在 _TOOL_REGISTRY 中注册。"""
        for name in ("piptool",):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_piptool_subcommands(self) -> None:
        """piptool 应有 i/u/r/d/up/f 子命令。"""
        subs = fx.list_subcommands("piptool")
        for name in ("i", "u", "r", "d", "up", "f"):
            assert name in subs, f"子命令 {name!r} 未注册"


# ============================================================================ #
# piptool 测试
# ============================================================================ #
class TestPiptoolHelpers:
    """piptool 辅助函数测试。"""

    def test_filter_protected_packages_keeps_safe(self) -> None:
        """_filter_protected_packages 保留非保护包。"""
        result = _filter_protected_packages(["requests", "flask"])
        assert result == ["requests", "flask"]

    def test_filter_protected_packages_removes_protected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """_filter_protected_packages 过滤受保护包并打印提示。"""
        result = _filter_protected_packages(["requests", "fcmd", "flask"])
        assert "fcmd" not in result
        assert "requests" in result
        assert "flask" in result
        out = capsys.readouterr().out
        assert "fcmd" in out

    def test_filter_protected_packages_case_insensitive(self, capsys: pytest.CaptureFixture[str]) -> None:
        """_filter_protected_packages 大小写不敏感。"""
        result = _filter_protected_packages(["FCMD", "Requests"])
        assert "FCMD" not in result
        assert "Requests" in result

    def test_get_installed_packages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_get_installed_packages 解析 pip list 输出。"""
        fake_result = CommandResult(
            cmd=["pip", "list"],
            returncode=0,
            stdout="requests==2.31.0\nflask==3.0.0\n",
            stderr="",
        )
        monkeypatch.setattr("fcmd.cli.dev.piptool.run_command", _fake_run(fake_result))
        result = _get_installed_packages()
        assert "requests" in result
        assert "flask" in result

    def test_get_installed_packages_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_get_installed_packages 空输出返回空列表。"""
        fake_result = CommandResult(cmd=["pip", "list"], returncode=0, stdout="", stderr="")
        monkeypatch.setattr("fcmd.cli.dev.piptool.run_command", _fake_run(fake_result))
        assert _get_installed_packages() == []

    def test_expand_wildcard_no_pattern(self) -> None:
        """_expand_wildcard_packages 无通配符时返回原列表。"""
        assert _expand_wildcard_packages("requests") == ["requests"]

    def test_expand_wildcard_with_pattern(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_expand_wildcard_packages 展开通配符。"""
        monkeypatch.setattr(
            "fcmd.cli.dev.piptool._get_installed_packages",
            lambda: ["requests", "flask", "django"],
        )
        result = _expand_wildcard_packages("f*")
        assert "flask" in result
        assert "requests" not in result


class TestPiptoolCommands:
    """piptool CLI 子命令测试。"""

    def test_pip_uninstall_protected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """pip_uninstall 跳过受保护包。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.dev.piptool.run_command",
            _recording_run(calls),
        )
        monkeypatch.setattr("fcmd.cli.dev.piptool._expand_wildcard_packages", lambda p: [p])
        pip_uninstall(["fcmd"])
        # 受保护包应跳过，不调用 pip uninstall
        assert not any("uninstall" in " ".join(c) for c in calls)
        out = capsys.readouterr().out
        assert "受保护" in out

    def test_pip_uninstall_normal(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pip_uninstall 正常卸载。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.dev.piptool.run_command",
            _recording_run(calls),
        )
        monkeypatch.setattr("fcmd.cli.dev.piptool._expand_wildcard_packages", lambda p: [p])
        pip_uninstall(["requests"])
        assert calls[0] == ["pip", "uninstall", "-y", "requests"]

    def test_pip_reinstall_all_protected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """pip_reinstall 全是受保护包时跳过。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.dev.piptool.run_command",
            _recording_run(calls),
        )
        pip_reinstall(["fcmd"])
        assert not calls
        out = capsys.readouterr().out
        assert "受保护" in out

    def test_pip_reinstall_normal(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pip_reinstall 正常重装。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.dev.piptool.run_command",
            _recording_run(calls),
        )
        pip_reinstall(["requests"])
        assert calls[0] == ["pip", "uninstall", "-y", "requests"]
        assert calls[1] == ["pip", "install", "requests"]

    def test_pip_reinstall_offline(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pip_reinstall 离线模式添加 --no-index。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.dev.piptool.run_command",
            _recording_run(calls),
        )
        pip_reinstall(["requests"], offline=True)
        assert calls[1] == [
            "pip",
            "install",
            "--no-index",
            "--find-links",
            ".",
            "requests",
        ]


class TestPiptoolDslSubcommands:
    """piptool DSL 子命令（i/up/d/f）与合并注册测试。"""

    def test_pip_i_list_expansion(self) -> None:
        """DSL 子命令 i：list 参数独占占位符按元素展开。"""
        spec = _TOOL_REGISTRY["piptool"]["i"]
        task = _build_task_spec(spec, {"packages": ["requests", "flask"]})
        assert task.cmd == ["pip", "install", "requests", "flask"]

    def test_pip_up_cmd(self) -> None:
        """DSL 子命令 up：零参 cmd。"""
        spec = _TOOL_REGISTRY["piptool"]["up"]
        task = _build_task_spec(spec, {})
        assert task.cmd == ["python", "-m", "pip", "install", "--upgrade", "pip"]

    def test_pip_d_on_token_expansion(self) -> None:
        """DSL 子命令 d：list 独占占位符展开 + bool on-token 追加。"""
        spec = _TOOL_REGISTRY["piptool"]["d"]
        task = _build_task_spec(spec, {"packages": ["requests"], "offline": True})
        assert task.cmd == ["pip", "download", "requests", "-d", "packages", "--no-index", "--find-links", "."]

    def test_pip_d_default_online(self) -> None:
        """DSL 子命令 d：offline 为假时不追加 on-token。"""
        spec = _TOOL_REGISTRY["piptool"]["d"]
        task = _build_task_spec(spec, {"packages": ["requests"], "offline": False})
        assert task.cmd == ["pip", "download", "requests", "-d", "packages"]

    def test_pip_f_str_cmd(self) -> None:
        """DSL 子命令 f：str cmd 走 shell 重定向，原样透传。"""
        spec = _TOOL_REGISTRY["piptool"]["f"]
        task = _build_task_spec(spec, {})
        assert task.cmd == "pip freeze --exclude-editable > requirements.txt"

    def test_merged_subcommands_visible(self) -> None:
        """合并注册后 Python 子命令（u/r）与 DSL 子命令（i/up/d/f）全部可见。"""
        subs = fx.list_subcommands("piptool")
        assert {"i", "u", "r", "f", "up", "d"} <= set(subs)


class TestPiptoolRunTool:
    """piptool 通过 run_tool 集成测试（DSL 子命令经引擎执行）。"""

    def test_pip_i_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fcmd piptool i <packages> 通过 run_tool 调用。"""
        captured: list[Any] = []

        def fake_run(cmd: Any, **kwargs: Any) -> Any:
            captured.append(cmd)
            return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", fake_run)
        code = run_tool("piptool", ["i", "requests"])
        assert code == 0
        assert captured[0] == ["pip", "install", "requests"]

    def test_pip_up_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fcmd piptool up 通过 run_tool 调用。"""
        captured: list[Any] = []

        def fake_run(cmd: Any, **kwargs: Any) -> Any:
            captured.append(cmd)
            return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", fake_run)
        code = run_tool("piptool", ["up"])
        assert code == 0
        assert captured[0] == ["python", "-m", "pip", "install", "--upgrade", "pip"]

    def test_pip_f_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd piptool f 通过 run_tool 执行 shell 重定向并打印完成消息。"""
        captured: list[Any] = []

        def fake_run(cmd: Any, **kwargs: Any) -> Any:
            captured.append(cmd)
            return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", fake_run)
        code = run_tool("piptool", ["f"])
        assert code == 0
        assert captured[0] == "pip freeze --exclude-editable > requirements.txt"
        out = capsys.readouterr().out
        assert "依赖已导出到 requirements.txt" in out
