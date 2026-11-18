"""TOML 读取兼容层：Python 3.11+ 用标准库 ``tomllib``，3.10 回落到 ``tomli``。"""

from __future__ import annotations

try:  # pragma: no cover - 3.11+ 走标准库
    import tomllib as _toml  # pyrefly: ignore [missing-import]
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as _toml  # type: ignore[no-redef]

__all__ = ["TOMLDecodeError", "load", "loads"]

load = _toml.load
loads = _toml.loads
TOMLDecodeError = _toml.TOMLDecodeError
