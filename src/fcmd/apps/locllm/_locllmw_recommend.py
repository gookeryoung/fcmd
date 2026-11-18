"""locllmw 概览页的智能推荐：模型 + 启动配置 + 性能预测/实测。

输入是 ``llmenv`` 扫描出的 :class:`ModelEntry` 列表、硬件快照与已记录的实测
结果；输出按「整卡显存能放下」筛选后的推荐行。性能在没有实测数据时按参考
机器的基准线性外推并明确标注「预测」；同一模型已有性能测试结果时改用实测
值并标注「实测」。每条推荐另按「显存余量 / 上下文 / KV 精度 / 解码速度」
四个维度打出 0-100 分（:func:`score_recommendation`），候选按模型大小筛选后
按评分从高到低展示，供概览页用颜色区分档位。本模块不依赖 GUI 框架，可直接单测。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fcmd.apps.locllm import _llmtune, llmenv, llmmodel
from fcmd.apps.locllm._locllmw_hardware import HardwareSnapshot
from fcmd.apps.locllm.llmbase import DEFAULT_PROFILE, PROFILES, ModelEntry

__all__ = [
    "SCORE_BANDS",
    "SCORE_PART_NAMES",
    "Recommendation",
    "predict_performance",
    "recommend",
    "score_band",
    "score_recommendation",
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

#: 评分权重（合计 100）：显存余量 40 / 上下文 25 / KV 精度 15 / 解码速度 20
SCORE_WEIGHT_FIT = 40
SCORE_WEIGHT_CONTEXT = 25
SCORE_WEIGHT_KV = 15
SCORE_WEIGHT_PERF = 20

#: 评分明细显示名，顺序与 ``Recommendation.score_parts`` 一致
SCORE_PART_NAMES = ("显存", "上下文", "KV", "性能")

_FIT_COMFORT_RATIO = 0.55
"""预计占用不超过显卡的 55% 算显存满分；正好用满（100%）得 0 分。"""

_CPU_FIT_RATIO = 0.55
"""CPU 推理不受显存约束，显存维度给中性值，靠性能维度拉开差距。"""

_COMFORT_DECODE_TPS = 20.0
"""解码 ≥ 20 tok/s 算舒适线，性能维度满分。"""

_KV_QUALITY = {"f16": 1.0, "q8_0": 0.7, "q4_0": 0.4}
"""KV 缓存精度相对 f16 的质量系数（精度越低，长上下文越容易丢细节）。"""

SCORE_BANDS = ((80, "excellent"), (65, "good"), (50, "fair"), (0, "limited"))
"""评分档位（从高到低）：≥80 极佳、≥65 合适、≥50 可用、其余勉强。"""


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
    score: int
    score_parts: tuple[int, int, int, int]
    """评分明细，顺序见 :data:`SCORE_PART_NAMES`，各项之和等于 ``score``。"""

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

    @property
    def score_band(self) -> str:
        """评分档位 id（``excellent`` / ``good`` / ``fair`` / ``limited``）。"""
        return score_band(self.score)

    @property
    def score_text(self) -> str:
        """评分摘要，例如 ``评分 78/100``。"""
        return f"评分 {self.score}/100"

    @property
    def score_detail(self) -> str:
        """评分明细，例如 ``显存 33 · 上下文 25 · KV 15 · 性能 12``。"""
        return " · ".join(f"{name} {value}" for name, value in zip(SCORE_PART_NAMES, self.score_parts, strict=True))


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def score_band(score: int) -> str:
    """把 0-100 的评分映射到档位 id（用于配色）。"""
    for threshold, band in SCORE_BANDS:
        if score >= threshold:
            return band
    return "limited"  # pragma: no cover - SCORE_BANDS 末项为 0，正常走不到


def score_recommendation(  # noqa: PLR0913 - 评分维度由推荐流程逐项透传
    *,
    profile: str,
    context: int,
    kv_type: str,
    model_bytes: int,
    gpu_mib: int | None,
    decode_tps: float,
) -> tuple[int, tuple[int, int, int, int]]:
    """给一条推荐打分，返回 ``(总分, (显存, 上下文, KV, 性能))``。

    四个维度各自归一到 0-1 后按权重取整，总分等于四项之和（便于界面展示明细）：

    - 显存余量（40）：预计占用 ≤55% 显存得满分，用满 100% 得 0 分；
      CPU 推理没有显存约束，给 55% 的中性分。
    - 上下文（25）：实际上下文 / 档位默认上下文，被压缩得越狠分越低。
    - KV 精度（15）：相对档位默认 KV 精度的质量系数（f16 1.0 / q8_0 0.7 / q4_0 0.4）。
    - 解码速度（20）：解码 tok/s / 舒适线 20 tok/s，达到或超过舒适线得满分。

    评分只反映「这份配置在当前机器上有多合适」，不代表模型能力；实测与预测
    只在 ``basis`` / 性能文本里区分。
    """
    wanted = PROFILES.get(profile)
    default_context = wanted.context if wanted is not None else max(context, 1)
    default_kv = wanted.kv_type if wanted is not None else "f16"
    if gpu_mib:
        needed = llmmodel.estimate_vram(model_bytes, context, kv_type)
        ratio = needed / (gpu_mib * 1024 * 1024)
        fit = _clamp((1.0 - ratio) / (1.0 - _FIT_COMFORT_RATIO))
    else:
        fit = _CPU_FIT_RATIO
    ctx = _clamp(context / default_context)
    kv = _clamp(_KV_QUALITY.get(kv_type, 0.4) / _KV_QUALITY.get(default_kv, 1.0))
    perf = _clamp(decode_tps / _COMFORT_DECODE_TPS)
    parts = (
        round(fit * SCORE_WEIGHT_FIT),
        round(ctx * SCORE_WEIGHT_CONTEXT),
        round(kv * SCORE_WEIGHT_KV),
        round(perf * SCORE_WEIGHT_PERF),
    )
    return sum(parts), parts


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
    """从能放入显存的模型里取最大的 ``limit`` 个，再按评分从高到低排序。

    候选集先按模型大小从大到小筛选（选能力更强的模型），展示顺序再按评分降序；
    评分相同时按模型大小从大到小、再按路径排序。显存未知（CPU 推理）时仍给出
    推荐，但性能按 CPU 折减预测；没有模型时返回空。
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
        score, score_parts = score_recommendation(
            profile=profile,
            context=context,
            kv_type=kv_type,
            model_bytes=entry.size,
            gpu_mib=gpu_mib,
            decode_tps=decode,
        )
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
                score=score,
                score_parts=score_parts,
            )
        )
        if len(recommendations) >= limit:
            break
    recommendations.sort(key=lambda row: (-row.score, -row.model.size, str(row.model.path)))
    return tuple(recommendations)
