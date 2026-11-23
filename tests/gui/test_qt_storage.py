"""通用多存储目录组件：StorageRoots 逻辑与 StorageCard 离屏构造。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QPushButton

from fcmd.gui.widgets import (
    DiskGrid,
    DiskLocation,
    DiskTile,
    StorageCard,
    StoragePairCard,
    StorageRoots,
    default_disk_locations,
)


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
        assert len(add) == 1
        assert add[0].text() == "添加工作目录"
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


def test_storage_card_can_clear_last_root_when_host_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    """``min_count=0`` 的宿主可以移除最后一个目录（用于回落默认）。"""
    from fcmd.gui.widgets import storage as storage_module

    monkeypatch.setattr(storage_module, "confirm", lambda *_args, **_kwargs: True)
    app = QApplication.instance() or QApplication([])
    card = StorageCard([Path("D:/models")], min_count=0)
    try:
        app.processEvents()
        card._remove(str(Path("D:/models")))
        app.processEvents()
        assert card.roots() == []
    finally:
        card.deleteLater()


def test_storage_card_can_hide_primary_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    """``show_primary=False``：目录地位平等，不出主目录胶囊与「设为主目录」。"""
    from PySide6.QtWidgets import QLabel

    from fcmd.gui.widgets import storage as storage_module

    # 移除动作会弹确认框；离屏环境下必须打桩，否则模态框会把测试挂住。
    monkeypatch.setattr(storage_module, "confirm", lambda *_args, **_kwargs: True)
    app = QApplication.instance() or QApplication([])
    card = StorageCard([Path("D:/scan-a"), Path("E:/scan-b")], show_primary=False, min_count=0)
    try:
        app.processEvents()
        buttons = [child.text() for child in card.findChildren(QPushButton)]
        assert "设为主目录" not in buttons
        assert buttons.count("移除") == 2
        assert [child.text() for child in card.findChildren(QLabel) if child.text() == "主目录"] == []
        # 移除到空列表不受 min_count 限制（宿主自己决定空列表语义）。
        card._remove(str(Path("D:/scan-a")))
        assert card.roots() == [Path("E:/scan-b")]
    finally:
        card.deleteLater()


def test_default_disk_locations_override_labels_and_meta(tmp_path: Path) -> None:
    """labels / metas 按目录下标覆盖卡片标题与副标题，用于左 / 右逻辑分组。"""
    locations = default_disk_locations(
        [tmp_path / "a", tmp_path / "b"],
        labels=("左目录", "右目录"),
        metas=("L:", "R:"),
    )
    assert [(item.label, item.meta) for item in locations] == [("左目录", "L:"), ("右目录", "R:")]
    assert locations[0].primary is True
    assert locations[1].primary is False


def test_disk_grid_max_count_hides_add_tile() -> None:
    """达到 max_count 后不再显示「添加目录」卡片。"""
    from PySide6.QtWidgets import QPushButton

    app = QApplication.instance() or QApplication([])
    grid = DiskGrid(add_text="添加目录", max_count=2, min_count=0)
    try:
        grid.set_locations(
            [
                DiskLocation(Path("D:/a"), "D:", primary=True),
                DiskLocation(Path("E:/b"), "E:"),
            ]
        )
        app.processEvents()
        add = [child for child in grid.findChildren(QPushButton) if child.objectName() == "DiskAdd"]
        assert add == []
        # 移除一个后又允许添加。
        grid.set_locations([DiskLocation(Path("D:/a"), "D:", primary=True)])
        app.processEvents()
        add = [child for child in grid.findChildren(QPushButton) if child.objectName() == "DiskAdd"]
        assert len(add) == 1
    finally:
        grid.deleteLater()


def test_disk_grid_resets_column_stretch_after_removing_roots() -> None:
    """删掉目录、列数变少后「添加目录」卡要重新占满一行，而不是被旧列 stretch 挤窄。"""
    app = QApplication.instance() or QApplication([])
    grid = DiskGrid(add_text="添加目录", min_count=0)
    try:
        grid.set_locations(
            [
                DiskLocation(Path("D:/a"), "D:", primary=True),
                DiskLocation(Path("E:/b"), "E:"),
            ]
        )
        app.processEvents()
        assert [grid._grid.columnStretch(column) for column in range(3)] == [1, 1, 1]

        # 回到 0 个目录：只剩“添加”卡，第一列占满，旧列不再平分宽度。
        grid.set_locations([])
        app.processEvents()
        assert [grid._grid.columnStretch(column) for column in range(3)] == [1, 0, 0]
    finally:
        grid.deleteLater()


def test_storage_card_change_replaces_root_in_place(monkeypatch: pytest.MonkeyPatch) -> None:
    """单目录「更改」：原位置替换目录，不改变顺序（主目录仍是首个）。"""
    from fcmd.gui.widgets import storage as storage_module

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(storage_module.QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: "E:/new")
    card = StorageCard(
        [Path("D:/old"), Path("F:/keep")],
        add_text=None,
        min_count=1,
        max_count=2,
        show_primary=False,
        change_text="更改",
    )
    changes: list[object] = []
    card.changed.connect(changes.append)
    try:
        app.processEvents()
        card._change("D:/old")
        assert card.roots() == [Path("E:/new"), Path("F:/keep")]
        assert changes and [Path(str(item)) for item in changes[-1]] == card.roots()  # type: ignore[union-attr]
    finally:
        card.deleteLater()


def test_storage_card_swap_reorders_roots() -> None:
    """swap() 交换两个目录位置并发 changed（双目录 / 手动排序复用）。"""
    app = QApplication.instance() or QApplication([])
    card = StorageCard([Path("D:/left"), Path("E:/right")], add_text=None, min_count=2, show_primary=False)
    try:
        app.processEvents()
        assert card.swap() is True
        assert card.roots() == [Path("E:/right"), Path("D:/left")]
        assert card.swap(0, 5) is False
    finally:
        card.deleteLater()


def test_storage_card_remove_can_skip_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    """``confirm_remove=False``：移除只清列表，不弹确认框（filescanw 扫描目录）。"""
    from fcmd.gui.widgets import storage as storage_module

    def _no_confirm(*_args: object, **_kwargs: object) -> bool:
        pytest.fail("confirm_remove=False 时不应弹确认框")

    monkeypatch.setattr(storage_module, "confirm", _no_confirm)
    app = QApplication.instance() or QApplication([])
    card = StorageCard([Path("D:/scan-a"), Path("E:/scan-b")], show_primary=False, min_count=0, confirm_remove=False)
    try:
        app.processEvents()
        card._remove(str(Path("E:/scan-b")))
        app.processEvents()
        assert card.roots() == [Path("D:/scan-a")]
    finally:
        card.deleteLater()


def test_storage_pair_card_renders_two_disk_tiles_with_placeholder(tmp_path: Path) -> None:
    """左右各一张磁盘卡（沿用 StorageCard 的 DiskTile）；未选择时显示占位文案。"""
    app = QApplication.instance() or QApplication([])
    left = tmp_path / "left"
    left.mkdir()
    card = StoragePairCard(str(left), "")
    try:
        app.processEvents()
        tiles = card.findChildren(DiskTile)
        assert len(tiles) == 2
        assert tiles[0].location.label == "左侧"
        assert tiles[0].location.display_text == str(left)
        assert tiles[1].location.label == "右侧"
        assert tiles[1].location.placeholder == "未选择目录"
        assert tiles[1].location.display_text == "未选择目录"
        # 未选择的一侧没有容量条数据，提示去「更改」。
        assert tiles[1].location.total == 0
    finally:
        card.deleteLater()


def test_storage_pair_card_swap_and_change_emit(tmp_path: Path) -> None:
    """交换只换左右槽位；「更改」在对应槽位弹目录框并回填。"""
    from fcmd.gui.widgets import storage as storage_module

    app = QApplication.instance() or QApplication([])
    monkeypatch = pytest.MonkeyPatch()
    try:
        card = StoragePairCard("L", "R")
        changed: list[tuple[str, str]] = []
        swapped: list[tuple[str, str]] = []
        card.changed.connect(lambda left, right: changed.append((left, right)))
        card.swapped.connect(lambda left, right: swapped.append((left, right)))
        app.processEvents()
        assert card.sides() == ("L", "R")

        card.swap()
        assert card.sides() == ("R", "L")
        assert changed[-1] == ("R", "L")
        assert swapped == [("R", "L")]

        monkeypatch.setattr(storage_module.QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: "D:/picked")
        card._change(1)
        assert card.sides() == ("R", "D:/picked")
        assert changed[-1] == ("R", "D:/picked")
        assert swapped == [("R", "L")]  # 只有 swap() 发 swapped

        # 取消选择不改变槽位，也不发 changed。
        monkeypatch.setattr(storage_module.QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: "")
        before = list(changed)
        card._change(0)
        assert card.sides() == ("R", "D:/picked")
        assert changed == before
        card.deleteLater()
    finally:
        monkeypatch.undo()


def test_storage_pair_card_change_button_targets_its_own_slot() -> None:
    """两张卡的「更改」各自绑定槽位，点右侧不会改到左侧。"""
    from fcmd.gui.widgets import storage as storage_module

    app = QApplication.instance() or QApplication([])
    card = StoragePairCard("L", "R")
    monkeypatch = pytest.MonkeyPatch()
    try:
        app.processEvents()
        monkeypatch.setattr(storage_module.QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: "D:/picked")
        buttons = [btn for btn in card.right_tile.findChildren(QPushButton) if btn.text() == "更改"]
        assert len(buttons) == 1
        buttons[0].click()
        assert card.sides() == ("L", "D:/picked")
    finally:
        monkeypatch.undo()
        card.deleteLater()
