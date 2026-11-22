"""关于对话框运行时版本行测试：fcmd / Python / PySide 三行。"""

from __future__ import annotations

import platform

from fcmd import __version__
from fcmd.gui.window import about_version_text


def test_about_version_text_lists_runtime_versions() -> None:
    """三行依次为 fcmd 版本、Python 版本与实际 Qt 绑定版本。"""
    lines = about_version_text().splitlines()
    assert lines[0] == f"fcmd {__version__}"
    assert lines[1] == f"Python 版本：{platform.python_version()}"
    assert lines[2].startswith("PySide 版本：")
    assert lines[2].split("：", 1)[1].split(" ", 1)[0] in {"PySide2", "PySide6"}
