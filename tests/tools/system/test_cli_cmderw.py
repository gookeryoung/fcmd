"""cmderw 双面板文件管理器测试。

验证 ``fcmd.tools.system.cmderw`` 模块：
- 工具注册与 GUI 启动入口
- 设置读写、历史记录与路径解析
- 目录读取 / 排序 / 格式化展示
- 文件复制、移动、删除、重命名与冲突计划
- 打包 / 解压（复用 archivex）、哈希（复用 hashbase）、搜索（复用 fileops）
- 预览、属性描述与系统默认程序打开
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.gui import window as _guiwindow
from fcmd.tools.system import cmderw
from fcmd.tools.system.cmderw import (
    ARCHIVE_SUFFIXES,
    DEFAULT_GEOMETRY,
    MAX_HISTORY,
    CmderSettings,
    ColorRule,
    FileEntry,
    PanelState,
    SearchHit,
    TransferItem,
    archive_stem,
    config_path,
    copy_entry,
    create_directory,
    default_archive_name,
    default_directory,
    default_extract_dir,
    delete_entries,
    delete_entry,
    describe_attributes,
    describe_entry,
    extract_archive_to,
    format_hash_report,
    format_mtime,
    format_search_hit,
    format_size,
    hash_paths,
    history_back,
    history_forward,
    is_hidden,
    is_within,
    list_directory,
    load_preview,
    load_settings,
    move_entry,
    normalize_tab_index,
    open_externally,
    pack_entries,
    panel_tab_title,
    parent_directory,
    plan_transfer,
    push_history,
    rename_entry,
    resolve_geometry,
    resolve_path_input,
    save_settings,
    search_paths,
    sort_entries,
    transfer_entries,
    unique_path,
    validate_name,
)


# ============================================================================ #
# 注册与启动入口
# ============================================================================ #
def test_tool_registered() -> None:
    """cmderw 应以单命令形态注册。"""
    assert set(_TOOL_REGISTRY["cmderw"]) == {None}


def test_run_gui_delegates_to_window_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_gui 把窗口工厂与标题交给统一运行器。"""
    captured: list[tuple[Any, str]] = []

    def fake_run_window(factory: Any, message: str) -> None:
        captured.append((factory, message))

    monkeypatch.setattr(_guiwindow, "run_window", fake_run_window)
    cmderw.run_gui()
    factory, message = captured[0]
    assert callable(factory)
    assert message == "双面板文件管理器"


def test_launch_cmd_calls_run_gui(monkeypatch: pytest.MonkeyPatch) -> None:
    """工具入口直接打开窗口。"""
    calls: list[bool] = []
    monkeypatch.setattr(cmderw, "run_gui", lambda: calls.append(True))
    cmderw.launch_cmd()
    assert calls == [True]


