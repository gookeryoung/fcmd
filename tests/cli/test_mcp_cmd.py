"""``fcmd mcp`` 内建命令：帮助、参数错误与 serve 委托。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fcmd.cli._builtins import mcp_cmd
from fcmd.core.exit_codes import ExitCode


def test_no_action_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    """不给动作时打印帮助并返回 USAGE。"""
    assert mcp_cmd.run([]) == int(ExitCode.USAGE)
    out = capsys.readouterr().out
    assert "MCP" in out
    assert "serve" in out


def test_unknown_action_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    """未知动作由 argparse 以非零码退出。"""
    with pytest.raises(SystemExit) as exc:
        mcp_cmd.run(["bogus"])
    assert exc.value.code != 0
    assert "invalid choice" in capsys.readouterr().err


def test_unknown_action_reaches_defensive_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """解析器意外返回未知动作时，走防御性 parser.error 分支。"""

    class _StubParser:
        def parse_args(self, argv: list[str]) -> SimpleNamespace:
            return SimpleNamespace(action="bogus")

        def error(self, message: str) -> None:
            raise SystemExit(2)

    monkeypatch.setattr(mcp_cmd, "_parser", _StubParser)
    with pytest.raises(SystemExit) as exc:
        mcp_cmd.run(["bogus"])
    assert exc.value.code == 2


def test_serve_delegates_and_returns_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """serve 委托 fcmd.mcp.server.run_mcp_stdio，正常返回 SUCCESS。"""
    from fcmd.mcp import server

    calls: list[str] = []
    monkeypatch.setattr(server, "run_mcp_stdio", lambda: calls.append("serve"))

    assert mcp_cmd.run(["serve"]) == int(ExitCode.SUCCESS)
    assert calls == ["serve"]


def test_serve_system_exit_int_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_mcp_stdio 以整数 SystemExit 退出时透传该码。"""
    from fcmd.mcp import server

    def _boom() -> None:
        raise SystemExit(3)

    monkeypatch.setattr(server, "run_mcp_stdio", _boom)
    assert mcp_cmd.run(["serve"]) == 3


def test_serve_system_exit_none_maps_to_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """SystemExit(None) 映射为 FAILURE。"""
    from fcmd.mcp import server

    def _boom() -> None:
        raise SystemExit(None)

    monkeypatch.setattr(server, "run_mcp_stdio", _boom)
    assert mcp_cmd.run(["serve"]) == int(ExitCode.FAILURE)


def test_serve_keyboard_interrupt_maps_to_interrupted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """KeyboardInterrupt 打印中断提示并返回 INTERRUPTED。"""
    from fcmd.mcp import server

    def _interrupt() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(server, "run_mcp_stdio", _interrupt)
    assert mcp_cmd.run(["serve"]) == int(ExitCode.INTERRUPTED)
    assert "已中断" in capsys.readouterr().err
