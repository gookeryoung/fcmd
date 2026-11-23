"""离线测试骨架本身的自测：wslman 用例不得窥探或改动真实宿主机。

对应「wslmanw 相关测试经常跑实际的 WSL 环境（包括 IDE），改为模拟」：
本文件的用例证明 ``conftest.py`` 里的替身真的生效——否则同伴用例可能悄悄
依赖开发机装了什么 IDE / 发行版。真实进程只允许一种情况：程序压根不存在
（真 ``Popen`` 只会立刻报 ``FileNotFoundError``，用于断言「缺失程序要报错」）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fcmd.apps.wslman import _open, _terminal, wslman
from fcmd.shared.paths import fcmd_home
from tests.apps.wslman import _fakes


def test_host_path_lookups_are_offline() -> None:
    """宿主机 PATH / 安装目录一律「没装」：与开发机是否装了 code / trae 无关。"""
    import shutil

    assert shutil.which("code") is None
    assert shutil.which("wsl.exe") is None
    assert _terminal.find_wt() is None
    assert _open.detect_host("vscode").found is False
    assert _open.detect_host("opencode-windows").found is False
    assert _open.detect_host("qoder-cn").found is False


def test_detect_host_still_uses_explicit_inputs() -> None:
    """替身只拦「默认参数」：显式传 env / which 的探测逻辑仍按原样执行。"""
    found = _open.detect_host("vscode", env={}, which=lambda _name: r"C:\tools\code.cmd")
    assert found.found is True
    assert found.path == r"C:\tools\code.cmd"
    assert _open.detect_host("trae", env={}, which=lambda _name: None).found is False


def test_build_environment_is_a_fake_snapshot() -> None:
    """``cli._build_environment`` 返回假 runner + 空注册表，不读真实 ``Lxss``。"""
    env = wslman._build_environment()
    assert env.platform_ok is True
    assert env.registry.available is False
    assert env.capabilities.wsl_path == _fakes.WSL
    with pytest.raises(AssertionError, match="未预置的命令"):
        env.runner.run(["wsl.exe", "--status"])


def test_existing_programs_are_never_started(tmp_path: Path) -> None:
    """能真的跑起来的东西（WSL / 终端 / IDE）一律拦下，逼用例注入假实现。"""
    fake_ide = tmp_path / "Code.exe"
    fake_ide.write_bytes(b"")
    with pytest.raises(AssertionError, match="不应启动真实进程"):
        _terminal.spawn_gui([str(fake_ide)])
    with pytest.raises(AssertionError, match="不应启动真实进程"):
        _terminal.spawn_detached([str(fake_ide), "-d", "Ubuntu", "--exec", "/bin/true"])
    with pytest.raises(AssertionError, match="不应执行真实进程"):
        subprocess.run([str(fake_ide)], check=False)


def test_missing_program_still_reaches_popen(tmp_path: Path) -> None:
    """解析不到的程序放行：真 ``Popen`` 只会报错，启动器用例靠它断言错误文案。"""
    with pytest.raises(_terminal.LaunchError):
        _terminal.spawn_gui([str(tmp_path / "missing-ide.exe")])


def test_fcmd_home_points_at_temp_dir(tmp_path_factory: pytest.TempPathFactory) -> None:
    """设置文件不落到真实用户目录：``FCMD_HOME`` 在 pytest 临时目录下。"""
    assert Path(fcmd_home()).is_relative_to(Path(str(tmp_path_factory.getbasetemp())))
