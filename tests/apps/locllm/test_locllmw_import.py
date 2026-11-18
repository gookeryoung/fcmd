"""locllmw 模型导入：GGUF / 文件夹落盘位置与覆盖语义。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apps.locllm._locllmw_import import FolderKind, copy_import, inspect_folder, plan_import


def _gguf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF")
    return path


def test_plan_single_gguf_uses_stem_folder(tmp_path: Path) -> None:
    source = _gguf(tmp_path / "downloads" / "demo-Q4_K_M.gguf")
    plan = plan_import([source], tmp_path / "models")[0]
    assert plan.target == tmp_path / "models" / "demo-Q4_K_M" / "demo-Q4_K_M.gguf"
    assert not plan.is_dir


def test_plan_folder_keeps_folder_name(tmp_path: Path) -> None:
    source = tmp_path / "downloads" / "org-model"
    (source / "sub").mkdir(parents=True)
    plan = plan_import([source], tmp_path / "models")[0]
    assert plan.target == tmp_path / "models" / "org-model"
    assert plan.is_dir


def test_plan_rejects_non_gguf_file(tmp_path: Path) -> None:
    source = tmp_path / "model.bin"
    source.write_bytes(b"x")
    with pytest.raises(ValueError, match=r"仅支持导入 \.gguf"):
        plan_import([source], tmp_path / "models")


def test_plan_rejects_source_inside_models_root(tmp_path: Path) -> None:
    root = tmp_path / "models"
    source = _gguf(root / "demo" / "demo.gguf")
    with pytest.raises(ValueError, match="已在模型目录中"):
        plan_import([source], root)


def test_copy_file_and_folder(tmp_path: Path) -> None:
    root = tmp_path / "models"
    file_plan = plan_import([_gguf(tmp_path / "a" / "a.gguf")], root)[0]
    assert copy_import(file_plan) == root / "a" / "a.gguf"
    assert (root / "a" / "a.gguf").is_file()

    folder = tmp_path / "b" / "repo"
    (folder / "nested").mkdir(parents=True)
    (folder / "nested" / "model.safetensors").write_bytes(b"x")
    dir_plan = plan_import([folder], root)[0]
    assert copy_import(dir_plan) == root / "repo"
    assert (root / "repo" / "nested" / "model.safetensors").is_file()


def test_copy_requires_overwrite_for_existing_target(tmp_path: Path) -> None:
    root = tmp_path / "models"
    plan = plan_import([_gguf(tmp_path / "a" / "a.gguf")], root)[0]
    copy_import(plan)
    with pytest.raises(FileExistsError):
        copy_import(plan)
    plan.source.write_bytes(b"GGUF2")
    copy_import(plan, overwrite=True)
    assert plan.target.read_bytes() == b"GGUF2"


def test_inspect_folder_detects_gguf(tmp_path: Path) -> None:
    folder = tmp_path / "gguf-model"
    _gguf(folder / "model-Q4_K_M.gguf")
    info = inspect_folder(folder)
    assert info.kind is FolderKind.GGUF
    assert "可直接启动" in info.label
    assert not info.can_convert


def test_inspect_folder_detects_tensors(tmp_path: Path) -> None:
    folder = tmp_path / "hf-model"
    folder.mkdir()
    (folder / "model.safetensors").write_bytes(b"x")
    info = inspect_folder(folder)
    assert info.kind is FolderKind.TENSORS
    assert "tensors" in info.label
    assert info.can_convert


def test_inspect_folder_detects_mixed(tmp_path: Path) -> None:
    folder = tmp_path / "mixed-model"
    folder.mkdir()
    (folder / "model.safetensors").write_bytes(b"x")
    (folder / "model-Q4_K_M.gguf").write_bytes(b"x")
    info = inspect_folder(folder)
    assert info.kind is FolderKind.MIXED
    assert info.can_convert


def test_inspect_folder_detects_diffusers_as_not_convertible(tmp_path: Path) -> None:
    folder = tmp_path / "diffusers-model"
    folder.mkdir()
    (folder / "model_index.json").write_text("{}", encoding="utf-8")
    (folder / "model.safetensors").write_bytes(b"x")
    info = inspect_folder(folder)
    assert info.kind is FolderKind.DIFFUSERS
    assert not info.can_convert


def test_inspect_folder_unknown(tmp_path: Path) -> None:
    folder = tmp_path / "empty"
    folder.mkdir()
    info = inspect_folder(folder)
    assert info.kind is FolderKind.UNKNOWN
    assert not info.can_convert


def test_inspect_folder_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不是有效文件夹"):
        inspect_folder(tmp_path / "missing")
