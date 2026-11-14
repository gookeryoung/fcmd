"""`_locllmw_ui._scan_dir` 的目录扫描分类。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.locllm import _locllmw_ui, llmmodel


def test_scan_dir_reports_missing_directory(tmp_path: Path) -> None:
    entries, issue = _locllmw_ui._scan_dir(tmp_path / "missing")
    assert entries == []
    assert issue == f"模型目录不存在：{tmp_path / 'missing'}"


def test_scan_dir_reports_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_root: Path) -> list[Any]:
        raise PermissionError("denied")

    monkeypatch.setattr(llmmodel, "scan_models", boom)
    entries, issue = _locllmw_ui._scan_dir(tmp_path)
    assert entries == []
    assert issue == f"没有权限读取模型目录：{tmp_path}"


def test_scan_dir_reports_other_os_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_root: Path) -> list[Any]:
        raise OSError("disk error")

    monkeypatch.setattr(llmmodel, "scan_models", boom)
    entries, issue = _locllmw_ui._scan_dir(tmp_path)
    assert entries == []
    assert issue == "扫描模型目录失败：disk error"


def test_scan_dir_leaves_empty_directory_to_caller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llmmodel, "scan_models", lambda _root: [])
    entries, issue = _locllmw_ui._scan_dir(tmp_path)
    assert entries == []
    assert issue is None


def test_scan_dir_returns_entries_without_issue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = ["entry"]
    monkeypatch.setattr(llmmodel, "scan_models", lambda _root: sentinel)
    entries, issue = _locllmw_ui._scan_dir(tmp_path)
    assert entries == sentinel
    assert issue is None
