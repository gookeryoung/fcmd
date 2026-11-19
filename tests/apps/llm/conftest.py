"""llm 测试的进程内 Qt 清理。

概览页用 ``QThreadPool`` 采样系统负载，采样链路会调 ``nvidia-smi``
（``_llmw_qt_overview.sample_once`` → ``_sample_load`` → ``_sample_gpu``）。
窗口/页面销毁不会取消已投递的任务，任务可能在上一个测试结束后才执行到
``subprocess.run``：此时若下一个测试把 ``subprocess.run`` 换成了假实现
（例如 ``tests/tools/dev/test_cli_autofmt.py``），残留调用会落进那个测试的
captured 列表，造成偶发断言失败。

这里在每个 llm 测试结束时等待线程池收尾，保证任务不会跨测试存活。
只在 Qt 已经导入时才等待，不额外引入 Qt 依赖。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _drain_qt_tasks() -> Iterator[None]:
    yield
    qtcore = sys.modules.get("PySide6.QtCore")
    if qtcore is not None:
        qtcore.QThreadPool.globalInstance().waitForDone(5000)
