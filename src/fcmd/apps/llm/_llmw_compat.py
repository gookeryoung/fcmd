"""_llmw_compat - 引擎 / 模型与本机硬件、驱动的适配判断（纯逻辑）。

两条链路共用这里的判断，界面只负责把 :class:`Compat` 渲染成标签与提示：

- 引擎与硬件：CUDA 需要 NVIDIA 显卡与足够新的驱动，Vulkan 走通用显卡驱动，
  ROCm / SYCL 只适用于对应厂商，CPU 不依赖显卡驱动。
- 模型与引擎：GGUF 才能被 llama.cpp 加载；视觉模型需要 mmproj；扩散模型
  按家族交给 sd.cpp；CPU 构建在有关卡时只能跑 CPU，速度受限。

不联网、不起子进程：GPU / 驱动信息由调用方传入（通常是硬件快照），
缺失时按「未知」处理，避免在界面刷新时反复调用 ``nvidia-smi``。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fcmd.apps.llm import llmmodel

if TYPE_CHECKING:
    from fcmd.apps.llm.llmbase import ModelEntry

__all__ = [
    "Compat",
    "GpuStatus",
    "cuda_requirement",
    "current_engine_variant",
    "current_image_backend",
    "engine_compat",
    "engine_kind",
    "gpu_status",
    "image_compat",
    "model_compat",
]


@dataclass(frozen=True, slots=True)
class Compat:
    """一条适配结论：级别 + 标签 + 语义色 + 说明。"""

    level: str
    """``ok`` / ``warn`` / ``fail`` / ``unknown``。"""

    label: str
    """短标签，直接放进状态胶囊（不超过 6 个字）。"""

    tone: str
    """状态胶囊语义色：``success`` / ``warning`` / ``danger`` / ``muted``。"""

    detail: str = ""

    @property
    def hint(self) -> str:
        """标签 + 说明的完整提示文本。"""
        return f"{self.label}：{self.detail}" if self.detail else self.label


@dataclass(frozen=True, slots=True)
class GpuStatus:
    """用于适配判断的 GPU 摘要（名称 / 显存 / 驱动版本）。"""

    name: str = ""
    memory_mib: int | None = None
    driver: str = ""

    @property
    def present(self) -> bool:
        return bool(self.name.strip())

    @property
    def vendor(self) -> str:
        """``nvidia`` / ``other`` / ``""``（未检测到）。"""
        if not self.present:
            return ""
        lowered = self.name.casefold()
        return "nvidia" if any(token in lowered for token in _NVIDIA_TOKENS) else "other"

    @property
    def driver_major(self) -> int:
        match = re.match(r"\s*(\d+)", self.driver)
        return int(match.group(1)) if match else 0


_NVIDIA_TOKENS = ("nvidia", "geforce", "rtx", "gtx", "quadro", "tesla")

_KINDS = {
    "cuda": "cuda",
    "vulkan": "vulkan",
    "rocm": "rocm",
    "hip": "rocm",
    "sycl": "sycl",
    "openvino": "openvino",
    "metal": "metal",
    "opencl": "opencl",
    "cpu": "cpu",
}

_CUDA_MIN_DRIVER = {11: 450, 12: 525, 13: 580}
"""各 CUDA 大版本在 Windows / Linux 上要求的最低 NVIDIA 驱动主版本。"""


@dataclass(frozen=True, slots=True)
class CudaRequirement:
    cuda: int
    min_driver: int


def engine_kind(variant: str) -> str:
    """把构建变体（``CUDA`` / ``cuda12`` / ``ROCm`` / ``Vulkan``…）归一到后端类别。"""
    key = (variant or "").strip().casefold()
    if key.startswith("cuda"):
        return "cuda"
    if key.startswith(("rocm", "hip")):
        return "rocm"
    return _KINDS.get(key, "unknown")


def cuda_requirement(variant: str) -> CudaRequirement:
    """CUDA 构建需要的 CUDA / 驱动大版本；未标注版本时按当前默认 CUDA 13 处理。"""
    match = re.search(r"cuda[-_ ]?(\d+)", (variant or "").casefold())
    cuda = int(match.group(1)) if match else 13
    return CudaRequirement(cuda, _CUDA_MIN_DRIVER.get(cuda, 580))


def gpu_status(hardware: Any | None = None, gpu_mib: int | None = None) -> GpuStatus:
    """从硬件快照（或仅显存值）构造 :class:`GpuStatus`；不触发任何检测。"""
    name = str(getattr(hardware, "gpu", "") or "")
    memory = getattr(hardware, "gpu_mib", None)
    driver = str(getattr(hardware, "gpu_driver", "") or "")
    if name:
        return GpuStatus(name, memory if memory is not None else gpu_mib, driver)
    if gpu_mib is not None:
        return GpuStatus("NVIDIA GPU", gpu_mib, "")
    return GpuStatus()


def engine_compat(variant: str, *, gpu: GpuStatus | None = None) -> Compat:
    """引擎构建与当前硬件 / 驱动的适配结论。"""
    status = gpu or GpuStatus()
    if not (variant or "").strip():
        return Compat("unknown", "未设引擎", "warning", "未找到当前引擎；先安装引擎并在设置页设为默认")
    kind = engine_kind(variant)
    handler = _ENGINE_HANDLERS.get(kind)
    if handler is None:
        return Compat("unknown", "未知构建", "muted", f"无法识别构建变体 {variant!r}，按 release 说明确认适用范围")
    return handler(variant, status)


def _cpu_compat(_variant: str, status: GpuStatus) -> Compat:
    detail = "CPU 构建不依赖显卡驱动，可离线运行；速度受 CPU 与内存限制"
    if status.present:
        detail = f"CPU 构建不依赖显卡驱动；{status.name} 不会启用，速度受 CPU 限制"
    return Compat("ok", "适配", "success", detail)


def _cuda_compat(variant: str, status: GpuStatus) -> Compat:
    requirement = cuda_requirement(variant)
    if not status.present:
        return Compat(
            "fail",
            "需 N 卡",
            "danger",
            "CUDA 构建只支持 NVIDIA 显卡；本机未检测到 NVIDIA GPU，建议改用 CPU / Vulkan 构建",
        )
    if status.vendor != "nvidia":
        return Compat("fail", "不适用", "danger", f"CUDA 构建只用于 NVIDIA 显卡；当前检测到 {status.name}")
    if not status.driver:
        return Compat(
            "warn",
            "驱动未知",
            "warning",
            f"未读到 NVIDIA 驱动版本；CUDA {requirement.cuda} 需要驱动 {requirement.min_driver}+",
        )
    if status.driver_major < requirement.min_driver:
        return Compat(
            "warn",
            "驱动过旧",
            "warning",
            f"CUDA {requirement.cuda} 需要驱动 {requirement.min_driver}+，当前 {status.driver}；GPU 卸载可能失败",
        )
    return Compat("ok", "适配", "success", f"{status.name} / 驱动 {status.driver} 满足 CUDA {requirement.cuda}")


def _vulkan_compat(_variant: str, status: GpuStatus) -> Compat:
    if status.present:
        return Compat("ok", "适配", "success", f"Vulkan 构建可走 {status.name}，需显卡驱动支持 Vulkan")
    return Compat(
        "unknown",
        "需 Vulkan",
        "warning",
        "未检测到 NVIDIA GPU；Vulkan 构建也适用于 AMD / Intel，请确认显卡驱动支持 Vulkan，否则改用 CPU",
    )


def _rocm_compat(_variant: str, status: GpuStatus) -> Compat:
    if status.vendor == "nvidia":
        return Compat("fail", "不适用", "danger", f"ROCm / HIP 构建只用于 AMD 显卡；当前检测到 {status.name}")
    return Compat("unknown", "需 AMD", "warning", "ROCm / HIP 构建需要 AMD 显卡与匹配的 ROCm 驱动")


def _intel_compat(_variant: str, status: GpuStatus) -> Compat:
    if status.vendor == "nvidia":
        return Compat("warn", "需 Intel", "warning", "SYCL / OpenVINO 主要面向 Intel 显卡与 NPU；NVIDIA 显卡不会加速")
    return Compat("unknown", "需 Intel", "warning", "SYCL / OpenVINO 需要 Intel GPU / NPU 与对应运行时")


def _metal_compat(_variant: str, _status: GpuStatus) -> Compat:
    if sys.platform == "darwin":
        return Compat("ok", "适配", "success", "Metal 构建用于 Apple Silicon")
    return Compat("fail", "仅 macOS", "danger", "Metal 构建只能在 macOS 上运行")


def _opencl_compat(_variant: str, _status: GpuStatus) -> Compat:
    return Compat("unknown", "兼容兜底", "warning", "OpenCL 构建依赖显卡驱动，性能通常不如 CUDA / Vulkan")


_ENGINE_HANDLERS = {
    "cuda": _cuda_compat,
    "vulkan": _vulkan_compat,
    "rocm": _rocm_compat,
    "sycl": _intel_compat,
    "openvino": _intel_compat,
    "metal": _metal_compat,
    "opencl": _opencl_compat,
    "cpu": _cpu_compat,
}


def _vision_compat(entry: ModelEntry) -> Compat | None:
    """视觉模型与 mmproj 的配对提示；非视觉模型返回 ``None``。"""
    if "视觉" not in entry.tags:
        return None
    try:
        candidate = llmmodel.find_mmproj(entry.path)
    except OSError:
        candidate = None
    if candidate is None:
        return Compat(
            "warn",
            "缺 mmproj",
            "warning",
            "视觉模型需要配套 mmproj 视觉塔；同目录未找到配对文件，服务会降级为纯文本",
        )
    return Compat("ok", "视觉就绪", "success", f"已配对视觉塔 {candidate.path.name}")


def model_compat(entry: ModelEntry, engine_variant: str, *, gpu: GpuStatus | None = None) -> Compat:
    """单个 GGUF 模型与推理引擎、硬件驱动的适配结论。"""
    status = gpu or GpuStatus()
    if entry.path.suffix.casefold() != ".gguf":
        return Compat("warn", "非 GGUF", "warning", "llama.cpp 只加载 GGUF；请先在模型页转换格式")
    if not (engine_variant or "").strip():
        return Compat("unknown", "未设引擎", "warning", "未找到默认推理引擎；先安装并在设置页设为默认")

    engine = engine_compat(engine_variant, gpu=status)
    if engine.level == "fail":
        return Compat("warn", "GPU 不兼容", "warning", engine.detail)

    vision = _vision_compat(entry)
    if vision is not None and vision.level == "warn":
        return vision
    return _model_fit(engine_variant, engine, status, vision)


def _model_fit(engine_variant: str, engine: Compat, status: GpuStatus, vision: Compat | None) -> Compat:
    """模型能加载时的补充提示：CPU 构建、受限引擎、视觉已配对。"""
    if engine_kind(engine_variant) == "cpu":
        if status.present:
            return Compat(
                "warn",
                "仅 CPU",
                "warning",
                f"当前推理引擎为 CPU 构建，将不使用 {status.name}；换 CUDA / Vulkan 构建可明显加速",
            )
        return Compat("ok", "CPU 兼容", "success", "当前为 CPU 构建，模型可在 CPU 上运行（速度较慢）")
    if engine.level in {"warn", "unknown"}:
        return Compat("warn", "受限", "warning", engine.detail)
    if vision is not None:
        return vision
    return Compat("ok", "兼容", "success", f"GGUF 与 {engine_variant} 引擎匹配；{engine.detail}")


def image_compat(model: Path, backend: str, *, gpu: GpuStatus | None = None) -> Compat:
    """扩散模型与 sd.cpp 引擎、硬件驱动的适配结论。"""
    status = gpu or GpuStatus()
    if not (backend or "").strip():
        return Compat("unknown", "未设图像引擎", "warning", "未找到当前 sd.cpp 引擎；先安装并在设置页设为默认")

    engine = engine_compat(backend, gpu=status)
    if engine.level == "fail":
        return Compat("warn", "GPU 不兼容", "warning", engine.detail)

    from fcmd.apps.llm import _llmpaint

    family_key = _llmpaint.detect_family(model)
    if family_key == "unknown":
        return Compat("warn", "家族未知", "warning", "未识别扩散模型家族；sd.cpp 可能无法加载该权重")

    label = _llmpaint.FAMILIES[family_key].label
    kind = engine_kind(backend)
    if kind == "cpu" and status.present:
        return Compat("warn", "仅 CPU", "warning", f"{label} 将用 CPU 出图，速度很慢；换 CUDA / Vulkan 构建可加速")
    if engine.level in {"warn", "unknown"}:
        return Compat("warn", "受限", "warning", engine.detail)
    return Compat("ok", "兼容", "success", f"{label} 可由 {backend} 引擎加载；{engine.detail}")


def current_engine_variant() -> str:
    """当前默认 llama.cpp 引擎的构建类别；没有默认引擎时返回空串。"""
    from fcmd.apps.llm import _llmw_settings

    try:
        engines = _llmw_settings.installed_engines()
    except OSError:
        return ""
    for info in engines:
        if info.source == "current":
            return _llmw_settings.engine_variant(info)
    return ""


def current_image_backend() -> str:
    """当前默认 sd.cpp 引擎的后端标识；没有默认引擎时返回空串。"""
    from fcmd.apps.llm import _llmpaint_engine

    tag = _llmpaint_engine.current_sd_tag()
    if not tag:
        return ""
    for engine in _llmpaint_engine.list_sd_engines():
        if engine.tag == tag:
            return _llmpaint_engine.engine_backend(engine)
    return ""
