"""locllmw 模型导入：GGUF 单文件或模型文件夹复制到模型目录。

导入只做「落盘 + 规整目录」，不在这里触发转换 / 量化：

- 单个 ``.gguf`` → ``<models_dir>/<文件名去后缀>/<文件名>``（一级目录布局）；
- 文件夹 → ``<models_dir>/<文件夹名>``，保留内部结构；
- 已在模型目录内的源路径直接拒绝，避免自我复制；
- 覆盖 / 合并同名目标时由调用方先确认，再以 ``overwrite=True`` 传入。

纯逻辑，不导入 Qt；网络 / 转换仍走既有 ``llmdown`` / ``llmconvert``。
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

__all__ = ["FolderInfo", "FolderKind", "ImportPlan", "copy_import", "inspect_folder", "plan_import"]


class FolderKind(StrEnum):
    """导入文件夹的类型：决定可选操作（直接导入 / 导入并转换）。"""

    TENSORS = "tensors"
    GGUF = "gguf"
    MIXED = "mixed"
    DIFFUSERS = "diffusers"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class FolderInfo:
    """文件夹检测结果：类型、展示名、说明与是否支持导入后转换。"""

    kind: FolderKind
    label: str
    hint: str
    can_convert: bool


def inspect_folder(path: Path) -> FolderInfo:
    """识别文件夹是 HF tensors、GGUF、二者混合、diffusers 还是未知类型。"""
    folder = Path(path).expanduser()
    if not folder.is_dir():
        raise ValueError(f"不是有效文件夹：{path}")
    has_index = (folder / "model_index.json").is_file()
    has_tensors = next(folder.rglob("*.safetensors"), None) is not None
    has_gguf = next(folder.rglob("*.gguf"), None) is not None
    if has_index:
        return FolderInfo(
            FolderKind.DIFFUSERS,
            "diffusers 多组件模型（不可转换）",
            "包含 model_index.json，当前只支持单目录 HF transformers safetensors；仍可导入留档。",
            False,
        )
    if has_tensors and has_gguf:
        return FolderInfo(
            FolderKind.MIXED,
            "GGUF + HF safetensors（tensors，可选转换）",
            "同时包含 GGUF 与 tensors：可直接启动 GGUF，也可把 tensors 转换为 GGUF。",
            True,
        )
    if has_tensors:
        return FolderInfo(
            FolderKind.TENSORS,
            "HF safetensors（tensors，可转换为 GGUF）",
            "导入后可用行内「转换」生成 GGUF，或选择「导入并转换」立即开始。",
            True,
        )
    if has_gguf:
        return FolderInfo(
            FolderKind.GGUF,
            "GGUF（可直接启动）",
            "导入后可直接在运行页选择并启动，无需转换。",
            False,
        )
    return FolderInfo(
        FolderKind.UNKNOWN,
        "未识别到 GGUF / safetensors（unknown）",
        "未找到 GGUF 或 safetensors 文件，仍可导入，但不会出现在可启动模型列表中。",
        False,
    )


@dataclass(frozen=True, slots=True)
class ImportPlan:
    """一项导入：源路径、目标位置与是否为目录。"""

    source: Path
    target: Path
    is_dir: bool


def plan_import(sources: Iterable[Path | str], models_root: Path) -> tuple[ImportPlan, ...]:
    """把用户选择映射为导入计划；非法路径 / 已在模型目录内时抛 ``ValueError``。"""
    root = Path(models_root).expanduser()
    root_resolved = root.resolve()
    plans: list[ImportPlan] = []
    for raw in sources:
        source = Path(raw).expanduser()
        if not source.exists():
            raise ValueError(f"路径不存在：{source}")
        resolved = source.resolve()
        if resolved.is_relative_to(root_resolved):
            raise ValueError(f"该模型已在模型目录中，无需导入：{source}")
        if source.is_file():
            if source.suffix.lower() != ".gguf":
                raise ValueError(f"仅支持导入 .gguf 文件：{source}")
            target = root / source.stem / source.name
            plans.append(ImportPlan(source, target, False))
        elif source.is_dir():
            target = root / source.name
            plans.append(ImportPlan(source, target, True))
        else:  # pragma: no cover - 文件 / 目录之外的类型不会由文件对话框给出
            raise ValueError(f"不支持的导入路径：{source}")
    return tuple(plans)


def copy_import(plan: ImportPlan, *, overwrite: bool = False) -> Path:
    """执行一项导入并返回目标路径；目标存在且未允许覆盖时抛 ``FileExistsError``。"""
    if plan.target.exists() and not overwrite:
        raise FileExistsError(f"目标已存在：{plan.target}")
    if plan.is_dir:
        shutil.copytree(plan.source, plan.target, dirs_exist_ok=overwrite)
    else:
        plan.target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plan.source, plan.target)
    return plan.target
