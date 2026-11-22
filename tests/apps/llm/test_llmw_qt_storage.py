"""llmw 存储卡片（Steam 式多模型目录）的离屏构造测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QPushButton

from fcmd.apps.llm._llmw_qt_storage import StorageAddTile, StorageGrid, StorageTile
from fcmd.apps.llm._llmw_storage import StorageLocation


def _location(path: Path, *, primary: bool = False, exists: bool = True) -> StorageLocation:
    return StorageLocation(path, "D:", "NTFS", 1000, 400, exists, primary)


def test_storage_grid_renders_tiles_and_add_card() -> None:
    app = QApplication.instance() or QApplication([])
    grid = StorageGrid()
    try:
        grid.set_locations([_location(Path("D:/models"), primary=True), _location(Path("E:/models"))])
        app.processEvents()
        tiles = grid.findChildren(StorageTile)
        assert len(tiles) == 2
        assert len(grid.findChildren(StorageAddTile)) == 1
        assert tiles[0].location.primary is True
    finally:
        grid.deleteLater()


def test_storage_grid_hides_remove_for_only_dir() -> None:
    app = QApplication.instance() or QApplication([])
    grid = StorageGrid()
    try:
        grid.set_locations([_location(Path("D:/models"), primary=True)])
        app.processEvents()
        tile = grid.findChildren(StorageTile)[0]
        labels = [button.text() for button in tile.findChildren(QPushButton)]
        assert "移除" not in labels
    finally:
        grid.deleteLater()
