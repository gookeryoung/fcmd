"""llmengine 独立命令测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.llm import llmengine
from fcmd.tools.llm.llmbase import Check, EngineInfo


def test_tool_registered() -> None:
    assert set(_TOOL_REGISTRY["llmengine"]) == {"list", "install", "use", "check"}


def test_list_without_engine(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmengine, "list_engines", lambda: [])
    llmengine.list_cmd()
    assert "未安装引擎" in capsys.readouterr().out


def test_list_marks_current(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        llmengine,
        "list_engines",
        lambda: [EngineInfo("b1", Path("llama-server.exe"), "current")],
    )
    llmengine.list_cmd()
    out = capsys.readouterr().out
    assert "b1" in out
    assert "llama-server.exe" in out


def test_install_sets_first_engine_as_current(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    used: list[str] = []

    def _install(tag: str, **kwargs: Any) -> EngineInfo:
        return EngineInfo(tag, Path("llama-server.exe"), "release")

    monkeypatch.setattr(llmengine, "install_engine", _install)
    monkeypatch.setattr(llmengine, "list_engines", lambda: [])
    monkeypatch.setattr(llmengine, "use_engine", lambda tag: used.append(tag) or EngineInfo(tag, Path("x")))
    llmengine.install_cmd("b1")
    assert used == ["b1"]
    assert "引擎就绪" in capsys.readouterr().out


def test_install_keeps_existing_current(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    used: list[str] = []

    def _install(tag: str, **kwargs: Any) -> EngineInfo:
        return EngineInfo(tag, Path("llama-server.exe"), "release")

    monkeypatch.setattr(llmengine, "install_engine", _install)
    monkeypatch.setattr(
        llmengine,
        "list_engines",
        lambda: [EngineInfo("b0", Path("old.exe"), "current")],
    )
    monkeypatch.setattr(llmengine, "use_engine", used.append)
    llmengine.install_cmd("b1")
    assert used == []
    assert "引擎就绪" in capsys.readouterr().out


def test_use_switches_default(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmengine, "use_engine", lambda tag: EngineInfo(tag, Path("llama-server.exe")))
    llmengine.use_cmd("b2")
    assert "默认引擎" in capsys.readouterr().out


def test_check_reports_result(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmengine, "resolve_engine", lambda tag=None: EngineInfo("b1", Path("llama-server.exe")))
    monkeypatch.setattr(
        llmengine,
        "check_engine",
        lambda engine: Check("推理引擎", "ok", "version ok"),
    )
    llmengine.check_cmd()
    assert "version ok" in capsys.readouterr().out
