"""paramesh 的统一错误类型。"""

from __future__ import annotations

__all__ = ["ParameshError"]


class ParameshError(ValueError):
    """网格生成输入不成立：模型非法、尺度不可用或输出冲突。"""
