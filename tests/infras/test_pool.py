"""守护线程池：语义对齐 ``ThreadPoolExecutor``，但不阻塞进程退出。

回归背景：``submit`` 曾走 ``QThreadPool``、镜像拉取 / 测速 / 重复文件查找走
``concurrent.futures.ThreadPoolExecutor``。两者的 worker 都是非守护线程，解释器
退出时会 ``join`` 它们 —— 只要有一个任务卡在 ``wsl.exe`` 子进程或网络超时上，
用户看到的就是「wslmanw 关窗后长时间无法结束进程」。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
import time
from typing import TYPE_CHECKING

import pytest

from fcmd.shared import pool

if TYPE_CHECKING:
    from pathlib import Path


def _wait(predicate: object, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return True
        time.sleep(0.01)
    return False


def test_submit_returns_future_with_result() -> None:
    with pool.DaemonPool(2) as workers:
        future = workers.submit(lambda value: value * 2, 21)
        assert future.result(timeout=5) == 42


def test_submit_forwards_kwargs_and_exceptions() -> None:
    def boom(*, reason: str) -> None:
        raise ValueError(reason)

    with pool.DaemonPool(2) as workers:
        future = workers.submit(boom, reason="bad")
        with pytest.raises(ValueError, match="bad"):
            future.result(timeout=5)


def test_map_keeps_input_order() -> None:
    items = [3, 1, 2]

    def slow_square(value: int) -> int:
        time.sleep(0.01 * (3 - value))
        return value * value

    with pool.DaemonPool(3) as workers:
        assert workers.map(slow_square, items) == [9, 1, 4]


def test_map_propagates_first_error() -> None:
    def fail(value: int) -> int:
        if value == 2:
            raise RuntimeError("two")
        return value

    with pool.DaemonPool(2) as workers, pytest.raises(RuntimeError, match="two"):
        workers.map(fail, [1, 2, 3])


def test_workers_are_daemon_threads() -> None:
    """核心不变量：池内 worker 必须是守护线程，进程退出时才不会被 join。"""
    seen: list[bool] = []
    with pool.DaemonPool(1, thread_name_prefix="probe") as workers:
        workers.submit(lambda: seen.append(threading.current_thread().daemon)).result(timeout=5)
    assert seen == [True]


def test_pool_reuses_workers_within_limit() -> None:
    with pool.DaemonPool(2) as workers:
        names: set[str] = set()
        for _ in range(6):
            names.add(workers.submit(lambda: threading.current_thread().name).result(timeout=5))
    assert len(names) <= 2
    assert all(name.startswith("fcmd-pool") for name in names)


def test_shutdown_rejects_new_tasks() -> None:
    workers = pool.DaemonPool(1)
    workers.shutdown()
    with pytest.raises(RuntimeError, match="线程池已关闭"):
        workers.submit(lambda: None).result(timeout=5)


def test_default_pool_is_shared_singleton() -> None:
    assert pool.default_pool() is pool.default_pool()


def test_parallel_map_handles_empty_input() -> None:
    assert pool.parallel_map(lambda value: value, []) == []
    assert pool.parallel_map(lambda value: value * 2, [1, 2]) == [2, 4]


def test_parallel_map_with_single_worker_stays_inline() -> None:
    current_names: list[str] = []
    pool.parallel_map(lambda _value: current_names.append(threading.current_thread().name), [1], workers=1)
    assert current_names == [threading.current_thread().name]


_BLOCKING_SCRIPT = textwrap.dedent(
    """
    import sys, time
    import fcmd.shared.pool as pool

    workers = pool.DaemonPool(1)
    workers.submit(time.sleep, 60)
    time.sleep(0.2)
    sys.stdout.write("submitted\\n")
    sys.stdout.flush()
    """
)

_THREADPOOL_SCRIPT = textwrap.dedent(
    """
    import sys, time
    from concurrent.futures import ThreadPoolExecutor

    executor = ThreadPoolExecutor(1)
    executor.submit(time.sleep, 60)
    time.sleep(0.2)
    sys.stdout.write("submitted\\n")
    sys.stdout.flush()
    """
)


def _run_script(path: Path, source: str, timeout: float) -> tuple[int, str]:
    path.write_text(source, encoding="utf-8")
    try:
        completed = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True,
            timeout=timeout,
            check=False,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return -1, ""
    return completed.returncode, completed.stdout


@pytest.mark.slow
def test_process_exits_without_waiting_for_daemon_pool(tmp_path: Path) -> None:
    """守护线程里还压着 60 s 的任务时，解释器仍应立刻退出（对照 ThreadPoolExecutor）。"""
    started = time.monotonic()
    code, out = _run_script(tmp_path / "daemon_pool.py", _BLOCKING_SCRIPT, timeout=20)
    elapsed = time.monotonic() - started
    assert "submitted" in out
    assert code == 0
    assert elapsed < 10, f"守护线程池拖住了进程退出：{elapsed:.1f}s"

    started = time.monotonic()
    code, _out = _run_script(tmp_path / "thread_pool.py", _THREADPOOL_SCRIPT, timeout=5)
    # 对照组：ThreadPoolExecutor 的 worker 不是守护线程，进程会被 join 卡住（超时返回 -1）。
    assert code == -1
    assert time.monotonic() - started >= 4.5
