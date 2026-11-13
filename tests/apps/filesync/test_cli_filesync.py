"""filesync CLI 测试：注册、比较输出与三种同步模式的实际落盘行为。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fcmd.apps.filesync import filesync
from fcmd.apps.filesync._core import (
    CompareOptions,
    CompareResult,
    ScanError,
)
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool
from tests.helpers import run_and_capture as _run_and_capture


def _write(root: Path, relative: str, data: bytes = b"x", mtime: float | None = None) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _pair(tmp_path: Path) -> tuple[Path, Path]:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    return left, right


def test_tool_registration() -> None:
    """filesync 注册 compare / sync 两个子命令。"""
    assert set(_TOOL_REGISTRY["filesync"]) == {"compare", "sync"}


def test_compare_command_lists_differences(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    left, right = _pair(tmp_path)
    _write(left, "only-left.txt", b"a")
    _write(right, "only-right.txt", b"b")
    _write(left, "same.txt", b"same")
    _write(right, "same.txt", b"same")
    filesync.compare_command(left, right)
    output = capsys.readouterr().out
    assert "仅左侧" in output
    assert "仅右侧" in output
    assert "only-left.txt" in output
    assert "same.txt" not in output


def test_compare_command_show_equal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    left, right = _pair(tmp_path)
    _write(left, "same.txt", b"same")
    _write(right, "same.txt", b"same")
    filesync.compare_command(left, right, show_equal=True)
    assert "same.txt" in capsys.readouterr().out


def test_compare_command_rejects_missing_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    left, right = _pair(tmp_path)
    filesync.compare_command(tmp_path / "missing", right)
    assert "左侧目录不存在" in capsys.readouterr().out
    filesync.compare_command(left, tmp_path / "missing")
    assert "右侧目录不存在" in capsys.readouterr().out


def test_compare_command_rejects_same_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    left, _right = _pair(tmp_path)
    filesync.compare_command(left, left)
    assert "不能相同" in capsys.readouterr().out


def test_sync_mirror_copies_and_deletes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    left, right = _pair(tmp_path)
    _write(left, "copy.txt", b"payload")
    _write(right, "remove.txt", b"x")
    filesync.sync_command(left, right, sync_mode="mirror")
    output = capsys.readouterr().out
    assert (right / "copy.txt").read_bytes() == b"payload"
    assert not (right / "remove.txt").exists()
    assert "计划：复制 1 项" in output
    assert "删除 1 项" in output
    assert "同步完成：复制 1 项" in output


def test_sync_dry_run_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    left, right = _pair(tmp_path)
    _write(left, "copy.txt", b"payload")
    filesync.sync_command(left, right, sync_mode="mirror", preview=True)
    output = capsys.readouterr().out
    assert not (right / "copy.txt").exists()
    assert "试运行：复制 1 项" in output


def test_sync_update_keeps_right_only_files(tmp_path: Path) -> None:
    left, right = _pair(tmp_path)
    _write(left, "left.txt", b"a")
    _write(right, "right.txt", b"b")
    filesync.sync_command(left, right, sync_mode="update")
    assert (right / "left.txt").read_bytes() == b"a"
    assert (right / "right.txt").exists()


def test_sync_two_way_copies_right_to_left(tmp_path: Path) -> None:
    left, right = _pair(tmp_path)
    _write(right, "new.txt", b"right")
    filesync.sync_command(left, right)
    assert (left / "new.txt").read_bytes() == b"right"


def test_sync_two_way_conflict_is_skipped(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    left, right = _pair(tmp_path)
    _write(left, "conflict.txt", b"abc", mtime=1000)
    _write(right, "conflict.txt", b"xy", mtime=1000)
    filesync.sync_command(left, right)
    output = capsys.readouterr().out
    assert "冲突跳过" in output
    assert "没有可执行动作（全部为冲突项）" in output
    assert (left / "conflict.txt").read_bytes() == b"abc"
    assert (right / "conflict.txt").read_bytes() == b"xy"


def test_sync_with_include_filter(tmp_path: Path) -> None:
    left, right = _pair(tmp_path)
    _write(left, "keep.py", b"py")
    _write(left, "skip.txt", b"txt")
    filesync.sync_command(left, right, sync_mode="mirror", include="*.py")
    assert (right / "keep.py").exists()
    assert not (right / "skip.txt").exists()


def test_sync_no_differences(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    left, right = _pair(tmp_path)
    _write(left, "same.txt", b"same")
    _write(right, "same.txt", b"same")
    filesync.sync_command(left, right)
    assert "没有需要同步的项" in capsys.readouterr().out


def test_sync_prints_scan_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """扫描错误即使没有动作也单独输出。"""
    left, right = _pair(tmp_path)
    result = CompareResult(
        left=left,
        right=right,
        errors=[ScanError("locked", "permission denied", "left")],
        options=CompareOptions(),
    )
    monkeypatch.setattr(filesync, "compare_trees", lambda *_args, **_kwargs: result)
    filesync.sync_command(left, right)
    output = capsys.readouterr().out
    assert "扫描错误 1 项" in output
    assert "左侧 locked" in output


def test_cli_entry_compare(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """通过 run_tool 走完整参数解析链路。"""
    left, right = _pair(tmp_path)
    _write(left, "only.txt", b"a")
    code = run_tool("filesync", ["compare", str(left), str(right)])
    assert code == 0
    assert "only.txt" in capsys.readouterr().out


def test_cli_entry_sync_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    left, right = _pair(tmp_path)
    _write(left, "only.txt", b"a")
    code = run_tool("filesync", ["sync", str(left), str(right), "--sync-mode", "mirror", "--preview"])
    assert code == 0
    assert not (right / "only.txt").exists()
    assert "试运行" in capsys.readouterr().out


class TestFilesyncCompareJson:
    """``filesync compare --json``：结构化差异，默认表格文案不变。"""

    def test_compare_json_lists_differences(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """仅左 / 仅右 / 相同分类分别落到各自字段。"""
        left, right = _pair(tmp_path)
        _write(left, "only-left.txt", b"a")
        _write(right, "only-right.txt", b"b")
        _write(left, "same.txt", b"same")
        _write(right, "same.txt", b"same")
        code, out = _run_and_capture("filesync", ["compare", str(left), str(right), "--json"], capsys)
        assert code == 0
        payload = json.loads(out)
        assert payload["schema"] == "fcmd-result/1"
        assert payload["tool"] == "filesync"
        assert payload["subcommand"] == "compare"
        data = payload["data"]
        assert data["left"] == str(left)
        assert data["right"] == str(right)
        assert data["equal"] is False
        assert data["only_left"] == ["only-left.txt"]
        assert data["only_right"] == ["only-right.txt"]
        assert data["different"] == []
        assert data["errors"] == []

    def test_compare_json_different(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """同名但内容不同归入 different。"""
        left, right = _pair(tmp_path)
        _write(left, "same.txt", b"left-content")
        _write(right, "same.txt", b"right")
        _, out = _run_and_capture("filesync", ["compare", str(left), str(right), "--json"], capsys)
        data = json.loads(out)["data"]
        assert data["different"] == ["same.txt"]
        assert data["equal"] is False

    def test_compare_json_equal(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """两侧一致时 equal 为 true。"""
        left, right = _pair(tmp_path)
        _write(left, "same.txt", b"same")
        _write(right, "same.txt", b"same")
        _, out = _run_and_capture("filesync", ["compare", str(left), str(right), "--json"], capsys)
        data = json.loads(out)["data"]
        assert data["equal"] is True
        assert data["only_left"] == data["only_right"] == data["different"] == []

    def test_compare_json_errors(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """扫描错误进入 errors 且使 equal=false。"""
        left, right = _pair(tmp_path)
        result = CompareResult(
            left=left,
            right=right,
            errors=[ScanError("locked", "permission denied", "left")],
            options=CompareOptions(),
        )
        monkeypatch.setattr(filesync, "compare_trees", lambda *_args, **_kwargs: result)
        filesync.compare_command(left, right, json_output=True)
        data = json.loads(capsys.readouterr().out)["data"]
        assert data["equal"] is False
        assert data["errors"] == [{"path": "locked", "error": "permission denied"}]

    def test_compare_json_left_missing(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--json 下左侧目录不存在：DIR_NOT_FOUND(field=left)。"""
        _, right = _pair(tmp_path)
        filesync.compare_command(tmp_path / "missing", right, json_output=True)
        captured = capsys.readouterr()
        assert captured.out == ""
        payload = json.loads(captured.err)
        assert payload["code"] == "DIR_NOT_FOUND"
        assert payload["field"] == "left"

    def test_compare_json_right_missing(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--json 下右侧目录不存在：DIR_NOT_FOUND(field=right)。"""
        left, _ = _pair(tmp_path)
        filesync.compare_command(left, tmp_path / "missing", json_output=True)
        payload = json.loads(capsys.readouterr().err)
        assert payload["code"] == "DIR_NOT_FOUND"
        assert payload["field"] == "right"

    def test_compare_json_same_directory(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--json 下左右相同：SAME_DIRECTORY。"""
        left, _ = _pair(tmp_path)
        filesync.compare_command(left, left, json_output=True)
        payload = json.loads(capsys.readouterr().err)
        assert payload["code"] == "SAME_DIRECTORY"
        assert payload["field"] == "right"
