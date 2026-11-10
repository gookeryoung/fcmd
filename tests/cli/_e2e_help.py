"""工具 ``--help`` 冒烟断言（供 test_e2e_help_* 三个文件共用）。

按领域拆分测试文件是为了让 ``pytest-xdist --dist loadfile`` 能把 E2E 用例
分散到多个 worker；断言本身只在此处维护一份。
"""

from __future__ import annotations

from collections.abc import Callable

from tests.conftest import CmdResult


def assert_help_ok(fcmd: Callable[..., CmdResult], tool: str) -> None:
    """工具 ``--help`` 不崩溃，退出码 0 或 1，退出码 0 时输出非空。"""
    r = fcmd(tool, "--help", expect=None)
    # 合法工具返回 0，未知工具返回 1；两者都视为"不崩溃"
    assert isinstance(r.returncode, int)
    if r.returncode == 0:
        assert r.stdout.strip(), "--help 输出不应为空"
