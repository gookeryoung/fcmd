"""llmmodel - 本地 GGUF 模型扫描、定位与显存估算。

独立命令：``llmmodel <list|find|vram>``。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import fcmd
from fcmd.console import Table, get_console
from fcmd.tools.llm import llmenv
from fcmd.tools.llm.llmbase import (
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

__all__ = [
    "GENERAL_TAG",
    "MODEL_TAGS",
    "ModelTag",
    "detect_model_tags",
    "estimate_vram",
    "find_model",
    "model_advice",
    "plan_vram",
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
        "多模态需要配套 mmproj：llama-server 须加 --mmproj；当前启动器未注入，仅文本可用",
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
    for entry in entries:
        table.add_row(entry.relative, human_size(entry.size), ", ".join(entry.profiles) or "-")
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
