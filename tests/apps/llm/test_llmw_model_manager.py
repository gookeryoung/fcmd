"""多模型目录下的「模型管理」：空目录不遮挡其它目录的模型。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox

from fcmd.apps.llm._llmw_qt_model_manager import ModelManagerDialog, scan_managed_models


def _model(root: Path, org: str, name: str) -> Path:
    directory = root / org / name
    directory.mkdir(parents=True)
    (directory / "model.gguf").write_bytes(b"x")
    return directory


def test_scan_managed_models_skips_empty_root_and_dedupes(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    models = tmp_path / "models"
    first = _model(models, "Qwen", "demo")
    _model(models, "Qwen", "other")
    # 同一仓库重复出现在第二个根目录时只保留首个。
    second_root = tmp_path / "models2"
    _model(second_root, "Qwen", "other")
    found = scan_managed_models([empty, models, second_root])
    assert [item.repo for item in found] == ["Qwen/demo", "Qwen/other"]
    assert found[0].path == first
    assert found[0].root == models


def test_dialog_aggregates_all_roots_and_removes_from_actual_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = QApplication.instance() or QApplication([])
    empty = tmp_path / "empty"
    empty.mkdir()
    models = tmp_path / "models"
    first = _model(models, "Qwen", "first")
    second = _model(models, "Qwen", "second")
    dialog = ModelManagerDialog([empty, models])
    try:
        dialog._render(scan_managed_models(dialog.models_roots))
        app.processEvents()
        # 空目录作为主目录时，模型管理仍显示其它目录里的模型。
        assert dialog.models_root == empty
        assert dialog._table.rowCount() == 2
        assert dialog._table.item(0, 4).text() == str(first)
        monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
        dialog._table.selectRow(0)
        dialog._remove_selected()
        assert not first.exists()
        assert second.exists()
    finally:
        dialog.deleteLater()
