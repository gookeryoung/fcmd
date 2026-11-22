"""引擎 / 模型与硬件、驱动适配判断的纯逻辑测试。"""

from __future__ import annotations

from pathlib import Path

from fcmd.apps.llm import _llmw_compat
from fcmd.apps.llm.llmbase import ModelEntry


def _gpu(driver: str = "580.88") -> _llmw_compat.GpuStatus:
    return _llmw_compat.GpuStatus("NVIDIA GeForce RTX 4080", 16384, driver)


def test_engine_kind_normalizes_labels_and_backends() -> None:
    assert _llmw_compat.engine_kind("CUDA") == "cuda"
    assert _llmw_compat.engine_kind("cuda12") == "cuda"
    assert _llmw_compat.engine_kind("Vulkan") == "vulkan"
    assert _llmw_compat.engine_kind("rocm-10.1") == "rocm"
    assert _llmw_compat.engine_kind("") == "unknown"


def test_cuda_requirement_follows_variant_version() -> None:
    assert _llmw_compat.cuda_requirement("cuda12").min_driver == 525
    assert _llmw_compat.cuda_requirement("CUDA").cuda == 13
    assert _llmw_compat.cuda_requirement("cuda13").min_driver == 580


def test_cuda_engine_requires_nvidia_gpu_and_recent_driver() -> None:
    ok = _llmw_compat.engine_compat("cuda12", gpu=_gpu("560.94"))
    assert (ok.level, ok.label) == ("ok", "适配")

    old = _llmw_compat.engine_compat("CUDA", gpu=_gpu("550.54"))
    assert (old.level, old.label) == ("warn", "驱动过旧")

    unknown_driver = _llmw_compat.engine_compat("cuda12", gpu=_llmw_compat.GpuStatus("NVIDIA RTX 4090", 24576))
    assert (unknown_driver.level, unknown_driver.label) == ("warn", "驱动未知")

    missing = _llmw_compat.engine_compat("cuda12", gpu=_llmw_compat.GpuStatus())
    assert (missing.level, missing.label) == ("fail", "需 N 卡")


def test_cpu_engine_is_always_usable_and_vulkan_falls_back_to_unknown() -> None:
    assert _llmw_compat.engine_compat("CPU").level == "ok"
    vulkan = _llmw_compat.engine_compat("Vulkan", gpu=_llmw_compat.GpuStatus())
    assert (vulkan.level, vulkan.label) == ("unknown", "需 Vulkan")
    assert _llmw_compat.engine_compat("ROCm", gpu=_gpu()).label == "不适用"


def test_model_compat_marks_cpu_only_build(tmp_path: Path) -> None:
    path = tmp_path / "Qwen3-8B-Q4_K_M.gguf"
    path.write_bytes(b"")
    entry = ModelEntry(root=tmp_path, path=path, size=1, tags=("对话",))
    compat = _llmw_compat.model_compat(entry, "CPU", gpu=_gpu())
    assert (compat.level, compat.label) == ("warn", "仅 CPU")


def test_model_compat_reports_missing_mmproj(tmp_path: Path) -> None:
    path = tmp_path / "llava-v1.6-vision.gguf"
    path.write_bytes(b"")
    entry = ModelEntry(root=tmp_path, path=path, size=1, tags=("视觉",))
    compat = _llmw_compat.model_compat(entry, "CUDA", gpu=_gpu())
    assert (compat.level, compat.label) == ("warn", "缺 mmproj")


def test_model_compat_rejects_non_gguf(tmp_path: Path) -> None:
    path = tmp_path / "model.safetensors"
    path.write_bytes(b"")
    entry = ModelEntry(root=tmp_path, path=path, size=1)
    assert _llmw_compat.model_compat(entry, "CUDA", gpu=_gpu()).label == "非 GGUF"


def test_image_compat_checks_family_and_engine(tmp_path: Path) -> None:
    path = tmp_path / "sdxl-base.safetensors"
    path.write_bytes(b"")
    ok = _llmw_compat.image_compat(path, "cuda12", gpu=_gpu())
    assert (ok.level, ok.label) == ("ok", "兼容")

    unknown = _llmw_compat.image_compat(tmp_path / "weights.safetensors", "vulkan", gpu=_gpu())
    assert (unknown.level, unknown.label) == ("warn", "家族未知")

    missing_engine = _llmw_compat.image_compat(path, "", gpu=_gpu())
    assert (missing_engine.level, missing_engine.label) == ("unknown", "未设图像引擎")