# ============================================================================ #
# 配置与设置
# ============================================================================ #
class TestSettings:
    """设置文件读写与回退。"""

    def test_config_path_uses_fcmd_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert config_path() == tmp_path / "ui" / "cmderw.json"

    def test_config_path_falls_back_to_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("FCMD_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert config_path() == tmp_path / ".fcmd" / "ui" / "cmderw.json"

    def test_default_directory_uses_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert default_directory() == tmp_path

    def test_default_directory_falls_back_to_cwd(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        missing = tmp_path / "missing-home"
        monkeypatch.setattr(Path, "home", lambda: missing)
        assert default_directory() == Path.cwd()

    def test_load_settings_defaults(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        settings = load_settings()
        assert settings.left.path == str(tmp_path)
        assert settings.right.path == str(tmp_path)
        assert settings.active == "left"
        assert settings.show_hidden is False
        assert settings.geometry is None

    def test_save_and_load_roundtrip(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        left_dir = tmp_path / "left"
        right_dir = tmp_path / "right"
        left_dir.mkdir()
        right_dir.mkdir()
        settings = CmderSettings(
            left=PanelState(
                path=str(left_dir),
                sort_key="size",
                descending=True,
                history=[str(left_dir)],
                history_index=0,
            ),
            right=PanelState(path=str(right_dir), sort_key="mtime"),
            active="right",
            show_hidden=True,
            geometry="1000x700+10+20",
        )
        save_settings(settings)
        loaded = load_settings()
        assert loaded.left.path == str(left_dir)
        assert loaded.left.sort_key == "size"
        assert loaded.left.descending is True
        assert loaded.left.history == [str(left_dir)]
        assert loaded.right.path == str(right_dir)
        assert loaded.right.sort_key == "mtime"
        assert loaded.active == "right"
        assert loaded.show_hidden is True
        assert loaded.geometry == "1000x700+10+20"

    def test_save_and_load_roundtrip_keeps_tabs_and_bookmarks(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        settings = CmderSettings(
            left_tabs=[PanelState(path=str(first)), PanelState(path=str(second), sort_key="size")],
            right_tabs=[PanelState(path=str(second))],
            left_tab_index=1,
            right_tab_index=0,
            bookmarks=[str(first), str(second)],
        )
        save_settings(settings)
        loaded = load_settings()
        assert [tab.path for tab in loaded.left_tabs] == [str(first), str(second)]
        assert [tab.path for tab in loaded.right_tabs] == [str(second)]
        assert loaded.left_tab_index == 1
        assert loaded.right_tab_index == 0
        assert loaded.left.path == str(second)
        assert loaded.left.sort_key == "size"
        assert loaded.right.path == str(second)
        assert loaded.bookmarks == [str(first), str(second)]

    def test_save_and_load_roundtrip_keeps_color_rules(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        rules = [
            ColorRule(pattern="*.log", kind="file", foreground="#ff0000"),
            ColorRule(pattern="*", min_size=1024, newer_days=7, background="#00ff00"),
        ]
        save_settings(
            CmderSettings(
                left=PanelState(path=str(tmp_path)),
                right=PanelState(path=str(tmp_path)),
                color_rules=rules,
            )
        )
        loaded = load_settings()
        assert loaded.color_rules == rules

    def test_load_old_config_without_tabs(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        path = config_path()
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"left": {"path": str(tmp_path)}, "right": {"path": str(tmp_path)}}),
            encoding="utf-8",
        )
        settings = load_settings()
        assert [tab.path for tab in settings.left_tabs] == [str(tmp_path)]
        assert [tab.path for tab in settings.right_tabs] == [str(tmp_path)]
        assert settings.left_tab_index == 0
        assert settings.right_tab_index == 0
        assert settings.bookmarks == []
        assert settings.color_rules == []

    def test_load_invalid_tabs_and_bookmarks_fall_back(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        path = config_path()
        path.parent.mkdir(parents=True)
        other = tmp_path / "other"
        path.write_text(
            json.dumps(
                {
                    "left": {"path": str(tmp_path)},
                    "right": {"path": str(tmp_path)},
                    "left_tabs": "bad",
                    "right_tabs": [None, 3],
                    "left_tab_index": "2",
                    "right_tab_index": 7,
                    "bookmarks": [3, "", str(tmp_path), str(tmp_path), str(other)],
                }
            ),
            encoding="utf-8",
        )
        settings = load_settings()
        assert [tab.path for tab in settings.left_tabs] == [str(tmp_path)]
        assert [tab.path for tab in settings.right_tabs] == [str(tmp_path)]
        assert settings.left_tab_index == 0
        assert settings.right_tab_index == 0
        assert settings.bookmarks == [str(tmp_path), str(other)]

    def test_load_settings_invalid_json(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        path = config_path()
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        assert load_settings().left.path == str(tmp_path)

    def test_load_settings_invalid_fields(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        target_file = tmp_path / "a.txt"
        target_file.write_text("x", encoding="utf-8")
        path = config_path()
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "left": "bad",
                    "right": {
                        "path": str(target_file),
                        "sort_key": "bad",
                        "descending": "yes",
                        "history": ["", 3, str(tmp_path)],
                        "history_index": 9,
                    },
                    "active": "middle",
                    "show_hidden": "yes",
                    "geometry": "bad",
                }
            ),
            encoding="utf-8",
        )
        settings = load_settings()
        assert settings.left.path == str(tmp_path)
        assert settings.right.path == str(tmp_path)
        assert settings.right.sort_key == "name"
        assert settings.right.descending is False
        assert settings.right.history == [str(tmp_path)]
        assert settings.right.history_index == 0
        assert settings.active == "left"
        assert settings.show_hidden is False
        assert settings.geometry is None

    def test_load_settings_nonexistent_path_falls_back(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        path = config_path()
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"left": {"path": str(tmp_path / "nope")}}), encoding="utf-8")
        assert load_settings().left.path == str(tmp_path)

    def test_load_settings_blank_path_falls_back(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        path = config_path()
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"left": {"path": "  "}, "right": {"path": 3}}), encoding="utf-8")
        settings = load_settings()
        assert settings.left.path == str(tmp_path)
        assert settings.right.path == str(tmp_path)

    def test_resolve_geometry(self) -> None:
        assert resolve_geometry("1000x700+10+20", 1920, 1080) == "1000x700+10+20"
        assert resolve_geometry("bad", 1920, 1080) == DEFAULT_GEOMETRY
        tiny = resolve_geometry(None, 800, 600)
        assert "800x600" in tiny


