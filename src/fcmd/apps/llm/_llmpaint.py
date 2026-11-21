"""_llmpaint - 本地扩散模型扫描、组件配对与文生图参数（纯逻辑）。

不依赖 Qt、不联网、不起子进程：llmw 对话页的画图能力与 ``llmpaint`` CLI 共用这里的
资产识别、启动参数、请求载荷与出图保存逻辑。sd.cpp 的 CLI / API 仍在演进，
识别只给默认槽位，用户可用「额外参数」覆盖。
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from fcmd.apps.llm import _llmconfig
from fcmd.apps.llm.llmbase import ServeError, human_size

__all__ = [
    "FAMILIES",
    "MIN_WEIGHT_BYTES",
    "ROLE_LABELS",
    "WEIGHT_EXTENSIONS",
    "Family",
    "PaintAsset",
    "PaintAssets",
    "PaintComponents",
    "PaintRequest",
    "build_model_args",
    "build_payload",
    "classify_role",
    "decode_image",
    "describe_asset",
    "detect_family",
    "effective_mode",
    "gallery",
    "is_diffusion_weight",
    "is_mmproj",
    "missing_required_slots",
    "save_images",
    "scan_assets",
    "server_flags",
    "suggest_components",
    "validate_components",
    "validate_request",
]

AssetRole = Literal["checkpoint", "diffusion", "vae", "clip_l", "clip_g", "t5xxl", "llm", "lora", "unknown"]
LoadMode = Literal["auto", "checkpoint", "diffusion"]

WEIGHT_EXTENSIONS = (".gguf", ".safetensors", ".ckpt", ".sft", ".pth")
"""扩散模型与组件的文件扩展名（与 sd.cpp 支持的权重格式一致）。"""

MIN_WEIGHT_BYTES = 1024 * 1024
"""小于 1 MiB 的文件视为占位 / 损坏，不进入候选。"""

ROLE_LABELS: dict[str, str] = {
    "checkpoint": "Checkpoint",
    "diffusion": "扩散模型",
    "vae": "VAE",
    "clip_l": "CLIP-L",
    "clip_g": "CLIP-G",
    "t5xxl": "T5-XXL",
    "llm": "文本编码器",
    "lora": "LoRA",
    "unknown": "其他权重",
}


@dataclass(frozen=True, slots=True)
class Family:
    """一个模型家族默认的加载方式与可选组件槽位。"""

    key: str
    label: str
    mode: str
    slots: tuple[str, ...]


FAMILIES: dict[str, Family] = {
    "sd15": Family("sd15", "SD 1.x", "checkpoint", ("vae",)),
    "sd2": Family("sd2", "SD 2.x", "checkpoint", ("vae",)),
    "sdxl": Family("sdxl", "SDXL", "checkpoint", ("vae",)),
    "sd3": Family("sd3", "SD 3.x", "diffusion", ("clip_l", "clip_g", "t5xxl", "vae")),
    "flux": Family("flux", "FLUX", "diffusion", ("clip_l", "t5xxl", "vae", "llm")),
    "qwen-image": Family("qwen-image", "Qwen-Image", "diffusion", ("llm", "vae")),
    "z-image": Family("z-image", "Z-Image", "diffusion", ("llm", "vae")),
    "minimax-h3": Family("minimax-h3", "MiniMax H3", "diffusion", ("llm", "vae")),
    "hidream": Family("hidream", "HiDream", "diffusion", ("llm", "vae")),
    "kolors": Family("kolors", "Kolors", "checkpoint", ("vae",)),
    "hunyuan-image": Family("hunyuan-image", "HunyuanImage", "diffusion", ("llm", "vae")),
    "unknown": Family("unknown", "未识别", "checkpoint", ("vae", "clip_l", "clip_g", "t5xxl", "llm")),
}
"""家族槽位表：只给默认展示与加载方式，不强制必填。"""

_FAMILY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("qwen-image", ("qwen-image", "qwenimage")),
    ("z-image", ("z-image", "zimage")),
    ("minimax-h3", ("minimax", "minimax-h3-image", "h3-image")),
    ("hidream", ("hidream",)),
    ("hunyuan-image", ("hunyuan-image", "hunyuanimage")),
    ("kolors", ("kolors",)),
    ("flux", ("flux",)),
    ("sdxl", ("sdxl", "sd-xl", "pony", "illustrious", "noobai")),
    ("sd3", ("sd3", "stable-diffusion-3")),
    ("sd2", ("sd2", "sd21", "v2-1", "stable-diffusion-2")),
    ("sd15", ("sd15", "sd1-5", "v1-5", "stable-diffusion-v1")),
)

_PARENT_ROLES: tuple[tuple[str, str], ...] = (
    ("clip_vision", "unknown"),
    ("vae", "vae"),
    ("text_encoders", "llm"),
    ("text-encoders", "llm"),
    ("text_encoder", "llm"),
    ("clip", "llm"),
    ("t5", "t5xxl"),
    ("diffusion_models", "diffusion"),
    ("diffusion-models", "diffusion"),
    ("unet", "diffusion"),
    ("checkpoints", "checkpoint"),
    ("stable-diffusion", "checkpoint"),
    ("stable_diffusion", "checkpoint"),
    ("loras", "lora"),
    ("lora", "lora"),
)

_ENCODER_PAIRS: dict[str, str] = {"l": "clip_l", "g": "clip_g"}

#: 这些家族的扩散模型必须同时提供独立 VAE / 文本编码器；缺一个 sd-server 会启动即退出。
_REQUIRED_SLOTS: dict[str, tuple[str, ...]] = {
    "z-image": ("llm", "vae"),
    "qwen-image": ("llm", "vae"),
    "minimax-h3": ("llm", "vae"),
    "hidream": ("llm", "vae"),
    "hunyuan-image": ("llm", "vae"),
}
_COMPANION_HINTS: dict[str, str] = {
    "vae": "Comfy-Org 对应仓库的 split_files/vae 或 vae 目录",
    "llm": "Qwen3 / Mistral 文本编码器（GGUF 或 safetensors）",
}

_LLM_ENCODER_KEYWORDS: tuple[str, ...] = (
    "qwen3",
    "qwen2.5",
    "qwen2-",
    "qwen2-vl",
    "qwen3-vl",
    "mistral",
    "llama",
)
"""可能作为扩散模型文本编码器的 LLM 家族关键字（仅在同一仓库已识别为扩散家族时生效）。"""


def _llm_encoder_hint(name: str) -> bool:
    """文件名是否像扩散模型用的 LLM 文本编码器（Qwen / Mistral / Llama）。"""
    normalized = _normalized(name)
    return any(token in normalized for token in _LLM_ENCODER_KEYWORDS)


def _encoder_role(name: str) -> AssetRole | None:
    """从文件名识别文本编码器子类型（CLIP-L / CLIP-G / T5）。

    按 token 匹配，``umt5`` 这类非 T5 编码器不会被误判：只有独立的
    ``t5`` / ``t5xxl`` token 才算 T5。
    """
    tokens = name.split("-")
    for index, token in enumerate(tokens):
        if token == "clip" and index + 1 < len(tokens) and tokens[index + 1] in _ENCODER_PAIRS:
            return _ENCODER_PAIRS[tokens[index + 1]]  # type: ignore[return-value]
        if token in {"clipl", "clipg"}:
            return "clip_l" if token == "clipl" else "clip_g"
        if token == "t5" or token.startswith("t5xxl") or token.startswith("t5v"):
            return "t5xxl"
    return None


def _normalized(name: str) -> str:
    """统一分隔符的小写名（``clip_l`` / ``clip-l`` 归一为 ``clip-l``）。"""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def is_mmproj(path: Path) -> bool:
    """是否为 llama.cpp 的视觉投影文件（扩散扫描与 LLM 扫描都跳过）。"""
    return "mmproj" in path.name.lower()


def detect_family(path: Path) -> str:
    """识别扩散模型家族：先看文件名，再看最近两级父目录；识别不了返回 ``unknown``。

    以归一化后的 token 序列匹配：允许关键词作为前缀贴紧变体后缀
    （``flux1`` / ``ponyDiffusionV6`` / ``illustriousXL``），但以数字结尾的
    关键词（``sd15`` / ``sd2``）要求 token 完全相同，避免 ``sd150`` 误判。

    HuggingFace / ModelScope 下载常见 ``<repo>/model.safetensors`` 这类通用文件名，
    家族信息只在仓库目录名里（``qwen-image-2.1`` / ``MiniMax-H3-Image``），
    因此父目录也必须参与识别，否则主流模型会被当成未知权重跳过。
    """
    for candidate in (path.name, path.parent.name, path.parent.parent.name):
        if not candidate:
            continue
        tokens = [token for token in _normalized(candidate).split("-") if token]
        for key, keywords in _FAMILY_KEYWORDS:
            if any(_keyword_hit(tokens, keyword) for keyword in keywords):
                return key
    return "unknown"


def _keyword_hit(tokens: Sequence[str], keyword: str) -> bool:
    """关键词是否命中 token 序列（支持多段关键词与变体后缀）。"""
    parts = keyword.split("-")
    for index in range(len(tokens) - len(parts) + 1):
        if all(_token_hit(token, part) for token, part in zip(tokens[index : index + len(parts)], parts, strict=True)):
            return True
    return False


def _token_hit(token: str, keyword: str) -> bool:
    if token == keyword:
        return True
    return len(keyword) >= 4 and not keyword[-1].isdigit() and token.startswith(keyword)


def classify_role(path: Path) -> AssetRole:  # noqa: PLR0911 - 角色分支各自直接返回
    """按父目录与文件名判断资产角色（父目录优先）。"""
    name = _normalized(path.name)
    parents = {part.lower() for part in path.parent.parts}
    for keyword, role in _PARENT_ROLES:
        if keyword in parents:
            if role == "llm":
                return _encoder_role(name) or "llm"
            return role  # type: ignore[return-value]
    encoder = _encoder_role(name)
    if encoder is not None:
        return encoder
    if "vae" in name:
        return "vae"
    if "lora" in name:
        return "lora"
    family = detect_family(path)
    # Qwen / Mistral 等 LLM 只有位于已识别的扩散模型仓库里才当作文本编码器；
    # 普通的聊天模型仓库保持 unknown，避免被 llmmodel.scan_models 当成扩散权重跳过。
    if family != "unknown" and _llm_encoder_hint(name):
        return "llm"
    if family != "unknown":
        return "diffusion" if FAMILIES[family].mode == "diffusion" else "checkpoint"
    return "unknown"


def is_diffusion_weight(path: Path) -> bool:
    """是否属于扩散模型 / 组件权重（供 ``llmmodel.scan_models`` 过滤）。"""
    if is_mmproj(path):
        return False
    return classify_role(path) in {"checkpoint", "diffusion", "vae", "clip_l", "clip_g", "t5xxl", "llm", "lora"}


@dataclass(frozen=True, slots=True)
class PaintAsset:
    """扫描到的一个扩散模型或组件文件。"""

    path: Path
    role: AssetRole
    family: str
    size: int

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    @property
    def family_label(self) -> str:
        return FAMILIES.get(self.family, FAMILIES["unknown"]).label


@dataclass(frozen=True, slots=True)
class PaintAssets:
    """一次扫描的结果。"""

    assets: tuple[PaintAsset, ...] = ()
    roots: tuple[Path, ...] = ()

    def by_role(self, role: str) -> tuple[PaintAsset, ...]:
        return tuple(asset for asset in self.assets if asset.role == role)

    def main_models(self) -> tuple[PaintAsset, ...]:
        return tuple(asset for asset in self.assets if asset.role in {"checkpoint", "diffusion"})

    def get(self, path: Path | None) -> PaintAsset | None:
        if path is None:
            return None
        key = _llmconfig.path_key(path)
        return next((asset for asset in self.assets if _llmconfig.path_key(asset.path) == key), None)


def scan_assets(roots: Iterable[Path], *, min_bytes: int = MIN_WEIGHT_BYTES) -> PaintAssets:
    """递归扫描若干根目录，返回扩散模型与组件（按角色 / 大小排序）。"""
    resolved_roots = tuple(root.expanduser() for root in roots)
    seen: set[str] = set()
    assets: list[PaintAsset] = []
    for root in resolved_roots:
        if not root.is_dir():
            continue
        for extension in WEIGHT_EXTENSIONS:
            for path in root.rglob(f"*{extension}"):
                if is_mmproj(path):
                    continue
                try:
                    size = path.stat().st_size
                except OSError:  # pragma: no cover - 扫描期间文件被删除
                    continue
                if size < min_bytes:
                    continue
                resolved = path.resolve()
                key = _llmconfig.path_key(resolved)
                if key in seen:
                    continue
                role = classify_role(resolved)
                family = detect_family(resolved)
                if role == "unknown" and family == "unknown":
                    continue
                seen.add(key)
                assets.append(PaintAsset(resolved, role, family, size))
    order = {role: index for index, role in enumerate(ROLE_LABELS)}
    assets.sort(key=lambda item: (order.get(item.role, len(order)), -item.size, str(item.path)))
    roots = tuple(dict.fromkeys(root.resolve() for root in resolved_roots))
    return PaintAssets(tuple(assets), roots)


def suggest_components(assets: PaintAssets, model: Path | None = None) -> PaintComponents:
    """按家族槽位给出默认组件（每个槽位优先同家族中最大的文件）。"""
    if model is not None:
        selected = assets.get(model)
        main_path = selected.path if selected is not None else model
        family_key = selected.family if selected is not None else detect_family(model)
        family = FAMILIES.get(family_key, FAMILIES["unknown"])
    else:
        main = next(iter(assets.main_models()), None)
        if main is None:
            return PaintComponents()
        main_path = main.path
        family_key = main.family
        family = FAMILIES.get(family_key, FAMILIES["unknown"])
    chosen: dict[str, Path] = {}
    for role in family.slots:
        candidates = assets.by_role(role)
        # 组件同样可能来自多个家族（例如同时装了 SDXL 与 Qwen-Image 的 VAE）：
        # 先挑同家族的，避免「自动配对」把不兼容的 VAE / 文本编码器配上去导致
        # sd-server 启动即退出；同家族没有时才回退到该角色的第一个候选。
        same_family = [asset for asset in candidates if asset.family == family_key]
        candidate = (same_family or list(candidates))[0] if candidates else None
        if candidate is not None:
            chosen[role] = candidate.path
    return PaintComponents(
        model=main_path,
        vae=chosen.get("vae"),
        clip_l=chosen.get("clip_l"),
        clip_g=chosen.get("clip_g"),
        t5xxl=chosen.get("t5xxl"),
        llm=chosen.get("llm"),
        mode="auto",
    )


@dataclass(frozen=True, slots=True)
class PaintRequest:
    """一次文生图请求（与 sd.cpp 的 A1111 兼容接口对齐）。"""

    prompt: str
    negative: str = ""
    width: int = 512
    height: int = 512
    steps: int = 25
    cfg_scale: float = 7.0
    seed: int = -1
    sampler: str = "euler_a"
    scheduler: str = ""
    batch: int = 1


@dataclass(frozen=True, slots=True)
class PaintComponents:
    """启动参数里的模型组件集合。"""

    model: Path | None = None
    vae: Path | None = None
    clip_l: Path | None = None
    clip_g: Path | None = None
    t5xxl: Path | None = None
    llm: Path | None = None
    lora: Path | None = None
    mode: str = "auto"
    extra_args: tuple[str, ...] = ()
    offload_to_cpu: bool = False
    vae_tiling: bool = False
    diffusion_fa: bool = False


def effective_mode(components: PaintComponents) -> str:
    """生效的加载方式：显式选择优先，否则按主模型家族推断。"""
    if components.mode in {"checkpoint", "diffusion"}:
        return components.mode
    if components.model is not None:
        return FAMILIES.get(detect_family(components.model), FAMILIES["unknown"]).mode
    return "checkpoint"


def build_model_args(components: PaintComponents) -> list[str]:
    """构造 sd-server 的模型加载参数（未选组件不输出）。"""
    if components.model is None:
        return []
    flag = "-m" if effective_mode(components) == "checkpoint" else "--diffusion-model"
    args = [flag, str(components.model)]
    for flag_name, path in (
        ("--vae", components.vae),
        ("--clip_l", components.clip_l),
        ("--clip_g", components.clip_g),
        ("--t5xxl", components.t5xxl),
        ("--llm", components.llm),
    ):
        if path is not None:
            args.extend([flag_name, str(path)])
    args.extend(server_flags(components))
    args.extend(components.extra_args)
    return args


def server_flags(components: PaintComponents) -> list[str]:
    """显存 / 画质相关的可选开关。"""
    flags: list[str] = []
    if components.offload_to_cpu:
        flags.append("--offload-to-cpu")
    if components.vae_tiling:
        flags.append("--vae-tiling")
    if components.diffusion_fa:
        flags.append("--diffusion-fa")
    return flags


def build_payload(request: PaintRequest) -> dict[str, object]:
    """构造 ``POST /sdapi/v1/txt2img`` 的 JSON 载荷。"""
    payload: dict[str, object] = {
        "prompt": request.prompt,
        "negative_prompt": request.negative,
        "width": request.width,
        "height": request.height,
        "steps": request.steps,
        "cfg_scale": request.cfg_scale,
        "seed": request.seed,
        "batch_size": request.batch,
        "n_iter": 1,
    }
    if request.sampler:
        payload["sampler_name"] = request.sampler
    if request.scheduler:
        payload["scheduler"] = request.scheduler
    return payload


def missing_required_slots(components: PaintComponents) -> tuple[str, ...]:
    """返回当前主模型家族仍缺少的必需组件（``llm`` / ``vae``）。"""
    if components.model is None:
        return ()
    family = detect_family(components.model)
    required = _REQUIRED_SLOTS.get(family, ())
    return tuple(role for role in required if getattr(components, role, None) is None)


def validate_components(components: PaintComponents) -> None:
    """校验组件选择；不合法时抛 :class:`ServeError`。"""
    if components.model is None:
        raise ServeError("请选择主模型（checkpoint 或 diffusion model）")
    if not components.model.is_file():
        raise ServeError(f"主模型不存在: {components.model}")
    if components.mode not in {"auto", "checkpoint", "diffusion"}:
        raise ServeError(f"未知的加载方式: {components.mode!r}")
    missing = missing_required_slots(components)
    if missing:
        labels = {"vae": "VAE", "llm": "文本编码器 (--llm)"}
        detail = "；".join(f"{labels.get(role, role)}：{_COMPANION_HINTS.get(role, '')}" for role in missing)
        raise ServeError(
            f"主模型 {components.model.name} 缺少必需组件："
            f"{'、'.join(labels.get(role, role) for role in missing)}。 可下载：{detail}"
        )
    for label, path in (
        ("VAE", components.vae),
        ("CLIP-L", components.clip_l),
        ("CLIP-G", components.clip_g),
        ("T5-XXL", components.t5xxl),
        ("文本编码器", components.llm),
        ("LoRA", components.lora),
    ):
        if path is not None and not path.is_file():
            raise ServeError(f"{label} 文件不存在: {path}")


def validate_request(request: PaintRequest) -> None:
    """校验提示词与采样参数。"""
    if not request.prompt.strip():
        raise ServeError("提示词不能为空")
    for label, value in (("宽度", request.width), ("高度", request.height)):
        if value < 64 or value > 4096 or value % 8:
            raise ServeError(f"{label} 必须是 64–4096 之间、能被 8 整除的整数: {value}")
    if not 1 <= request.steps <= 150:
        raise ServeError(f"步数必须在 1–150 之间: {request.steps}")
    if not 0 <= request.cfg_scale <= 30:
        raise ServeError(f"CFG 必须在 0–30 之间: {request.cfg_scale}")
    if request.seed < -1:
        raise ServeError(f"种子必须 ≥ -1（-1 表示随机）: {request.seed}")
    if not 1 <= request.batch <= 8:
        raise ServeError(f"批量张数必须在 1–8 之间: {request.batch}")


def decode_image(item: str) -> bytes:
    """解码接口返回的图片：裸 base64 或 ``data:image/...;base64,`` 均可。"""
    text = item.strip()
    if text.startswith("data:"):
        _, _, text = text.partition(",")
    text = "".join(text.split())
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ServeError(f"图片数据不是合法的 base64：{exc}") from exc
    if not data:
        raise ServeError("图片数据为空")
    return data


def _unique_path(path: Path) -> Path:
    """同名文件已存在时追加序号，避免同一秒内覆盖。"""
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path


def save_images(
    items: Sequence[str],
    out_dir: Path,
    *,
    seed: int = -1,
    now: datetime | None = None,
) -> list[Path]:
    """把接口返回的图片写入输出目录，返回保存路径。"""
    if not items:
        raise ServeError("接口没有返回图片")
    out_dir = out_dir.expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    suffix = f"-s{seed}" if seed >= 0 else ""
    paths: list[Path] = []
    for index, item in enumerate(items):
        data = decode_image(item)
        path = _unique_path(out_dir / f"paint-{stamp}-{index:02d}{suffix}.png")
        path.write_bytes(data)
        paths.append(path)
    return paths


def gallery(out_dir: Path, limit: int = 12) -> tuple[Path, ...]:
    """最近出图（按修改时间倒序）。"""
    if not out_dir.is_dir():
        return ()
    files = [path for path in out_dir.glob("paint-*.png") if path.is_file()]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return tuple(files[: max(limit, 0)])


def describe_asset(asset: PaintAsset) -> str:
    """表格 / 下拉用的单行描述。"""
    return f"{asset.family_label} · {asset.role_label} · {human_size(asset.size)}"
