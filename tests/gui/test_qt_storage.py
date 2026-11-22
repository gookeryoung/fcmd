"""通用多存储目录组件：StorageRoots 逻辑与 StorageCard 离屏构造。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QPushButton

from fcmd.gui.widgets import DiskLocation, DiskTile, StorageCard, StorageRoots


def test_storage_roots_add_remove_and_primary(tmp_path: Path) -> None:
    first = tmp_path / "models-a"
    second = tmp_path / "models-b"
    roots = StorageRoots([first])
    assert roots.add(second) is True
    assert roots.add(first) is False  # 去重（Windows 大小写 / 绝对路径）
    assert roots.make_primary(second) is True
    assert roots.paths[0] == second
    assert roots.remove(first) is True
    assert roots.paths == [second]
    # 至少保留一个目录
    assert roots.remove(second) is False


def test_storage_card_renders_tiles_and_removal(monkeypatch: pytest.MonkeyPatch) -> None:
    from fcmd.gui.widgets import storage as storage_module

    monkeypatch.setattr(storage_module, "confirm", lambda *_args, **_kwargs: True)
    app = QApplication.instance() or QApplication([])
    card = StorageCard([Path("D:/models"), Path("E:/models")], add_text="添加工作目录")
    try:
        app.processEvents()
        tiles = card.grid.findChildren(DiskTile)
        assert len(tiles) == 2
        assert tiles[0].location.primary is True
        add = [child for child in card.findChildren(QPushButton) if child.objectName() == "DiskAdd"]
        assert add and add[0].text() == "添加工作目录"
        # 移除由组件自身处理：调用内部动作后同步列表并刷新卡片
        card._remove(str(Path("E:/models")))
        app.processEvents()
        assert card.roots() == [Path("D:/models")]
    finally:
        card.deleteLater()


def test_storage_card_supports_custom_disk_locations() -> None:
    app = QApplication.instance() or QApplication([])
    location = DiskLocation(Path("X:/custom"), "X:", "NTFS", 1000, 400, primary=True)
    card = StorageCard([Path("X:/custom")], locations=lambda _roots: [location])
    try:
        app.processEvents()
        tile = card.grid.findChildren(DiskTile)[0]
        assert tile.location.label == "X:"
        assert tile.location.primary_label == "主目录"
    finally:
        card.deleteLater()
