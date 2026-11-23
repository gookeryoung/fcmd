"""wslman 测试包的共享入口（工厂函数见 ``_fakes.py``）。

**默认离线**：所有用例都不读宿主机的 PATH / 注册表 / 安装目录，也不启动真实进程
（``wsl.exe`` / ``wt.exe`` / IDE / 终端 / ``cmd.exe``）。发现真实宿主机状态的用例
（例如「智能检测补全内置条目」）必须自己 ``monkeypatch`` 掉这里的替身，再显式注入
假数据；这样测试结果与开发机装了什么 IDE、装了哪些发行版无关。

替身口径：

- ``shutil.which`` 一律返回 ``None``（``_runner.find_wsl`` / ``_terminal.find_wt`` /
  打开方式解析都据此判定「没装」）；
- ``LOCALAPPDATA`` / ``APPDATA`` / ``USERPROFILE`` / ``ProgramFiles`` 指向临时目录，
  ``_open.windows_candidates`` 扫不到本机 IDE；
- ``cli._build_environment`` 返回假 runner + 空注册表 + 空配置，不读真实 ``Lxss``；
- ``FCMD_HOME`` 指向临时目录，设置文件不落到真实用户目录；
- ``subprocess.run`` / ``Popen`` / ``os.startfile`` / ``QDesktopServices.openUrl``
  直接失败，测试要么注入假实现，要么就该被发现。
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.wslman import _open, _runner, _terminal, wslman
from fcmd.apps.wslman._config import WslmanConfig
from tests.apps.wslman import _fakes

_REAL_WHICH = shutil.which
_REAL_DETECT_HOST = _open.detect_host


def _exe_name(argv: Any) -> str:
    try:
        first = str(argv[0])
    except (TypeError, IndexError, KeyError):
        return ""
    return first.replace("/", "\\").rsplit("\\", 1)[-1].casefold()


def _is_wsl_call(argv: Any) -> bool:
    """判断 argv 是否真的会调用 ``wsl.exe``；测试里不允许落盘到本机 WSL。"""
    return _exe_name(argv) in {"wsl", "wsl.exe"}


def _launch_is_harmless(argv: Any) -> bool:
    """放行两种「真启动也不会有副作用」的情况。

    - 本进程的 Python 解释器：``SubprocessLauncher`` 自测要用；
    - 压根解析不到的程序：真 ``Popen`` 只会立刻报 ``FileNotFoundError``，
      这类用例断言的就是「缺失程序要报错」，不存在启动外部程序的风险。
    """
    try:
        first = str(argv[0])
    except (TypeError, IndexError, KeyError):
        return False
    if not first:
        return False
    if Path(first).name.casefold() == Path(sys.executable).name.casefold():
        return True
    return not Path(first).is_file() and _REAL_WHICH(first) is None


def _no_which(_name: str, *args: Any, **kwargs: Any) -> None:
    """替身：宿主机 PATH 里什么都找不到。"""
    return None


def _hermetic_detect_host(
    key: str,
    *,
    env: Mapping[str, str] | None = None,
    which: Any = _REAL_WHICH,
) -> _open.Detection:
    """``_open.detect_host`` 的默认参数版本：没显式传 ``which`` / ``env`` 就离线。"""
    if which is _REAL_WHICH:
        which = _no_which
    if env is None:
        env = {}
    return _REAL_DETECT_HOST(key, env=env, which=which)


def _fake_environment(_config_path: Any = None, *, runner: Any = None) -> wslman.Environment:
    """完全离线的环境快照：假 runner、空注册表、空配置。"""
    return wslman.Environment(
        runner=runner or _fakes.FakeRunner(),
        capabilities=_fakes.capabilities(),
        registry=_fakes.snapshot(available=False),
        config=WslmanConfig(path=_config_path),
        platform_ok=True,
    )


@pytest.fixture(autouse=True)
def _hermetic_host(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """把所有「问宿主环境」的入口换成本地替身（见模块文档）。"""
    empty = tmp_path_factory.mktemp("host")
    for name in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "ProgramFiles", "ProgramFiles(x86)"):
        monkeypatch.setenv(name, str(empty))
    monkeypatch.setenv("FCMD_HOME", str(empty / ".fcmd"))
    monkeypatch.setattr(shutil, "which", _no_which)
    monkeypatch.setattr(_open, "detect_host", _hermetic_detect_host)
    monkeypatch.setattr(wslman, "_build_environment", _fake_environment)


@pytest.fixture(autouse=True)
def _forbid_real_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试不得真的启动进程：未注入假实现时直接失败而不是动本机环境。"""

    real_run = _runner.subprocess.run
    real_popen = _terminal.subprocess.Popen

    def guarded_run(argv: Any, *args: Any, **kwargs: Any) -> Any:
        if _launch_is_harmless(argv):
            return real_run(argv, *args, **kwargs)
        raise AssertionError(f"测试不应执行真实进程：{argv!r}；请注入假 Runner")

    def guarded_popen(argv: Any, *args: Any, **kwargs: Any) -> Any:
        if _launch_is_harmless(argv):
            return real_popen(argv, *args, **kwargs)
        raise AssertionError(f"测试不应启动真实进程：{argv!r}；请注入假启动器")

    def guarded_startfile(path: Any, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"测试不应打开真实文件 / 程序：{path!r}")

    monkeypatch.setattr(_runner.subprocess, "run", guarded_run)
    monkeypatch.setattr(_runner.subprocess, "Popen", guarded_popen)
    monkeypatch.setattr(_terminal.subprocess, "Popen", guarded_popen)
    monkeypatch.setattr(os, "startfile", guarded_startfile, raising=False)
    try:
        from PySide6 import QtGui
    except ImportError:  # pragma: no cover - 未装 Qt 的环境
        return

    def guarded_open_url(url: Any, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"测试不应打开外部程序：{url!r}")

    monkeypatch.setattr(QtGui.QDesktopServices, "openUrl", guarded_open_url)
