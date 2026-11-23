"""兼容 shim：已迁移到 :mod:`fcmd.mcp.server`。"""

from __future__ import annotations

import sys

from fcmd.mcp import server as _impl

sys.modules[__name__] = _impl
