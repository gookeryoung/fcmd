"""llmserve 启动参数调优策略（纯逻辑，不做输出 / 不注册 CLI）。

预设值来自本机实测（RTX 5090 D 32GB / llama.cpp b12212 /
Qwen3-Coder-30B-A3B-Instruct Q5_K_M），测量方法与结论见
``docs/adr/0015-llm-serve-tuning.md``。``auto`` 只做保守的硬件 / 上下文判断，
显式填写的启动参数始终优先于策略值。
"""

from __future__ import annotations

from dataclasses import replace

from fcmd.tools.llm.llmbase import ServeError, Tuning
from fcmd.tools.llm.llmmodel import estimate_vram

TUNE_NAMES: tuple[str, ...] = ("auto", "default", "throughput", "long-context", "low-vram")

TUNE_LABELS: dict[str, str] = {
    "auto": "自动（按硬件）",
    "default": "默认（现状）",
    "throughput": "并发吞吐",
    "long-context": "长上下文",
    "low-vram": "省显存",
}

LABEL_TO_TUNE: dict[str, str] = {label: name for name, label in TUNE_LABELS.items()}

#: 预计显存占用超过该比例时，auto 切换到省显存策略
VRAM_TIGHT_RATIO = 0.92
#: 达到该上下文长度时，auto 切换到长上下文策略
LONG_CONTEXT_TOKENS = 131072

_PRESETS: dict[str, Tuning] = {
    "default": Tuning(
        name="default",
        label="默认（现状）",
        detail="b4096 / ubatch 2048 / 单并发，实测单请求解码与预填充最优",
        batch=4096,
        ubatch=None,
        parallel=1,
        cache_reuse=256,
    ),
    "throughput": Tuning(
        name="throughput",
        label="并发吞吐",
        detail="4 个并发槽 + 8 个 HTTP 线程，实测并发吞吐约 2.4 倍，单请求约 -7%",
        batch=4096,
        ubatch=None,
        parallel=4,
        cache_reuse=256,
        threads_http=8,
    ),
    "long-context": Tuning(
        name="long-context",
        label="长上下文",
        detail="KV 改用 q8_0 以容纳 128K 上下文，实测 32K 解码约 -25%",
        batch=4096,
        ubatch=None,
        parallel=1,
        cache_reuse=256,
        kv_type="q8_0",
    ),
    "low-vram": Tuning(
        name="low-vram",
        label="省显存",
        detail="KV 改用 q4_0，b2048 / ubatch 512，实测 8K 预填充约 -14%、解码约 -24%",
        batch=2048,
        ubatch=512,
        parallel=1,
        cache_reuse=128,
        kv_type="q4_0",
    ),
}


def preset(name: str) -> Tuning:
    """返回预设策略；未知名称抛出 :class:`ServeError`。"""
    try:
        return _PRESETS[name]
    except KeyError as exc:
        options = ", ".join(TUNE_NAMES)
        raise ServeError(f"未知调优策略: {name}（可选: {options}）") from exc


def resolve(name: str, *, context: int, model_bytes: int, gpu_mib: int | None) -> Tuning:
    """把策略名解析为生效值；``auto`` 按显存与上下文选择保守预设。"""
    if name != "auto":
        return preset(name)
    selected, reason = _auto_select(context=context, model_bytes=model_bytes, gpu_mib=gpu_mib)
    base = _PRESETS[selected]
    return replace(base, name="auto", label=f"自动 → {base.label}", detail=reason)


def _auto_select(context: int, model_bytes: int, gpu_mib: int | None) -> tuple[str, str]:
    if gpu_mib is None:
        return "default", "未检测到 NVIDIA GPU，保持默认参数（CPU 推理）"
    total_bytes = gpu_mib * 1024 * 1024
    if context >= LONG_CONTEXT_TOKENS:
        needed_long = estimate_vram(model_bytes, context, "q8_0")
        if needed_long <= total_bytes * 0.95:
            return "long-context", (
                f"上下文 {context} ≥ {LONG_CONTEXT_TOKENS}，改用长上下文策略"
                f"（KV q8_0 预计 {needed_long / 1024**3:.1f} GiB）"
            )
        return "low-vram", (
            f"上下文 {context} 较长，即使 KV q8_0 也预计占用 "
            f"{needed_long / 1024**3:.1f} GiB / {total_bytes / 1024**3:.1f} GiB，改用省显存策略"
        )
    needed = estimate_vram(model_bytes, context, "f16")
    if needed > total_bytes * VRAM_TIGHT_RATIO:
        return "low-vram", (
            f"预计显存 {needed / 1024**3:.1f} GiB / {total_bytes / 1024**3:.1f} GiB 超过 "
            f"{VRAM_TIGHT_RATIO:.0%}，改用省显存策略"
        )
    return "default", f"预计显存 {needed / 1024**3:.1f} GiB / {total_bytes / 1024**3:.1f} GiB，显存与上下文适中"
