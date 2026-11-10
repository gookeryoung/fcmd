"""懒加载 ``__getattr__`` / ``__dir__`` 模板与导入耗时诊断。

供 :mod:`fcmd`、:mod:`fcmd.core`、:mod:`fcmd.core.execution` 三个包的
``__getattr__`` / ``__dir__`` 共用，避免各包重复实现同一段
"按映射导入 → 缓存到包命名空间 → 返回" 的逻辑。

设置 ``FCMD_IMPORT_DEBUG`` 可让 :func:`timed_import` 把慢导入输出到 stderr：
``1`` / ``on`` 使用默认 100ms 阈值，也可直接给毫秒数（如 ``FCMD_IMPORT_DEBUG=50``）。
未设置或为 ``0`` / ``off`` 时不做任何计时，保持默认路径零额外开销。
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Mapping, Sequence
from types import ModuleType
from typing import Any

__all__ = ["lazy_dir", "lazy_getattr", "slow_import_threshold_ms", "timed_import"]

_SLOW_IMPORT_ENV = "FCMD_IMPORT_DEBUG"
_DEFAULT_SLOW_IMPORT_MS = 100.0
_DISABLED_VALUES = frozenset({"", "0", "off", "false", "no"})
_ENABLED_VALUES = frozenset({"1", "on", "true", "yes"})


def slow_import_threshold_ms() -> float | None:
    """解析 ``FCMD_IMPORT_DEBUG`` 得到慢导入阈值（毫秒）；未开启时返回 ``None``。"""
    raw = os.environ.get(_SLOW_IMPORT_ENV, "").strip().lower()
    if raw in _DISABLED_VALUES:
        return None
    if raw in _ENABLED_VALUES:
        return _DEFAULT_SLOW_IMPORT_MS
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_SLOW_IMPORT_MS
    return value if value > 0 else _DEFAULT_SLOW_IMPORT_MS


def timed_import(module_path: str, origin: str) -> ModuleType:
    """导入模块；诊断开启且耗时超过阈值时向 stderr 输出一行记录。

    ``origin`` 标明触发导入的来源（如 ``fcmd.core.TaskSpec`` 或 ``tool:img2icow``），
    便于从输出反查是谁拖慢了启动。
    """
    threshold = slow_import_threshold_ms()
    if threshold is None:
        return _load_module(module_path)
    start = time.perf_counter()
    module = _load_module(module_path)
    elapsed_ms = (time.perf_counter() - start) * 1000
    if elapsed_ms >= threshold:
        sys.stderr.write(f"[fcmd] slow import {elapsed_ms:.1f}ms: {module_path} (from {origin})\n")
    return module


def _load_module(module_path: str) -> ModuleType:
    """实际导入入口，独立成函数便于测试替换而不污染全局 importlib。"""
    import importlib

    return importlib.import_module(module_path)


def lazy_getattr(
    module_name: str,
    mapping: Mapping[str, tuple[str, str | None]],
    name: str,
    namespace: dict[str, Any],
) -> Any:
    """按映射懒加载属性，并缓存到 ``namespace``（通常是 ``globals()``）。

    ``mapping`` 的值为 ``(模块路径, 符号名)``；符号名为 ``None`` 表示返回
    子模块自身（即 ``from fcmd.core import toolkit`` 语义）。未登记的属性
    抛 ``AttributeError``（消息含 ``module_name``，与默认行为一致）。
    """
    target = mapping.get(name)
    if target is None:
        raise AttributeError(f"module {module_name!r} has no attribute {name!r}")
    module_path, attr_name = target
    module = timed_import(module_path, f"{module_name}.{name}")
    value = module if attr_name is None else getattr(module, attr_name)
    namespace[name] = value  # 缓存到全局，后续直接命中
    return value


def lazy_dir(namespace: Mapping[str, Any], all_names: Sequence[str]) -> list[str]:
    """合并当前命名空间与 ``__all__``，得到 ``dir()`` 补全建议列表。"""
    return sorted(set(namespace) | set(all_names))
