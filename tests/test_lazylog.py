"""LazyLogger 测试：推迟 logging 导入的代理行为。"""

from __future__ import annotations

import logging
import subprocess
import sys

import pytest

from fcmd._lazylog import LazyLogger


def test_lazy_logger_forwards_to_real_logger() -> None:
    """代理方法转发到真实 Logger，且重复解析命中缓存。"""
    logger = LazyLogger("fcmd.test.lazy")
    real = logging.getLogger("fcmd.test.lazy")
    # 触发解析并验证方法转发（级别/名字与真实 Logger 一致）
    logger.info("消息")
    logger.warning("警告")
    assert logger._real is real  # 测试访问私有属性验证解析缓存
    assert logger.getEffectiveLevel() == real.getEffectiveLevel()


def test_lazy_logger_uses_logging_module_mechanics(caplog: pytest.LogCaptureFixture) -> None:
    """代理输出走 logging 机制（caplog 可捕获，propagate 正常）。"""
    logger = LazyLogger("fcmd.test.caplog")
    with caplog.at_level(logging.ERROR, logger="fcmd.test.caplog"):
        logger.error("错误消息")
    assert any(r.message == "错误消息" for r in caplog.records)
    # _resolve 后代理与同名真实 Logger 等价
    assert logging.getLogger("fcmd.test.caplog") is logger._real


def test_lazy_logger_defers_logging_import() -> None:
    """logging 未导入时丢弃日志调用且不触发导入；用户启用后恢复转发（子进程隔离）。"""
    code = (
        "import sys\n"
        "from fcmd._lazylog import LazyLogger\n"
        "logger = LazyLogger('fcmd.test.defer')\n"
        "logger.info('no-op')\n"
        "assert 'logging' not in sys.modules, 'logging should not be imported'\n"
        "import logging\n"
        "logging.basicConfig()\n"
        "logger.warning('forwarded')\n"
        "assert logging.getLogger('fcmd.test.defer') is logger._real\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
