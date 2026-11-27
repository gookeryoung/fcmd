"""llm 测试的进程内 Qt 清理。

概览页用后台线程采样系统负载，采样链路会调 ``nvidia-smi``
（``_llmw_qt_overview.sample_overview`` → ``_sample_load`` → ``_sample_gpu``）。
窗口/页面销毁不会取消已投递的任务，任务可能在上一个测试结束后才执行到
``subprocess.run``：此时若下一个测试把 ``subprocess.run`` 换成了假实现
（例如 ``tests/tools/dev/test_cli_autofmt.py``），残留调用会落进那个测试的
captured 列表，造成偶发断言失败。

这里在每个 llm 测试结束时等已提交的后台任务收尾，保证任务不会跨测试存活。
任务跑在守护线程池里（进程退出不等它，所以这里必须自己排空）。
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _drain_qt_tasks() -> Iterator[None]:
    yield
    workers = sys.modules.get("fcmd.gui.workers")
    if workers is None:
        return
    qtcore = sys.modules.get("PySide6.QtCore")
    app = qtcore.QCoreApplication.instance() if qtcore is not None else None
    deadline = time.monotonic() + 5.0
    # 回调（含 ``on_finished`` 的清理）由 Qt 排队回落到主线程执行，必须边等边转事件循环。
    while workers._PENDING and time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        time.sleep(0.01)


@pytest.fixture(autouse=True)
def _hermetic_vllm() -> Any:
    """vLLM 平台探测不碰真实宿主机（否则 `wsl -d` 会把开发机的实例启动起来）。"""
    from tests.helpers import forbid_wsl_distro_start, hermetic_vllm_platform

    restore_platform = hermetic_vllm_platform()
    restore_guard = forbid_wsl_distro_start()
    yield
    restore_guard()
    restore_platform()
