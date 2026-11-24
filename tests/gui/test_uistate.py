"""GUI 共用持久化辅助的测试：路径历史、最近列表与防抖写盘。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.gui.uistate import (
    DebouncedSaver,
    PathHistory,
    RecentFiles,
    merge_recent,
    normalize_recent,
)


def test_path_history_remembers_directory_and_file_parent(tmp_path: Path) -> None:
    payload: dict[str, str] = {}
    paths = PathHistory(payload)
    folder = tmp_path / "scan"
    folder.mkdir()
    file = folder / "notes.txt"
    file.write_text("x", encoding="utf-8")

    assert paths.start("last_open_dir") == ""
    assert paths.remember("last_open_dir", folder) == str(folder)
    assert payload["last_open_dir"] == str(folder)
    assert paths.start("last_open_dir") == str(folder)

    assert paths.remember("last_save_dir", file) == str(folder)
    assert payload["last_save_dir"] == str(folder)
    # 保存对话框选的是尚不存在的新文件：仍记父目录。
    assert paths.remember("last_save_dir", folder / "new-report.csv") == str(folder)


def test_path_history_stale_value_falls_back_to_system_default(tmp_path: Path) -> None:
    payload = {"last_open_dir": str(tmp_path / "missing"), "last_export_dir": 3}
    paths = PathHistory(payload)

    assert paths.start("last_open_dir") == ""
    assert paths.start("last_export_dir") == ""
    assert paths.raw("last_export_dir") == ""
    assert paths.default_name("last_export_dir", "report.csv") == "report.csv"


def test_path_history_default_name_uses_last_directory(tmp_path: Path) -> None:
    payload: dict[str, str] = {}
    paths = PathHistory(payload)
    paths.remember("last_export_dir", tmp_path)

    assert paths.default_name("last_export_dir", "report.csv") == str(tmp_path / "report.csv")


def test_recent_files_dedupes_and_trims_limit() -> None:
    def p(value: str) -> str:
        return str(Path(value))

    payload: dict[str, object] = {}
    recent = RecentFiles(payload, limit=3)
    assert recent.items() == []

    assert recent.add(p("/data/a.exe")) == [p("/data/a.exe")]
    assert recent.add(p("/data/b.exe")) == [p("/data/b.exe"), p("/data/a.exe")]
    # 同一路径再次打开置顶且不重复。
    assert recent.add(p("/data/a.exe")) == [p("/data/a.exe"), p("/data/b.exe")]
    recent.add(p("/data/c.exe"))
    assert recent.items() == [p("/data/c.exe"), p("/data/a.exe"), p("/data/b.exe")]
    assert payload["recent_files"] == recent.items()


def test_merge_recent_keeps_legacy_semantics() -> None:
    """不校验路径存在性：移动盘 / 网络盘离线时不清空历史。"""

    def p(value: str) -> str:
        return str(Path(value))

    files = [p("C:/a.exe"), p("C:/b.dll")]
    assert merge_recent(files, p("C:/b.dll")) == [p("C:/b.dll"), p("C:/a.exe")]
    assert merge_recent(files, p("C:/new.exe"), limit=2) == [p("C:/new.exe"), p("C:/a.exe")]
    assert merge_recent([42, "", p("C:/a.exe")], p("C:/a.exe")) == [p("C:/a.exe")]  # type: ignore[list-item]


def test_normalize_recent_filters_invalid_entries() -> None:
    assert normalize_recent(["a", 3, "", "a", "b"], limit=5) == ["a", "b"]
    assert normalize_recent("not-a-list") == []
    assert normalize_recent(["a", "b"], limit=1) == ["a"]


def test_debounced_saver_flushes_once_and_flush_is_immediate() -> None:
    pytest.importorskip("PySide6")
    import os

    from PySide6.QtCore import QEventLoop, QTimer

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from fcmd.gui.app import create_application

    # 复用 / 创建 QApplication：在测试进程里先建 QCoreApplication 后再建 QApplication
    # 会直接崩溃，这里统一走窗口测试同一套入口。
    application = create_application("uistate-test")
    assert application is not None
    calls: list[str] = []
    saver = DebouncedSaver(lambda: calls.append("save"), delay_ms=20)

    saver.touch()
    saver.touch()
    assert saver.pending() is True
    loop = QEventLoop()
    QTimer.singleShot(80, loop.quit)
    loop.exec()
    assert calls == ["save"]
    assert saver.pending() is False

    saver.touch()
    saver.flush()
    assert calls == ["save", "save"]
    assert saver.pending() is False

    saver.touch()
    saver.cancel()
    assert saver.pending() is False
