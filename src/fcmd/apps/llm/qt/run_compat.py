"""llmw 运行页方法迁移期的共享类型（ADR 0046 P4）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from fcmd.apps.llm import _llmpaint_server, _llmw_compat, llmbase
from fcmd.apps.llm.llmbase import LaunchPlan
from fcmd.apps.llm.llmserve import ServeState, StopResult

__all__ = [
    "Any",
    "LaunchPlan",
    "Path",
    "QLabel",
    "QVBoxLayout",
    "QWidget",
    "ServeState",
    "StopResult",
    "_llmpaint_server",
    "_llmw_compat",
    "llmbase",
]
