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
from pathlib import Path

__all__ = ["ImportPlan", "copy_import", "plan_import"]


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
