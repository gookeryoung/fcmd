"""infras.jsonio 的读写与失败清理测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.infras.jsonio import read_json_file, write_json_atomic, write_text_atomic


def test_read_json_file(tmp_path: Path) -> None:
    """读取 UTF-8 JSON 并保留中文。"""
    path = tmp_path / "data.json"
    path.write_text('{"name": "模型"}', encoding="utf-8")
    assert read_json_file(path) == {"name": "模型"}


def test_read_json_file_missing(tmp_path: Path) -> None:
    """缺失文件抛出 ``FileNotFoundError``，由调用方决定降级。"""
    with pytest.raises(FileNotFoundError):
        read_json_file(tmp_path / "missing.json")


def test_read_json_file_invalid(tmp_path: Path) -> None:
    """内容非法时抛出 ``ValueError``。"""
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        read_json_file(path)


def test_write_json_atomic_creates_parent(tmp_path: Path) -> None:
    """默认缩进 2 空格、末尾补换行，并自动创建父目录。"""
    path = tmp_path / "nested" / "data.json"
    write_json_atomic(path, {"name": "模型"})
    assert path.read_text(encoding="utf-8") == '{\n  "name": "模型"\n}\n'


def test_write_json_atomic_options(tmp_path: Path) -> None:
    """可关闭缩进并按键排序，用于缓存类文件。"""
    path = tmp_path / "cache.json"
    write_json_atomic(path, {"b": 2, "a": 1}, indent=None, sort_keys=True)
    assert path.read_text(encoding="utf-8") == '{"a": 1, "b": 2}\n'


def test_write_json_atomic_compact_separators(tmp_path: Path) -> None:
    """紧凑分隔符用于列表缓存。"""
    path = tmp_path / "cache.json"
    write_json_atomic(path, {"a": [1, 2]}, indent=None, separators=(",", ":"))
    assert path.read_text(encoding="utf-8") == '{"a":[1,2]}\n'


def test_write_text_atomic_cleans_tmp_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """替换失败时删除临时文件，不留下半成品。"""

    def raise_oserror(self: Path, target: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", raise_oserror)
    with pytest.raises(OSError):
        write_text_atomic(tmp_path / "data.json", "{}")
    assert list(tmp_path.iterdir()) == []
