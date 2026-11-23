"""wslman 测试包的共享入口（工厂函数见 ``_fakes.py``）。"""

from __future__ import annotations

from typing import Any

import pytest

from fcmd.apps.wslman import _runner, _terminal


def _is_wsl_call(argv: Any) -> bool:
    """判断 argv 是否真的会调用 ``wsl.exe``；测试里不允许落盘到本机 WSL。"""
    try:
        first = str(argv[0])
    except (TypeError, IndexError, KeyError):
        return False
    name = first.replace("/", "\\").rsplit("\\", 1)[-1].casefold()
    return name in {"wsl", "wsl.exe"}


@pytest.fixture(autouse=True)
def _forbid_real_wsl(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试不得真的执行 ``wsl.exe``：未注入假 runner 时直接失败而不是动本机实例。"""
    real_run = _runner.subprocess.run
    real_popen = _terminal.subprocess.Popen

    def guarded_run(argv: Any, *args: Any, **kwargs: Any) -> Any:
        if _is_wsl_call(argv):
            raise AssertionError(f"测试不应调用真实 wsl.exe：{argv!r}")
        return real_run(argv, *args, **kwargs)

    def guarded_popen(argv: Any, *args: Any, **kwargs: Any) -> Any:
        if _is_wsl_call(argv):
            raise AssertionError(f"测试不应启动真实 wsl.exe：{argv!r}")
        return real_popen(argv, *args, **kwargs)

    monkeypatch.setattr(_runner.subprocess, "run", guarded_run)
    monkeypatch.setattr(_terminal.subprocess, "Popen", guarded_popen)
