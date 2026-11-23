"""Infras 媒体子包：可选依赖状态（deps）、图标（icon）与图片处理（image）。"""

from __future__ import annotations

from .deps import *  # noqa: F403
from .deps import HAS_PIL, HAS_RESVG  # noqa: F401
from .icon import *  # noqa: F403
from .icon import (  # noqa: F401
    _has_valid_magic,
    _looks_like_svg,
    _parse_sizes,
    _resize_for_icon,
)
from .image import *  # noqa: F403
from .image import (
    _apply_exif_modifications,  # noqa: F401
    _apply_single_exif_set,  # noqa: F401
    _load_font,  # noqa: F401
    _resolve_position,  # noqa: F401
    _save_image,  # noqa: F401
)
