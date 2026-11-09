"""llmmodel - 本地 GGUF 模型扫描、定位与显存估算。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from fcmd.tools.llm import llmenv
from fcmd.tools.llm.llmbase import (
    KV_BYTES_PER_TOKEN_FP16,
    KV_SCALE,
    PROFILES,
    VRAM_OVERHEAD_BYTES,
    LaunchPlan,
    ModelEntry,
    ServeError,
)

__all__ = ["estimate_vram", "find_model", "plan_vram", "scan_models"]


def scan_models(
    roots: Sequence[Path] | None = None,
    home: Path | None = None,
) -> list[ModelEntry]:
    """递归扫描模型目录，返回 GGUF 清单（忽略 mmproj、跨目录去重、按大小降序）。"""
    entries: list[ModelEntry] = []
    seen: set[str] = set()
    for raw_root in llmenv.models_roots(home=home) if roots is None else roots:
        root = raw_root.expanduser()
        if not root.is_dir():
            continue
        for path in root.rglob("*.gguf"):
            if "mmproj" in path.name.lower():
                continue
            try:
                size = path.stat().st_size
            except OSError:  # pragma: no cover - 扫描期间文件被删除
                continue
            resolved = path.resolve()
            key = llmenv.path_key(resolved)
            if key in seen:
                continue
            seen.add(key)
            profiles = tuple(name for name, prof in PROFILES.items() if prof.quant.lower() in path.name.lower())
            entries.append(ModelEntry(root=root, path=resolved, size=size, profiles=profiles))
    entries.sort(key=lambda entry: (-entry.size, str(entry.path)))
    return entries


def find_model(
    quant: str,
    models_root: Path | None = None,
    model: Path | None = None,
    home: Path | None = None,
) -> Path:
    """定位指定量化档位的 GGUF 文件。

    ``model`` 可为 GGUF 文件或目录（覆盖模型目录配置）；未指定时在所有已配置的
    模型目录中递归查找文件名包含 ``quant`` 的 GGUF（忽略 mmproj），同名取最大。
    """
    if model is not None:
        target = model.expanduser()
        if target.is_file():
            if target.suffix.lower() != ".gguf":
                raise ServeError(f"模型文件不是 .gguf: {target}")
            return target.resolve()
        if not target.is_dir():
            raise ServeError(f"模型路径不存在: {target}")
        roots = [target]
    else:
        roots = llmenv.models_roots(models_root, home)
        existing = [root for root in roots if root.is_dir()]
        if not existing:
            raise ServeError(
                f"模型目录不存在: {', '.join(str(root) for root in roots)}；先下载模型："
                f"fcmd llmdown download unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF --include *{quant}* --dest {roots[0]}"
            )
        roots = existing

    entries = scan_models(roots)
    matched = [entry for entry in entries if quant.lower() in entry.path.name.lower()]
    if not matched:
        available = sorted({entry.path.name for entry in entries})[:5]
        hint = f"；已发现: {', '.join(available)}" if available else ""
        raise ServeError(
            f"在 {', '.join(str(root) for root in roots)} 未找到量化 {quant} 的 GGUF{hint}；"
            f"可执行 fcmd llmdown download unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF --include *{quant}* "
            f"--dest {roots[0]}"
        )
    return max(matched, key=lambda entry: entry.size).path


def estimate_vram(model_bytes: int, context: int, kv_type: str = "q8_0") -> int:
    """估算显存占用（模型权重 + KV 缓存 + 固定开销）。"""
    kv = int(context * KV_BYTES_PER_TOKEN_FP16 * KV_SCALE.get(kv_type, 1.0))
    return model_bytes + kv + VRAM_OVERHEAD_BYTES


def plan_vram(plan: LaunchPlan) -> int:
    """估算启动计划的显存占用。"""
    try:
        size = plan.model.stat().st_size
    except OSError:
        size = 0
    return estimate_vram(size, plan.context, plan.effective_kv_type)
