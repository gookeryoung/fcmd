import sys

if sys.version_info >= (3, 13):
    from typing import TypeVar
else:
    from typing_extensions import TypeVar

__all__ = ["TypeVar"]
