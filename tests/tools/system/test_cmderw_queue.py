"""传输队列暂停门与并行调度测试。"""

from __future__ import annotations

import threading

import pytest

from fcmd.tools.system import _cmderw_queue as queue_mod


class TestClampParallel:
    def test_bounds(self) -> None:
        assert queue_mod.clamp_parallel(0) == queue_mod.MIN_QUEUE_PARALLEL
        assert queue_mod.clamp_parallel(3) == 3
        assert queue_mod.clamp_parallel(99) == queue_mod.MAX_QUEUE_PARALLEL


class TestPauseGate:
    def test_defaults_to_running(self) -> None:
        gate = queue_mod.PauseGate()
        assert not gate.paused
        assert gate.wait(0.01) is True

    def test_pause_blocks_until_resume(self) -> None:
        gate = queue_mod.PauseGate()
        gate.pause()
        assert gate.paused
        assert gate.wait(0.01) is False
        gate.resume()
        assert not gate.paused
        assert gate.wait(0.01) is True

    def test_resume_wakes_waiter(self) -> None:
        gate = queue_mod.PauseGate()
        gate.pause()
        released = threading.Event()

        def waiter() -> None:
            assert gate.wait(5) is True
            released.set()

        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        gate.resume()
        assert released.wait(5)
        thread.join(5)


class TestParallelMap:
    def test_serial_keeps_order(self) -> None:
        seen: list[int] = []
        queue_mod.parallel_map([1, 2, 3], seen.append, parallel=1)
        assert seen == [1, 2, 3]

    def test_runs_all_items(self) -> None:
        seen: list[int] = []
        lock = threading.Lock()

        def worker(item: int) -> None:
            with lock:
                seen.append(item)

        queue_mod.parallel_map([1, 2, 3, 4], worker, parallel=4)
        assert sorted(seen) == [1, 2, 3, 4]

    def test_parallel_really_concurrent(self) -> None:
        barrier = threading.Barrier(2, timeout=5)

        def worker(_item: int) -> None:
            barrier.wait()

        queue_mod.parallel_map([1, 2], worker, parallel=2)

    def test_worker_exception_propagates(self) -> None:
        def worker(_item: int) -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            queue_mod.parallel_map([1], worker, parallel=2)

    def test_empty_items(self) -> None:
        queue_mod.parallel_map([], lambda _item: None, parallel=4)
