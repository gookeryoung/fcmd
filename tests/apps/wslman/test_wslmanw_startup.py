"""wslmanw 首屏导入面与构造期换算缓存（ADR 0057）。

导入链要真进程才准：同进程里别的用例早已把 ``_install`` / ``_wslui`` 拉起来，
``sys.modules`` 断言会永远成立，所以这里起一个只导入窗口模块的子进程。
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from fcmd.apps.wslman import wslmanw

_HEAVY_MODULES = (
    "fcmd.apps.wslman._install",
    "fcmd.apps.wslman._wslui",
    "fcmd.tools.archive.archivex",
)
"""首屏用不到、但曾经被顶层导入带起来的重模块。"""


def test_window_import_skips_install_and_wslui() -> None:
    """导入窗口模块（``wslmanw`` 第一跳）不连带安装 / 镜像源 / 压缩包解压。"""
    code = (
        "import sys\n"
        "import fcmd.apps.wslman._wslmanw_qt\n"
        f"heavy = {list(_HEAVY_MODULES)!r}\n"
        "print(','.join(name for name in heavy if name in sys.modules))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_local_arch_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """本机架构在进程内不会变：``platform.machine()`` 只问一次。"""
    calls: list[str] = []

    def fake_machine() -> str:
        calls.append("machine")
        return "ARM64"

    monkeypatch.setattr(wslmanw.platform, "machine", fake_machine)
    wslmanw.local_arch.cache_clear()
    try:
        assert wslmanw.local_arch() == "aarch64"
        assert wslmanw.local_arch() == "aarch64"
        assert calls == ["machine"]
    finally:
        wslmanw.local_arch.cache_clear()
