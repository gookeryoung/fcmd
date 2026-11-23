"""llmw 模型页方法迁移期的共享类型（ADR 0046 P4）。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QListWidgetItem, QWidget

from fcmd.apps.llm import _llmw_models
from fcmd.gui.widgets import ItemRow

from .._llmw_qt_center import (
    FolderInfo,
    ImportPlan,
    ModelFile,
    ModelInfo,
    ModelListCache,
    ModelSearch,
    ModelSummary,
    ProgressEvent,
    QuantOption,
)

__all__ = [
    "Any",
    "Callable",
    "FolderInfo",
    "ImportPlan",
    "ItemRow",
    "ModelFile",
    "ModelInfo",
    "ModelListCache",
    "ModelSearch",
    "ModelSummary",
    "Path",
    "ProgressEvent",
    "QListWidgetItem",
    "QWidget",
    "QuantOption",
    "Sequence",
    "_llmw_models",
]
