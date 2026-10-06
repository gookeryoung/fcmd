"""惰性日志代理：推迟 ``import logging`` 到真正需要时。

fcmd 的极速路径（图构建 + dependency 同步快速执行）通常不产生任何**可观测**
日志输出——logging 未配置时 INFO 级别本就被丢弃——而 logging 导入链（含
traceback / _colorize，Python 3.14）在暖进程下成本 ~20ms，是 graph/fx.task
懒加载链的最大单项开销。

:class:`LazyLogger` 的语义：

* ``logging`` 已在 ``sys.modules``（用户 ``import logging`` / ``basicConfig``
  配置过日志）→ 代理完全转发到真实 :class:`logging.Logger`，``caplog``/
  级别过滤/``extra`` 等行为一致。
* ``logging`` 未导入 → 日志调用被丢弃（与未配置日志时的默认行为等价），
  极速路径不付出导入成本。若用户在运行中途才首次导入 logging，之前的
  调用不补发（彼时本就无可观测输出）。
"""

from __future__ import annotations

import sys
from typing import Any

__all__ = ["LazyLogger"]


def _discard(*_args: Any, **_kwargs: Any) -> None:
    """无操作：logging 未导入时丢弃日志调用。"""


class LazyLogger:
    """模块级惰性 Logger 代理：按需解析真实 Logger，未启用日志时零开销。

    用法与 :func:`logging.getLogger` 完全一致::

        logger = LazyLogger(__name__)
        logger.info("...")
    """

    __slots__ = ("_name", "_real")

    def __init__(self, name: str) -> None:
        self._name = name
        self._real: Any = None

    def _resolve(self) -> Any:
        """导入 logging 并解析真实 Logger（仅首次调用付出导入成本）。"""
        import logging

        if self._real is None:
            self._real = logging.getLogger(self._name)
        return self._real

    def __getattr__(self, attr: str) -> Any:
        # 仅未定义属性（info/warning/...）会进入此处；_name/_real 走实例字典。
        if self._real is None and "logging" not in sys.modules:
            return _discard
        return getattr(self._resolve(), attr)
