"""llmmodel - 本地 GGUF 模型扫描、定位与显存估算。

独立命令：``llmmodel <list|find|vram>``。
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import fcmd
from fcmd.apps.locllm import llmenv
from fcmd.apps.locllm.llmbase import (
    DEFAULT_PROFILE,
    KV_BYTES_PER_TOKEN_FP16,
    KV_SCALE,
    PROFILES,
    VRAM_OVERHEAD_BYTES,
    LaunchPlan,
    ModelEntry,
    ServeError,
    human_size,
)
from fcmd.console import Table, get_console

__all__ = [
    "GENERAL_TAG",
    "MMPROJ_MIN_SCORE",
    "MODEL_TAGS",
    "MmprojCandidate",
    "ModelTag",
    "VramFit",
    "detect_model_tags",
    "estimate_vram",
    "find_mmproj",
    "find_model",
    "fit_vram",
    "max_context",
    "model_advice",
    "pair_score",
    "plan_vram",
    "resolve_mmproj",
    "scan_models",
]


@dataclass(frozen=True, slots=True)
class ModelTag:
    """模型能力标签：文件名关键词、推荐档位与最佳实践建议。"""

    name: str
    keywords: tuple[str, ...]
    profile: str
    hint: str


#: 兜底标签：没有具体能力关键词但命中主流模型系列时使用
GENERAL_TAG = "通用"

#: 内置能力标签（按展示顺序排列；具体能力可命中多个，“通用”仅作兜底）
MODEL_TAGS: dict[str, ModelTag] = {
    "代码": ModelTag(
        "代码",
        ("coder", "code", "codestral"),
        "balanced",
        "代码补全/改码建议 temperature 0.2、top_p 0.95；仓库级长上下文用 long 档",
    ),
    "推理": ModelTag(
        "推理",
        ("reasoning", "think", "qwq", "deepseek-r1", "-r1-", "r1-distill", "magistral"),
        "long",
        "推理模型会输出思考链：用 long/max-context 档，temperature 0.6、top_p 0.95，等待就绪放宽到 300s 以上",
    ),
    "对话": ModelTag(
        "对话",
        ("instruct", "chat", "-it-", "sft", "dpo", "assistant"),
        "balanced",
        "通用对话建议 temperature 0.7、top_p 0.9；多轮长会话关注 KV 使用率",
    ),
    "视觉": ModelTag(
        "视觉",
        ("vision", "llava", "minicpm-v", "internvl", "-vl-", "vl-", "pixtral", "molmo", "paligemma"),
        "balanced",
        "多模态需要配套 mmproj：启动时加 --mmproj（llmserve 默认 auto 自动配对同目录视觉塔），错配会触发降级为纯文本",
    ),
    "数学": ModelTag(
        "数学",
        ("math", "minerva"),
        "balanced",
        "数学解题建议 temperature 0.2，并在提示中要求逐步推导与自检",
    ),
    "重排": ModelTag(
        "重排",
        ("rerank",),
        "balanced",
        "重排模型需开启 --reranking，客户端调用 /v1/rerank，不要用于 /v1/chat/completions",
    ),
    "嵌入": ModelTag(
        "嵌入",
        ("embed", "bge-m3", "gte-", "e5-", "jina", "mxbai", "stella"),
        "balanced",
        "嵌入模型不是对话模型：llama-server 需开启 --embeddings，客户端调用 /v1/embeddings",
    ),
    GENERAL_TAG: ModelTag(
        GENERAL_TAG,
        (
            "qwen",
            "llama",
            "mistral",
            "mixtral",
            "gemma",
            "deepseek",
            "glm",
            "kimi",
            "moonshot",
            "minimax",
            "phi-",
            "yi-",
            "baichuan",
            "internlm",
            "hunyuan",
            "ernie",
            "nemotron",
            "granite",
            "olmo",
            "exaone",
            "seed-oss",
            "gpt-oss",
            "command-r",
            "falcon",
            "mamba",
            "rwkv",
            "smollm",
            "stablelm",
            "zephyr",
            "vicuna",
            "wizard",
            "dolphin",
            "hermes",
            "solar",
            "openchat",
            "orca",
            "tulu",
        ),
        "balanced",
        "主流通用模型：balanced 档起步，temperature 0.7、top_p 0.9；启动器已开启 --jinja，"
        "客户端直接用 /v1/chat/completions；长文档再升 long 档",
    ),
}


def detect_model_tags(name: str) -> tuple[str, ...]:
    """识别模型能力标签：具体能力优先；都没有但命中主流系列时兜底为“通用”。"""
    lowered = name.lower()
    specific = tuple(
        tag.name
        for tag in MODEL_TAGS.values()
        if tag.name != GENERAL_TAG and any(keyword in lowered for keyword in tag.keywords)
    )
    if specific:
        return specific
    general = MODEL_TAGS[GENERAL_TAG]
    return (GENERAL_TAG,) if any(keyword in lowered for keyword in general.keywords) else ()


def model_advice(tags: Sequence[str]) -> str:
    """按能力标签生成推荐档位与最佳实践建议；未识别标签时给出通用建议。"""
    hits = [MODEL_TAGS[name] for name in tags if name in MODEL_TAGS]
    if not hits:
        return "未识别能力标签：按 balanced 档起步，观察 tok/s 与 KV 使用率后再调档"
    profiles = dict.fromkeys(tag.profile for tag in hits)
    parts = [f"推荐档位 {'/'.join(profiles)}", *(f"{tag.name}: {tag.hint}" for tag in hits)]
    return "；".join(parts)


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
            entries.append(
                ModelEntry(
                    root=root,
                    path=resolved,
                    size=size,
                    profiles=profiles,
                    tags=detect_model_tags(path.name),
                )
            )
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


#: 自动配对视觉塔的最低分；低于该值视为“无匹配”，避免误配
MMPROJ_MIN_SCORE = 50
#: 视觉塔文件与模型文件名中的噪音 token（不参与相似度计算）
_MMPROJ_NOISE = frozenset({"mmproj", "projector", "f16", "bf16", "f32", "gguf", "model", "vision", "vit", "clip"})
_SIZE_MARKER = re.compile(r"(?<![a-z0-9])(\d+(?:\.\d+)?)b(?![a-z])", re.IGNORECASE)
_QUANT_TOKEN = re.compile(r"i?q\d+(_[a-z0-9]+)*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class MmprojCandidate:
    """一个视觉塔（mmproj）候选及其配对依据。"""

    path: Path
    score: int
    reasons: tuple[str, ...] = ()


def _name_tokens(name: str) -> set[str]:
    """把文件名切成有意义的 token（去掉量化档、mmproj 标记等噪音）。"""
    tokens = {token for token in re.split(r"[^a-z0-9]+", name.lower()) if token}
    return {token for token in tokens if token not in _MMPROJ_NOISE and not _QUANT_TOKEN.fullmatch(token)}


def _size_markers(name: str) -> set[str]:
    """提取文件名中的参数量标记（如 ``8b``、``30b-a3b`` 里的 ``8``/``30``/``3``）。"""
    return {match.group(1).lower() for match in _SIZE_MARKER.finditer(name.lower())}


def pair_score(model: Path, mmproj: Path) -> tuple[int, tuple[str, ...]]:
    """给“主模型 ↔ 视觉塔”配对打分；返回 ``(分数, 依据)``。"""
    score = 0
    reasons: list[str] = []
    if model.parent == mmproj.parent:
        score += 40
        reasons.append("同目录 +40")
    elif mmproj.parent.name.lower() == "mmproj" and mmproj.parent.parent == model.parent:
        score += 30
        reasons.append("同目录 mmproj/ 子目录 +30")
    model_tokens = _name_tokens(model.stem)
    cand_tokens = _name_tokens(mmproj.stem)
    if model_tokens and cand_tokens:
        overlap = len(model_tokens & cand_tokens)
        ratio = overlap / len(model_tokens | cand_tokens)
        if ratio > 0:
            gained = round(30 * ratio)
            score += gained
            reasons.append(f"文件名相似 {ratio:.0%} +{gained}")
    elif model.parent == mmproj.parent:
        score += 15
        reasons.append("同目录且无同名特征 +15")
    model_sizes = _size_markers(model.name)
    cand_sizes = _size_markers(mmproj.name)
    if model_sizes and cand_sizes:
        if model_sizes & cand_sizes or model_sizes == cand_sizes:
            score += 20
            reasons.append("参数量一致 +20")
        elif model_sizes.isdisjoint(cand_sizes):
            score -= 100
            reasons.append(f"参数量冲突 {sorted(model_sizes)} vs {sorted(cand_sizes)} -100")
    if any(token in mmproj.stem.lower() for token in ("f16", "bf16", "f32")):
        score += 5
        reasons.append("projector 高精度 +5")
    try:
        if mmproj.stat().st_size < 20 * 1024 * 1024:
            score -= 20
            reasons.append("文件过小 -20")
    except OSError:  # pragma: no cover - 扫描期间文件被删除
        pass
    return score, tuple(reasons)


def _mmproj_candidates(model: Path) -> list[Path]:
    """收集候选视觉塔：模型同目录 → 同目录 ``mmproj/`` 子目录。"""
    found: list[Path] = []
    seen: set[str] = set()

    def add(paths: Sequence[Path]) -> None:
        for path in paths:
            name = path.name.lower()
            if path == model or path.suffix.lower() != ".gguf":
                continue
            if "mmproj" not in name and "projector" not in name and path.parent.name.lower() != "mmproj":
                continue
            try:
                key = str(path.resolve())
            except OSError:  # pragma: no cover - 路径异常
                continue
            if key not in seen:
                seen.add(key)
                found.append(path)

    add(sorted(model.parent.glob("*mmproj*.gguf")))
    add(sorted(model.parent.glob("*projector*.gguf")))
    add(sorted((model.parent / "mmproj").glob("*.gguf")))
    return found


def find_mmproj(model: Path) -> MmprojCandidate | None:
    """为主模型寻找最佳视觉塔；分数低于阈值时返回 ``None``。"""
    candidates = _mmproj_candidates(model)
    if not candidates:
        return None
    scored: list[MmprojCandidate] = []
    for path in candidates:
        score, reasons = pair_score(model, path)
        scored.append(MmprojCandidate(path=path, score=score, reasons=reasons))
    scored.sort(key=lambda item: (-item.score, str(item.path)))
    best = scored[0]
    if len(scored) == 1 and best.score > 0 and best.path.parent == model.parent:
        best = MmprojCandidate(
            path=best.path,
            score=max(best.score, MMPROJ_MIN_SCORE),
            reasons=(*best.reasons, "唯一候选"),
        )
    if best.score < MMPROJ_MIN_SCORE:
        return None
    return best


def resolve_mmproj(model: Path, spec: str | Path | None) -> Path | None:
    """把 ``auto`` / ``off`` / 具体路径解析为视觉塔路径。"""
    text = str(spec or "auto").strip()
    lowered = text.lower()
    if lowered in {"", "auto"}:
        candidate = find_mmproj(model)
        return candidate.path if candidate is not None else None
    if lowered in {"off", "none", "0", "no"}:
        return None
    path = Path(text).expanduser()
    if not path.is_file():
        raise ServeError(f"视觉塔文件不存在: {path}")
    if path.suffix.lower() != ".gguf":
        raise ServeError(f"视觉塔必须是 .gguf 文件: {path}")
    return path.resolve()


def estimate_vram(model_bytes: int, context: int, kv_type: str = "q8_0") -> int:
    """估算显存占用（模型权重 + KV 缓存 + 固定开销）。"""
    kv = int(context * KV_BYTES_PER_TOKEN_FP16 * KV_SCALE.get(kv_type, 1.0))
    return model_bytes + kv + VRAM_OVERHEAD_BYTES


@dataclass(frozen=True, slots=True)
class VramFit:
    """模型在给定可用显存下的适配情况。"""

    needed: int
    available: int
    max_context: int

    @property
    def ok(self) -> bool:
        """预计占用不超过可用显存。"""
        return self.needed <= self.available


def max_context(model_bytes: int, gpu_bytes: int, kv_type: str = "q8_0") -> int:
    """估算可用显存下能容纳的最大上下文（向下取整到 1K，``0`` 表示权重都放不下）。"""
    free = gpu_bytes - model_bytes - VRAM_OVERHEAD_BYTES
    unit = KV_BYTES_PER_TOKEN_FP16 * KV_SCALE.get(kv_type, 1.0)
    if free <= 0 or unit <= 0:  # pragma: no cover - unit 恒为正，防御异常入参
        return 0
    return int(free / unit) // 1024 * 1024


def fit_vram(model_bytes: int, gpu_bytes: int, context: int, kv_type: str = "q8_0") -> VramFit:
    """判断模型在指定上下文 / KV 下能否放入可用显存，并给出可容纳的最大上下文。"""
    return VramFit(
        needed=estimate_vram(model_bytes, context, kv_type),
        available=gpu_bytes,
        max_context=max_context(model_bytes, gpu_bytes, kv_type),
    )


def plan_vram(plan: LaunchPlan) -> int:
    """估算启动计划的显存占用（含视觉塔）。"""
    try:
        size = plan.model.stat().st_size
    except OSError:
        size = 0
    if plan.mmproj is not None:
        with contextlib.suppress(OSError):  # pragma: no cover - 视觉塔在启动前被删除
            size += plan.mmproj.stat().st_size
    return estimate_vram(size, plan.context, plan.effective_kv_type)


@fcmd.tool("llmmodel", subcommand="list", help="列出模型目录中的 GGUF 文件")
def list_cmd(models_root: Path | None = None) -> None:
    """扫描模型目录并列出 GGUF 文件。

    Parameters
    ----------
    models_root:
        只扫描该目录；默认用 ``FCMD_MODELS``、配置文件或内置默认目录。
    """
    roots = llmenv.models_roots(models_root)
    missing = [root for root in roots if not root.is_dir()]
    existing = [root for root in roots if root.is_dir()]
    if not existing:
        raise ServeError(f"模型目录不存在: {', '.join(str(root) for root in roots)}；先用 fcmd llmdown download 下载")
    entries = scan_models(existing)
    if not entries:
        raise ServeError(
            f"{', '.join(str(root) for root in existing)} 下没有 .gguf 文件；先用 fcmd llmdown download 下载"
        )

    table = Table(title=f"本地模型（{len(entries)} 个）", box="ascii")
    table.add_column("文件", style="cyan", no_wrap=True)
    table.add_column("大小", justify="right", no_wrap=True)
    table.add_column("匹配档位")
    table.add_column("标签", style="magenta")
    for entry in entries:
        table.add_row(
            entry.relative, human_size(entry.size), ", ".join(entry.profiles) or "-", ", ".join(entry.tags) or "-"
        )
    get_console().print(table)

    get_console().print("各档位将选用的模型:")
    for name, profile in PROFILES.items():
        matched = [entry for entry in entries if profile.quant.lower() in entry.path.name.lower()]
        quant = f"{profile.quant:<7}"
        if matched:
            chosen = max(matched, key=lambda entry: entry.size)
            get_console().print(f"  {name:<12} {quant} {chosen.path}")
        else:
            get_console().print(f"  {name:<12} {quant} [yellow]缺失[/yellow]")

    for root in missing:
        get_console().print(f"[yellow]提示: 配置中的目录不存在 {root}[/yellow]")


@fcmd.tool("llmmodel", subcommand="find", help="按量化类型定位 GGUF 文件")
def find_cmd(quant: str, models_root: Path | None = None, model: Path | None = None) -> None:
    """输出匹配量化类型的 GGUF 路径（同名取最大）。

    Parameters
    ----------
    quant:
        量化类型，如 ``Q4_K_M``（按文件名匹配，大小写不敏感）。
    models_root:
        只在指定目录查找。
    model:
        指定 GGUF 文件或包含 GGUF 的目录，覆盖模型目录配置。
    """
    get_console().print(str(find_model(quant, models_root, model)), markup=False)


@fcmd.tool("llmmodel", subcommand="vram", help="估算指定 GGUF 的显存占用")
def vram_cmd(model: Path, ctx: int = PROFILES[DEFAULT_PROFILE].context, kv: str = "q8_0") -> None:
    """估算模型权重 + KV 缓存 + 固定开销的显存占用。

    Parameters
    ----------
    model:
        GGUF 文件路径。
    ctx:
        上下文长度（默认取 balanced 档位）。
    kv:
        KV 缓存类型（``f16`` / ``q8_0`` / ``q4_0``，默认 ``q8_0``）。
    """
    if kv not in KV_SCALE:
        raise ServeError(f"不支持的 KV 类型 {kv!r}，可选: {', '.join(KV_SCALE)}")
    target = model.expanduser()
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise ServeError(f"读取模型失败: {target}（{exc}）") from exc

    estimate = estimate_vram(size, ctx, kv)
    table = Table(title="显存估算", box="ascii")
    table.add_column("项目", style="cyan", no_wrap=True)
    table.add_column("值", justify="right", no_wrap=True)
    table.add_row("模型", str(target))
    table.add_row("模型大小", human_size(size))
    table.add_row("上下文", str(ctx))
    table.add_row("KV 类型", kv)
    table.add_row("预计占用", human_size(estimate))
    get_console().print(table)


@fcmd.main("llmmodel")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行