class TestTabHelpers:
    """标签下标夹取与标签标题。"""

    def test_normalize_tab_index_clamps(self) -> None:
        assert normalize_tab_index(0, 3) == 0
        assert normalize_tab_index(2, 3) == 2
        assert normalize_tab_index(9, 3) == 2
        assert normalize_tab_index(-1, 3) == 0

    def test_normalize_tab_index_rejects_non_int(self) -> None:
        assert normalize_tab_index("1", 3) == 0
        assert normalize_tab_index(None, 3) == 0
        assert normalize_tab_index(True, 3) == 0
        assert normalize_tab_index(0, 0) == 0

    def test_panel_tab_title_uses_last_component(self) -> None:
        assert panel_tab_title("/root/fcmd") == "fcmd"
        assert panel_tab_title("/root/fcmd/") == "fcmd"
        assert panel_tab_title("fcmd") == "fcmd"
        assert panel_tab_title("/") == "/"
        assert panel_tab_title("") == ""


# ============================================================================ #
# 历史与路径
# ============================================================================ #
class TestHistoryAndPaths:
    def test_push_history_appends(self) -> None:
        history, index = push_history([], -1, "/a")
        assert history == ["/a"]
        assert index == 0
        history, index = push_history(history, index, "/b")
        assert history == ["/a", "/b"]
        assert index == 1

    def test_push_history_same_path_is_noop(self) -> None:
        assert push_history(["/a", "/b"], 1, "/b") == (["/a", "/b"], 1)

    def test_push_history_truncates_forward_branch(self) -> None:
        history, index = push_history(["/a", "/b", "/c"], 0, "/d")
        assert history == ["/a", "/d"]
        assert index == 1

    def test_push_history_dedupes_tail(self) -> None:
        history, index = push_history(["/a", "/b"], -1, "/b")
        assert history == ["/a", "/b"]
        assert index == 1

    def test_push_history_respects_limit(self) -> None:
        history = [f"/{index}" for index in range(MAX_HISTORY)]
        updated, index = push_history(history, len(history) - 1, "/new")
        assert len(updated) == MAX_HISTORY
        assert updated[-1] == "/new"
        assert index == MAX_HISTORY - 1

    def test_history_navigation(self) -> None:
        history = ["/a", "/b", "/c"]
        assert history_back(history, 2) == 1
        assert history_back(history, 0) == 0
        assert history_forward(history, 1) == 2
        assert history_forward(history, 2) == 2

    def test_parent_directory(self, tmp_path: Path) -> None:
        assert parent_directory(tmp_path) == tmp_path.parent
        assert parent_directory(Path(tmp_path.anchor)) is None

    def test_resolve_path_input_directory(self, tmp_path: Path) -> None:
        assert resolve_path_input(str(tmp_path)) == (tmp_path, None)

    def test_resolve_path_input_file(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        assert resolve_path_input(str(target)) == (tmp_path, "a.txt")

    def test_resolve_path_input_relative(self, tmp_path: Path) -> None:
        child = tmp_path / "child"
        child.mkdir()
        assert resolve_path_input("child", base=tmp_path) == (child, None)

    def test_resolve_path_input_errors(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            resolve_path_input("   ")
        with pytest.raises(FileNotFoundError):
            resolve_path_input(str(tmp_path / "missing"))

    def test_is_within(self, tmp_path: Path) -> None:
        child = tmp_path / "a" / "b"
        child.mkdir(parents=True)
        assert is_within(child, tmp_path) is True
        assert is_within(tmp_path, child) is False


# ============================================================================ #
# 目录读取与展示
# ============================================================================ #
def _entry(
    name: str,
    *,
    is_dir: bool = False,
    size: int = 0,
    mtime: float = 0.0,
    suffix: str | None = None,
) -> FileEntry:
    """构造测试用条目。"""
    path = Path("/tmp") / f"{name}{suffix or ''}"
    return FileEntry(name=name, path=path, is_dir=is_dir, size=size, mtime=mtime)


class TestListingAndFormatting:
    def test_list_directory_hides_dotfiles(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("abc", encoding="utf-8")
        (tmp_path / ".hidden").write_text("x", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        names = [entry.name for entry in list_directory(tmp_path)]
        assert names == ["sub", "a.txt"]
        names_all = [entry.name for entry in list_directory(tmp_path, show_hidden=True)]
        assert ".hidden" in names_all

    def test_list_directory_sizes_and_flags(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("abc", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        entries = {entry.name: entry for entry in list_directory(tmp_path)}
        assert entries["sub"].is_dir is True
        assert entries["sub"].size == 0
        assert entries["a.txt"].size == 3
        assert entries["a.txt"].mtime > 0
        assert entries["a.txt"].is_symlink is False

    def test_list_directory_missing(self, tmp_path: Path) -> None:
        with pytest.raises(NotADirectoryError):
            list_directory(tmp_path / "missing")

    @pytest.mark.skipif(os.name == "nt", reason="Windows 创建符号链接需要额外权限")
    def test_list_directory_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "target.txt"
        target.write_text("x", encoding="utf-8")
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        entry = next(item for item in list_directory(tmp_path) if item.name == "link.txt")
        assert entry.is_symlink is True

    @pytest.mark.skipif(os.name == "nt", reason="Windows 创建符号链接需要额外权限")
    def test_list_directory_broken_symlink(self, tmp_path: Path) -> None:
        link = tmp_path / "broken.txt"
        link.symlink_to(tmp_path / "missing.txt")
        entry = next(item for item in list_directory(tmp_path) if item.name == "broken.txt")
        assert entry.size == 0
        assert entry.mtime == 0

    def test_sort_entries_keys(self) -> None:
        entries = [
            _entry("b.txt", size=2, mtime=2.0, suffix=".txt"),
            _entry("a.py", size=3, mtime=1.0, suffix=".py"),
            _entry("dir", is_dir=True),
        ]
        assert [entry.name for entry in sort_entries(entries)] == ["dir", "a.py", "b.txt"]
        assert [entry.name for entry in sort_entries(entries, sort_key="size")] == ["dir", "b.txt", "a.py"]
        assert [entry.name for entry in sort_entries(entries, sort_key="mtime")] == ["dir", "a.py", "b.txt"]
        assert [entry.name for entry in sort_entries(entries, sort_key="type")] == ["dir", "a.py", "b.txt"]
        assert [entry.name for entry in sort_entries(entries, sort_key="size", descending=True)] == [
            "dir",
            "a.py",
            "b.txt",
        ]
        assert [entry.name for entry in sort_entries(entries, sort_key="bad")] == ["dir", "a.py", "b.txt"]

    def test_format_size(self) -> None:
        assert format_size(0) == "0 B"
        assert format_size(512) == "512 B"
        assert format_size(1024) == "1 KB"
        assert format_size(1536) == "1.5 KB"
        assert format_size(1024**2) == "1 MB"
        assert format_size(1024**3) == "1 GB"
        assert format_size(1024**6) == "1024 PB"
        assert format_size(0, is_dir=True) == "<DIR>"

    def test_format_mtime(self) -> None:
        assert format_mtime(0) == ""
        assert format_mtime(-1) == ""
        assert len(format_mtime(1_700_000_000.0)) == 16

    def test_is_hidden(self) -> None:
        assert is_hidden(".gitignore") is True
        assert is_hidden("a.txt") is False
        hidden_stat = cast("os.stat_result", SimpleNamespace(st_file_attributes=0x2))
        visible_stat = cast("os.stat_result", SimpleNamespace(st_file_attributes=0))
        assert is_hidden("a.txt", hidden_stat) is True
        assert is_hidden("a.txt", visible_stat) is False

    def test_describe_attributes(self) -> None:
        assert describe_attributes(_entry("d", is_dir=True)) == "DIR"
        assert describe_attributes(_entry("f.txt")) == "FILE"
        hidden = FileEntry(name=".x", path=Path("/tmp/.x"), hidden=True)
        assert describe_attributes(hidden) == "FILE H"
        link = FileEntry(name="l", path=Path("/tmp/l"), is_symlink=True)
        assert describe_attributes(link) == "LINK"

    def test_describe_entry(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("abc", encoding="utf-8")
        entry = next(item for item in list_directory(tmp_path) if item.name == "a.txt")
        text = describe_entry(entry)
        assert "名称: a.txt" in text
        assert "类型: 文件" in text
        assert "大小: 3 B" in text

    def test_describe_entry_directory(self, tmp_path: Path) -> None:
        entry = FileEntry(name="sub", path=tmp_path / "sub", is_dir=True)
        assert "类型: 文件夹" in describe_entry(entry)
        assert "大小: <DIR>" in describe_entry(entry)

    @pytest.mark.skipif(os.name == "nt", reason="Windows 创建符号链接需要额外权限")
    def test_describe_entry_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "target.txt"
        target.write_text("x", encoding="utf-8")
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        entry = next(item for item in list_directory(tmp_path) if item.name == "link.txt")
        assert "类型: 符号链接" in describe_entry(entry)


# ============================================================================ #
# 基础文件操作
# ============================================================================ #
class TestFileOperations:
    def test_validate_name(self) -> None:
        assert validate_name("  a.txt  ") == "a.txt"
        for bad in ("", "   ", ".", "..", "a/b", "a\\b", "a<b", "a:b", 'a"b', "a|b", "a?b", "a*b", "a\x00b"):
            with pytest.raises(ValueError):
                validate_name(bad)

    def test_unique_path_file(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        assert unique_path(target).name == "a (2).txt"
        (tmp_path / "a (2).txt").write_text("x", encoding="utf-8")
        assert unique_path(target).name == "a (3).txt"
        assert unique_path(tmp_path / "new.txt").name == "new.txt"

    def test_unique_path_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "dir.name"
        target.mkdir()
        assert unique_path(target).name == "dir.name (2)"

    def test_create_directory(self, tmp_path: Path) -> None:
        target = create_directory(tmp_path, "new")
        assert target.is_dir()
        with pytest.raises(FileExistsError):
            create_directory(tmp_path, "new")

    def test_rename_entry(self, tmp_path: Path) -> None:
        source = tmp_path / "a.txt"
        source.write_text("x", encoding="utf-8")
        target = rename_entry(source, "b.txt")
        assert target.name == "b.txt"
        assert target.read_text(encoding="utf-8") == "x"
        assert rename_entry(target, "b.txt") == target
        other = tmp_path / "c.txt"
        other.write_text("y", encoding="utf-8")
        with pytest.raises(FileExistsError):
            rename_entry(target, "c.txt")

    def test_copy_entry_file_and_directory(self, tmp_path: Path) -> None:
        source = tmp_path / "a.txt"
        source.write_text("x", encoding="utf-8")
        copied = tmp_path / "copy.txt"
        copy_entry(source, copied)
        assert copied.read_text(encoding="utf-8") == "x"
        with pytest.raises(FileExistsError):
            copy_entry(source, copied)

        folder = tmp_path / "folder"
        folder.mkdir()
        (folder / "inner.txt").write_text("y", encoding="utf-8")
        copy_entry(folder, tmp_path / "folder-copy")
        assert (tmp_path / "folder-copy" / "inner.txt").read_text(encoding="utf-8") == "y"

    def test_copy_entry_missing(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            copy_entry(tmp_path / "missing", tmp_path / "target")

    def test_move_entry(self, tmp_path: Path) -> None:
        source = tmp_path / "a.txt"
        source.write_text("x", encoding="utf-8")
        target = tmp_path / "b.txt"
        move_entry(source, target)
        assert target.exists()
        assert not source.exists()
        with pytest.raises(FileExistsError):
            move_entry(target, target)

    def test_delete_entry(self, tmp_path: Path) -> None:
        file_path = tmp_path / "a.txt"
        file_path.write_text("x", encoding="utf-8")
        delete_entry(file_path)
        assert not file_path.exists()
        folder = tmp_path / "folder"
        folder.mkdir()
        (folder / "inner.txt").write_text("x", encoding="utf-8")
        delete_entry(folder)
        assert not folder.exists()

    def test_delete_entry_missing(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            delete_entry(tmp_path / "missing")

    def test_delete_entry_rejects_root(self) -> None:
        with pytest.raises(ValueError):
            delete_entry(Path(Path.cwd().anchor))


# ============================================================================ #
# 批量复制 / 移动 / 删除
# ============================================================================ #
class TestTransfer:
    def test_plan_transfer_same_directory_skips(self, tmp_path: Path) -> None:
        source = tmp_path / "a.txt"
        source.write_text("x", encoding="utf-8")
        items, skipped = plan_transfer([source], tmp_path)
        assert items == []
        assert skipped == [source]

    def test_plan_transfer_inside_source_skips(self, tmp_path: Path) -> None:
        folder = tmp_path / "folder"
        child = folder / "child"
        child.mkdir(parents=True)
        items, skipped = plan_transfer([folder], child)
        assert items == []
        assert skipped == [folder]

    def test_plan_transfer_conflict_modes(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        source_file = source / "a.txt"
        source_file.write_text("new", encoding="utf-8")
        destination = tmp_path / "dst"
        destination.mkdir()
        (destination / "a.txt").write_text("old", encoding="utf-8")

        items, skipped = plan_transfer([source_file], destination, conflict="rename")
        assert skipped == []
        assert items[0].target.name == "a (2).txt"
        assert items[0].overwrite is False

        items, _ = plan_transfer([source_file], destination, conflict="overwrite")
        assert items[0].target == destination / "a.txt"
        assert items[0].overwrite is True

        items, skipped = plan_transfer([source_file], destination, conflict="skip")
        assert items == []
        assert skipped == [source_file]

    def test_plan_transfer_ask_chooser(self, tmp_path: Path) -> None:
        source = tmp_path / "a.txt"
        source.write_text("new", encoding="utf-8")
        destination = tmp_path / "dst"
        destination.mkdir()
        target = destination / "a.txt"
        target.write_text("old", encoding="utf-8")

        def chooser(seen: Path) -> str:
            assert seen == target
            return "overwrite"

        items, _ = plan_transfer([source], destination, conflict="ask", chooser=chooser)
        assert items[0].overwrite is True

        items, _ = plan_transfer([source], destination, conflict="ask")
        assert items[0].target.name == "a (2).txt"

        with pytest.raises(ValueError):
            plan_transfer([source], destination, conflict="unknown")

    def test_plan_transfer_requires_directory(self, tmp_path: Path) -> None:
        with pytest.raises(NotADirectoryError):
            plan_transfer([], tmp_path / "missing")

    def test_plan_transfer_without_conflict(self, tmp_path: Path) -> None:
        source = tmp_path / "a.txt"
        source.write_text("x", encoding="utf-8")
        destination = tmp_path / "dst"
        destination.mkdir()
        items, skipped = plan_transfer([source], destination)
        assert skipped == []
        assert items[0].target == destination / "a.txt"
        assert items[0].overwrite is False

    def test_transfer_entries_copy_and_progress(self, tmp_path: Path) -> None:
        source = tmp_path / "a.txt"
        source.write_text("x", encoding="utf-8")
        target = tmp_path / "dst" / "a.txt"
        target.parent.mkdir()
        events: list[tuple[int, int, str]] = []
        report = transfer_entries(
            [TransferItem(source=source, target=target)],
            progress=lambda done, total, name: events.append((done, total, name)),
        )
        assert report.completed == [target]
        assert report.errors == []
        assert events == [(1, 1, "a.txt")]
        assert target.read_text(encoding="utf-8") == "x"

    def test_transfer_entries_overwrite(self, tmp_path: Path) -> None:
        source = tmp_path / "src" / "a.txt"
        source.parent.mkdir()
        source.write_text("new", encoding="utf-8")
        target = tmp_path / "dst" / "a.txt"
        target.parent.mkdir()
        target.write_text("old", encoding="utf-8")
        report = transfer_entries([TransferItem(source=source, target=target, overwrite=True)])
        assert report.completed == [target]
        assert target.read_text(encoding="utf-8") == "new"

    def test_transfer_entries_reports_errors(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing.txt"
        target = tmp_path / "dst.txt"
        report = transfer_entries([TransferItem(source=missing, target=target)])
        assert report.errors
        assert report.completed == []

    def test_transfer_entries_existing_without_overwrite(self, tmp_path: Path) -> None:
        source = tmp_path / "src.txt"
        source.write_text("new", encoding="utf-8")
        target = tmp_path / "dst.txt"
        target.write_text("old", encoding="utf-8")
        report = transfer_entries([TransferItem(source=source, target=target)])
        assert report.errors
        assert target.read_text(encoding="utf-8") == "old"

    def test_transfer_entries_move(self, tmp_path: Path) -> None:
        source = tmp_path / "a.txt"
        source.write_text("x", encoding="utf-8")
        target = tmp_path / "dst" / "a.txt"
        target.parent.mkdir()
        report = transfer_entries([TransferItem(source=source, target=target)], move=True)
        assert report.completed == [target]
        assert not source.exists()

    def test_delete_entries(self, tmp_path: Path) -> None:
        first = tmp_path / "a.txt"
        first.write_text("x", encoding="utf-8")
        second = tmp_path / "missing.txt"
        events: list[tuple[int, int, str]] = []
        report = delete_entries(
            [first, second],
            progress=lambda done, total, name: events.append((done, total, name)),
        )
        assert report.completed == [first]
        assert report.errors and report.errors[0][0] == second
        assert events == [(1, 2, "a.txt"), (2, 2, "missing.txt")]


# ============================================================================ #
# 打包 / 解压
# ============================================================================ #
class TestArchive:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("a.zip", "a"),
            ("a.tar.gz", "a"),
            ("a.tgz", "a"),
            ("a.txt", "a.txt"),
            (".zip", ".zip"),
        ],
    )
    def test_archive_stem(self, name: str, expected: str) -> None:
        assert archive_stem(name) == expected

    def test_default_extract_dir(self, tmp_path: Path) -> None:
        assert default_extract_dir(Path("/a/b/demo.tar.gz"), tmp_path) == tmp_path / "demo"

    def test_default_archive_name(self, tmp_path: Path) -> None:
        one = [tmp_path / "a.txt"]
        assert default_archive_name(one) == "a.txt.zip"
        multiple = [tmp_path / "x" / "a.txt", tmp_path / "x" / "b.txt"]
        assert default_archive_name(multiple) == "x.zip"
        assert default_archive_name([]) == "archive.zip"
        assert default_archive_name([Path("/") / "a.txt"]) == "a.txt.zip"

    def test_archive_suffixes_has_common_formats(self) -> None:
        assert ".zip" in ARCHIVE_SUFFIXES
        assert ".tar.gz" in ARCHIVE_SUFFIXES

    def test_pack_single_file(self, tmp_path: Path) -> None:
        source = tmp_path / "a.txt"
        source.write_text("hello", encoding="utf-8")
        output = tmp_path / "a.zip"
        events: list[tuple[int, int, str]] = []
        result = pack_entries([source], output, progress=lambda d, t, n: events.append((d, t, n)))
        assert result == output
        assert events == [(1, 1, "a.zip")]
        with zipfile.ZipFile(output) as archive:
            assert archive.namelist() == ["a.txt"]

    def test_pack_single_directory_skips_ignored(self, tmp_path: Path) -> None:
        folder = tmp_path / "folder"
        (folder / "sub").mkdir(parents=True)
        (folder / "a.txt").write_text("hello", encoding="utf-8")
        (folder / "a.pyc").write_bytes(b"cache")
        output = tmp_path / "folder.zip"
        pack_entries([folder], output)
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
        assert "a.txt" in names
        assert "a.pyc" not in names

    def test_pack_multiple_entries_zip(self, tmp_path: Path) -> None:
        first = tmp_path / "a.txt"
        first.write_text("a", encoding="utf-8")
        second = tmp_path / "b.txt"
        second.write_text("b", encoding="utf-8")
        ignored = tmp_path / "chosen.pyc"
        ignored.write_bytes(b"cache")
        folder = tmp_path / "sub"
        (folder / "empty").mkdir(parents=True)
        (folder / "inner.txt").write_text("c", encoding="utf-8")
        (folder / ".git").mkdir()
        (folder / ".git" / "config").write_text("x", encoding="utf-8")
        output = tmp_path / "bundle.zip"
        pack_entries([first, second, ignored, folder], output)
        with zipfile.ZipFile(output) as archive:
            names = set(archive.namelist())
        assert {"a.txt", "b.txt", "sub/empty/", "sub/inner.txt"} <= names
        assert "chosen.pyc" not in names
        assert "sub/.git/config" not in names

    def test_pack_multiple_requires_zip(self, tmp_path: Path) -> None:
        first = tmp_path / "a.txt"
        first.write_text("a", encoding="utf-8")
        second = tmp_path / "b.txt"
        second.write_text("b", encoding="utf-8")
        with pytest.raises(ValueError):
            pack_entries([first, second], tmp_path / "bundle.tar.gz")

    def test_pack_multiple_requires_same_parent(self, tmp_path: Path) -> None:
        first = tmp_path / "a" / "a.txt"
        second = tmp_path / "b" / "b.txt"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_text("a", encoding="utf-8")
        second.write_text("b", encoding="utf-8")
        with pytest.raises(ValueError):
            pack_entries([first, second], tmp_path / "bundle.zip")

    def test_pack_errors(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            pack_entries([], tmp_path / "a.zip")
        with pytest.raises(FileNotFoundError):
            pack_entries([tmp_path / "missing.txt"], tmp_path / "a.zip")

    def test_extract_archive_to(self, tmp_path: Path) -> None:
        source = tmp_path / "a.txt"
        source.write_text("hello", encoding="utf-8")
        archive = tmp_path / "a.zip"
        pack_entries([source], archive)
        destination = tmp_path / "out"
        events: list[tuple[int, int, str]] = []
        result = extract_archive_to(
            archive,
            destination,
            progress=lambda done, total, name: events.append((done, total, name)),
        )
        assert result == destination
        assert (destination / "a.txt").read_text(encoding="utf-8") == "hello"
        assert events == [(1, 1, "a.zip")]

    def test_extract_archive_missing(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            extract_archive_to(tmp_path / "missing.zip", tmp_path / "out")


# ============================================================================ #
# 哈希 / 搜索 / 预览
# ============================================================================ #
class TestHashSearchPreview:
    def test_hash_paths_file(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("hello", encoding="utf-8")
        events: list[tuple[int, int, str]] = []
        results = hash_paths([target], "sha256", progress=lambda d, t, n: events.append((d, t, n)))
        assert results[0].digest == hashlib.sha256(b"hello").hexdigest()
        assert events == [(1, 1, "a.txt")]

    def test_hash_paths_directory(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "b.txt").write_text("b", encoding="utf-8")
        (tmp_path / "ignored.zip").write_text("x", encoding="utf-8")
        results = hash_paths([tmp_path], "md5")
        assert {result.path.name for result in results} == {"a.txt", "b.txt"}

    def test_hash_paths_invalid_algorithm(self) -> None:
        with pytest.raises(ValueError):
            hash_paths([], "nope")

    def test_format_hash_report(self, tmp_path: Path) -> None:
        results = [cmderw.HashResult(path=tmp_path / "a.txt", digest="abc")]
        assert format_hash_report(results, "sha256") == f"sha256  abc  {tmp_path / 'a.txt'}"

    def test_search_paths_by_name(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("needle", encoding="utf-8")
        (tmp_path / "b.py").write_text("x", encoding="utf-8")
        hits = search_paths(tmp_path, "*.txt")
        assert [hit.path.name for hit in hits] == ["a.txt"]
        assert hits[0].line == 0

    def test_search_paths_by_content(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("first\nneedle here\n", encoding="utf-8")
        hits = search_paths(tmp_path, "needle", content=True, extension="txt")
        assert len(hits) == 1
        assert hits[0].path.name == "a.txt"
        assert hits[0].line == 2
        assert "needle" in hits[0].text
        dotted = search_paths(tmp_path, "needle", content=True, extension=".txt")
        assert len(dotted) == 1
        without_extension = search_paths(tmp_path, "needle", content=True)
        assert len(without_extension) == 1

    def test_search_paths_errors(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            search_paths(tmp_path, "  ")
        with pytest.raises(NotADirectoryError):
            search_paths(tmp_path / "missing", "x")

    def test_format_search_hit(self, tmp_path: Path) -> None:
        path = tmp_path / "a.txt"
        assert format_search_hit(SearchHit(path=path)) == str(path)
        assert format_search_hit(SearchHit(path=path, line=3, text="needle")) == f"{path}:3: needle"

    def test_load_preview_text(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("hello\nworld\n", encoding="utf-8")
        assert load_preview(target) == "hello\nworld\n"

    def test_load_preview_truncates(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("".join(f"line {index}\n" for index in range(20)), encoding="utf-8")
        preview = load_preview(target, max_lines=5)
        assert "line 4" in preview
        assert "line 5" not in preview
        assert "仅显示前 5 行" in preview

    def test_load_preview_binary(self, tmp_path: Path) -> None:
        target = tmp_path / "a.bin"
        target.write_bytes(b"\x00\x01\x02")
        with pytest.raises(ValueError):
            load_preview(target)


# ============================================================================ #
# 系统默认程序打开
# ============================================================================ #
class TestOpenExternally:
    def test_windows_uses_startfile(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        calls: list[str] = []
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(os, "startfile", calls.append, raising=False)
        open_externally(tmp_path / "a.txt")
        assert calls == [str(tmp_path / "a.txt")]

    @pytest.mark.parametrize(("platform", "command"), [("darwin", "open"), ("linux", "xdg-open")])
    def test_posix_uses_default_opener(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        platform: str,
        command: str,
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(sys, "platform", platform)
        monkeypatch.setattr(cmderw.subprocess, "Popen", lambda args, **_kwargs: calls.append(args))
        open_externally(tmp_path / "a.txt")
        assert calls == [[command, str(tmp_path / "a.txt")]]
