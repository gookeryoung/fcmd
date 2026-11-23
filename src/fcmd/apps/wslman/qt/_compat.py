"""wslman 窗口方法迁移期的共享类型/别名（ADR 0046 P2/P3）。"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QDialog, QListWidgetItem, QPushButton, QWidget

from fcmd.apps.wslman import _files, _initenv, _open
from fcmd.apps.wslman._distro import Distro
from fcmd.apps.wslman._images import ImageEntry
from fcmd.gui.widgets import ItemRow

__all__ = [
    "Any",
    "Distro",
    "ImageEntry",
    "ItemRow",
    "Mapping",
    "Path",
    "QDialog",
    "QListWidgetItem",
    "QPushButton",
    "QWidget",
    "Sequence",
    "ThreadPoolExecutor",
    "_files",
    "_initenv",
    "_open",
    "threading",
]
