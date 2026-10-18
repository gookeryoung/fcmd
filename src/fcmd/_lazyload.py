"""懒加载 ``__getattr__`` / ``__dir__`` 模板。

供 :mod:`fcmd`、:mod:`fcmd.core`、:mod:`fcmd.core.execution` 三个包的
``__getattr__`` / ``__dir__`` 共用，避免各包重复实现同一段
"按映射导入 → 缓存到包命名空间 → 返回" 的逻辑。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["lazy_dir", "lazy_getattr"]


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
    import importlib

    module = importlib.import_module(module_path)
    value = module if attr_name is None else getattr(module, attr_name)
    namespace[name] = value  # 缓存到全局，后续直接命中
    return value


def lazy_dir(namespace: Mapping[str, Any], all_names: Sequence[str]) -> list[str]:
    """合并当前命名空间与 ``__all__``，得到 ``dir()`` 补全建议列表。"""
    return sorted(set(namespace) | set(all_names))
