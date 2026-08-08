import sys

if sys.version_info >= (3, 13):  # pragma: no cover - 测试环境单版本
    from typing import TypeVar
else:
    from typing_extensions import TypeVar

__all__ = ["TypeVar"]
