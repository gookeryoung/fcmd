"""locllmw 概览页的智能推荐：模型 + 启动配置 + 性能预测/实测。

输入是 ``llmenv`` 扫描出的 :class:`ModelEntry` 列表、硬件快照与已记录的实测
结果；输出按「整卡显存能放下」筛选后的推荐行。性能在没有实测数据时按参考
机器的基准线性外推并明确标注「预测」；同一模型已有性能测试结果时改用实测
值并标注「实测」。本模块不依赖 Tk，可直接单测。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fcmd.apps.locllm import _llmtune, llmenv, llmmodel
from fcmd.apps.locllm._locllmw_hardware import HardwareSnapshot
from fcmd.apps.locllm.llmbase import DEFAULT_PROFILE, PROFILES, ModelEntry

__all__ = [
    "Recommendation",
    "predict_performance",
    "recommend",
]

#: 参考机器：RTX 5090 D 32GB + Qwen3-Coder-30B-A3B Q5_K_M（ADR 0015 实测）
REFERENCE_GPU_MIB = 32607
REFERENCE_MODEL_BYTES = 20_200_000_000
REFERENCE_DECODE_TPS = 250.0
REFERENCE_PROMPT_TPS = 16200.0

#: 不同调优策略对单请求解码速度的折减（相对默认策略）
_TUNING_FACTORS = {
    "throughput": 0.93,
    "long-context": 0.75,
    "low-vram": 0.76,
}
_TUNING_LABEL_FACTORS = (("并发吞吐", 0.93), ("长上下文", 0.75), ("省显存", 0.76))

_MIN_CONTEXT = 4096


@dataclass(frozen=True, slots=True)
class Recommendation:
    """一条推荐：模型、启动配置与性能（``measured`` 为真表示来自性能测试）。"""

    model: ModelEntry
    profile: str
    context: int
    kv_type: str
    batch: int
    ubatch: int
    parallel: int
    tuning: str
    decode_tps: float
    prompt_tps: float
    measured: bool
    basis: str

    @property
    def config_text(self) -> str:
        """紧凑的配置摘要，供概览表展示。"""
        return (
            f"{self.profile} / ctx {self.context // 1024}K / KV {self.kv_type} / "
            f"b{self.batch} / ub{self.ubatch} / 并行 {self.parallel}"
        )

    @property
    def performance_text(self) -> str:
        """性能摘要：解码 / 预填充 + 来源标记。"""
        source = "实测" if self.measured else "预测"
        return f"解码 {self.decode_tps:.0f} tok/s · 预填充 {self.prompt_tps:.0f} tok/s（{source}）"


def _gpu_factor(gpu_mib: int | None) -> float:
    if not gpu_mib:
        return 0.03
    return max(0.05, min(1.6, (gpu_mib / REFERENCE_GPU_MIB) ** 0.85))


def predict_performance(model_bytes: int, gpu_mib: int | None, *, tuning: str = "default") -> tuple[float, float]:
    """按参考机器基准预测解码 / 预填充速度（tok/s）。"""
    if model_bytes <= 0:
        return 0.0, 0.0
    scale = REFERENCE_MODEL_BYTES / model_bytes * _gpu_factor(gpu_mib)
    factor = _TUNING_FACTORS.get(tuning, 1.0)
    for label, value in _TUNING_LABEL_FACTORS:
        if label in tuning:
            factor = value
            break
    return REFERENCE_DECODE_TPS * scale * factor, REFERENCE_PROMPT_TPS * scale


def _preferred_profile(entry: ModelEntry) -> str:
    for tag in entry.tags:
        model_tag = llmmodel.MODEL_TAGS.get(tag)
        if model_tag is not None and model_tag.profile in PROFILES:
            return model_tag.profile
    return DEFAULT_PROFILE


def _choose_config(entry: ModelEntry, gpu_mib: int | None) -> tuple[str, int, str, int, int, int, str] | None:
    """为单个模型选择档位 / 上下文 / KV，返回 ``None`` 表示整卡放不下。"""
    profile_name = _preferred_profile(entry)
    profile = PROFILES[profile_name]
    context = profile.context
    kv_type = profile.kv_type
    if not gpu_mib:
        tuning = _llmtune.resolve("auto", context=context, model_bytes=entry.size, gpu_mib=None)
        return (
            profile_name,
            context,
            kv_type,
            tuning.batch,
            tuning.ubatch or profile.ubatch,
            tuning.parallel,
            tuning.label,
        )
    gpu_bytes = gpu_mib * 1024 * 1024
    fit = llmmodel.fit_vram(entry.size, gpu_bytes, context, kv_type)
    if not fit.ok and kv_type != "q8_0":
        kv_type = "q8_0"
        fit = llmmodel.fit_vram(entry.size, gpu_bytes, context, kv_type)
    if fit.max_context < _MIN_CONTEXT:
        return None
    context = min(context, max(fit.max_context, _MIN_CONTEXT))
    if not llmmodel.fit_vram(entry.size, gpu_bytes, context, kv_type).ok:
        return None
    tuning = _llmtune.resolve("auto", context=context, model_bytes=entry.size, gpu_mib=gpu_mib)
    return (
        profile_name,
        context,
        kv_type,
        tuning.batch,
        tuning.ubatch or profile.ubatch,
        tuning.parallel,
        tuning.label,
    )


def _measured_for(measured: Mapping[str, Mapping[str, Any]], key: str) -> Mapping[str, Any] | None:
    entry = measured.get(key)
    return entry if isinstance(entry, Mapping) else None


def recommend(
    models: Sequence[ModelEntry],
    hardware: HardwareSnapshot | None,
    measured: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    limit: int = 3,
) -> tuple[Recommendation, ...]:
    """按模型大小从大到小推荐最多 ``limit`` 个能放入显存的模型。

    显存未知（CPU 推理）时仍给出推荐，但性能按 CPU 折减预测；没有模型时返回空。
    """
    if not models:
        return ()
    measured = measured or {}
    gpu_mib = hardware.gpu_mib if hardware is not None else None
    recommendations: list[Recommendation] = []
    for entry in sorted(models, key=lambda item: (-item.size, str(item.path))):
        chosen = _choose_config(entry, gpu_mib)
        if chosen is None:
            continue
        profile, context, kv_type, batch, ubatch, parallel, tuning = chosen
        key = llmenv.path_key(entry.path)
        record = _measured_for(measured, key)
        decode, prompt = predict_performance(entry.size, gpu_mib, tuning=tuning)
        decode_value = record.get("decode_tps") if record is not None else None
        is_measured = isinstance(decode_value, (int, float))
        if is_measured:
            decode = float(decode_value)
            prompt_value = record.get("prompt_tps") if record is not None else None
            if isinstance(prompt_value, (int, float)):
                prompt = float(prompt_value)
        basis = f"{(entry.tags and ' / '.join(entry.tags)) or '通用'} · {'性能测试实测' if is_measured else '按参考机器外推'}"
        recommendations.append(
            Recommendation(
                model=entry,
                profile=profile,
                context=context,
                kv_type=kv_type,
                batch=batch,
                ubatch=ubatch,
                parallel=parallel,
                tuning=tuning,
                decode_tps=decode,
                prompt_tps=prompt,
                measured=is_measured,
                basis=basis,
            )
        )
        if len(recommendations) >= limit:
            break
    return tuple(recommendations)
