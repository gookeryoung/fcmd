"""工作流智能推荐：按场景 / 任务属性 / 现有参数匹配模型组合与出图参数。

输入是工作流节点链、``llmenv`` 扫出的 LLM 模型、硬件快照、已记录的实测结果与
本地扩散模型路径；输出「LLM 模型 + 出图模型 + 节点参数」的组合方案，按适配度
排序。场景与属性都从节点链推导，不额外增加需要用户维护的配置字段：

- 场景：``text.input → llm.chat → image.txt2img`` 为提示词润色出图，
  只有 ``image.txt2img`` 为文生图，只有 ``llm.chat`` 为问答，其余为文本处理；
- 任务属性：分辨率 / 步数偏大视为「画质优先」，偏小视为「速度优先」，其余均衡；
  用户可在页面上临时切换，应用推荐后参数会写回节点，再次推导结果一致。

LLM 侧复用 :func:`fcmd.apps.llm._llmw_recommend.recommend` 的显存 / 上下文 / KV /
性能评分，不重复模型筛选；出图侧按家族与任务属性打 0-100 分。本模块不依赖 GUI，
可直接单测。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fcmd.apps.llm import _llmpaint
from fcmd.apps.llm._llmw_flow_model import FlowGraph
from fcmd.apps.llm._llmw_flow_nodes import IMAGE_TXT2IMG, LLM_CHAT
from fcmd.apps.llm._llmw_hardware import HardwareSnapshot
from fcmd.apps.llm._llmw_recommend import Recommendation, recommend, score_band
from fcmd.apps.llm.llmbase import ModelEntry

__all__ = [
    "ATTRIBUTES",
    "SCENES",
    "FlowRecommendation",
    "detect_attribute",
    "detect_scene",
    "recommend_flows",
]

#: 场景 id → 展示名；顺序即页面选项顺序。
SCENES: dict[str, str] = {
    "prompt-image": "提示词润色 → 出图",
    "image": "文生图",
    "chat": "LLM 问答",
    "text": "文本处理",
}

#: 任务属性：``(key, 标题, 说明)``。
ATTRIBUTES: tuple[tuple[str, str, str], ...] = (
    ("quality", "画质优先", "更大分辨率与步数"),
    ("balanced", "均衡", "兼顾速度与画质"),
    ("speed", "速度优先", "小尺寸少步数"),
)

_ATTRIBUTE_LABELS = {key: title for key, title, _desc in ATTRIBUTES}

#: 「重」家族出图慢但画质上限高；「快」家族步数少、适合快速预览。
_HEAVY_FAMILIES = frozenset({"flux2", "hunyuan-image", "minimax-h3", "hidream", "qwen-image", "z-image"})
_FAST_FAMILIES = frozenset({"sd15", "sd2", "unknown"})

#: 出图模型评分：家族与任务属性的匹配度（0-100）。
_IMAGE_SCORES: dict[str, dict[str, int]] = {
    "quality": {"heavy": 96, "mid": 88, "fast": 62},
    "balanced": {"heavy": 88, "mid": 92, "fast": 78},
    "speed": {"heavy": 62, "mid": 82, "fast": 96},
}
_NO_IMAGE_SCORE = 60
"""没有扫到扩散模型时的中性分：仍给出参数建议，只是不选具体模型。"""

#: LLM 系统提示：只在「提示词润色 → 出图」场景注入，避免覆盖问答场景的自定义人设。
_PROMPT_SYSTEM = "你是提示词工程师。把用户输入改写成适合文生图模型的详细英文提示词，只输出提示词本身。"


@dataclass(frozen=True, slots=True)
class FlowRecommendation:
    """一条工作流模型组合方案：LLM 模型 + 出图模型 + 节点参数。"""

    scene: str
    scene_label: str
    attribute: str
    attribute_label: str
    score: int
    score_parts: tuple[int, int]
    """``(LLM 适配分, 出图适配分)``；没有对应节点时该项为 100。"""
    llm_name: str
    llm_path: Path | None
    llm_profile: str
    llm_context: int
    llm_temperature: float
    llm_max_tokens: int
    llm_system: str
    image_name: str
    image_path: Path | None
    width: int
    height: int
    steps: int
    cfg_scale: float
    rationale: str
    basis: str

    @property
    def score_band(self) -> str:
        """评分档位 id（``excellent`` / ``good`` / ``fair`` / ``limited``）。"""
        return score_band(self.score)

    @property
    def score_text(self) -> str:
        """``适配 86/100``，供页面状态胶囊使用。"""
        return f"适配 {self.score}/100"

    @property
    def model_text(self) -> str:
        """模型组合摘要：``Qwen3-8B · flux1-dev``，只列出工作流实际用到的模型。"""
        parts: list[str] = []
        if self.scene in {"prompt-image", "chat"}:
            parts.append(self.llm_name or "未选 LLM")
        if self.scene in {"prompt-image", "image"}:
            parts.append(self.image_name or "沿用画图页模型")
        return " · ".join(parts)

    @property
    def config_text(self) -> str:
        """参数摘要：``1024×1024 · 28 步 · CFG 3.5``。"""
        return f"{self.width}×{self.height} · {self.steps} 步 · CFG {self.cfg_scale:g}"

    @property
    def score_detail(self) -> str:
        """评分明细，只列有节点的一侧：``LLM 84 · 出图 92``。"""
        if self.scene == "image":
            return f"出图 {self.score_parts[1]}"
        if self.scene == "chat":
            return f"LLM {self.score_parts[0]}"
        return f"LLM {self.score_parts[0]} · 出图 {self.score_parts[1]}"


def detect_scene(flow: FlowGraph) -> str:
    """按节点类型判定工作流场景 id（见 :data:`SCENES`）。"""
    types = {node.type for node in flow.nodes}
    has_llm = LLM_CHAT in types
    has_image = IMAGE_TXT2IMG in types
    if has_llm and has_image:
        return "prompt-image"
    if has_image:
        return "image"
    if has_llm:
        return "chat"
    return "text"


def detect_attribute(flow: FlowGraph) -> str:
    """从出图节点的分辨率 / 步数推导任务属性；没有出图节点时按 LLM 输出上限判断。"""
    image = next((node for node in flow.nodes if node.type == IMAGE_TXT2IMG), None)
    if image is not None:
        width = _as_int(image.params.get("width"), 512)
        height = _as_int(image.params.get("height"), 512)
        steps = _as_int(image.params.get("steps"), 25)
        longest = max(width, height)
        if longest >= 1024 or steps >= 30:
            return "quality"
        if longest <= 512 and steps <= 20:
            return "speed"
        return "balanced"
    chat = next((node for node in flow.nodes if node.type == LLM_CHAT), None)
    if chat is not None and _as_int(chat.params.get("max_tokens"), 2048) >= 2048:
        return "quality"
    return "balanced"


def recommend_flows(  # noqa: PLR0913 - 输入源成组透传，保持调用口径与概览页一致
    flow: FlowGraph,
    models: Sequence[ModelEntry],
    hardware: HardwareSnapshot | None,
    measured: Mapping[str, Mapping[str, Any]] | None = None,
    image_paths: Sequence[Path] = (),
    *,
    attribute: str = "",
    limit: int = 3,
) -> tuple[FlowRecommendation, ...]:
    """返回按适配度排序的模型组合方案；工作流没有模型节点时返回空。

    ``attribute`` 为空时按 :func:`detect_attribute` 推导；``image_paths`` 是本地
    扩散模型路径（运行页扫描结果），为空时只给参数建议、不指定出图模型。
    """
    scene = detect_scene(flow)
    if scene == "text":
        return ()
    attr = attribute if attribute in _ATTRIBUTE_LABELS else detect_attribute(flow)
    needs_llm = scene in {"prompt-image", "chat"}
    needs_image = scene in {"prompt-image", "image"}

    llm_rows: tuple[Recommendation, ...] = (
        recommend(models, hardware, measured, limit=max(limit, 1)) if needs_llm else ()
    )
    if needs_llm and not llm_rows:
        return ()

    image_path, image_family = _pick_image(image_paths, attr) if needs_image else (None, "")
    image_score = _IMAGE_SCORES[attr][_family_group(image_family)] if image_path is not None else _NO_IMAGE_SCORE
    preset = _image_preset(attr, image_family)
    llm_temperature, llm_max_tokens, llm_system = _llm_params(scene, attr)
    image_name = image_path.stem if image_path is not None else ""

    combos: list[FlowRecommendation] = []
    for row in llm_rows or (None,):
        llm_score = row.score if row is not None else 100
        if needs_llm and needs_image:
            score = round(llm_score * 0.6 + image_score * 0.4)
        elif needs_llm:
            score = llm_score
        else:
            score = image_score
        combos.append(
            FlowRecommendation(
                scene=scene,
                scene_label=SCENES[scene],
                attribute=attr,
                attribute_label=_ATTRIBUTE_LABELS[attr],
                score=score,
                score_parts=(llm_score, image_score if needs_image else 100),
                llm_name=row.model.path.stem if row is not None else "",
                llm_path=row.model.path if row is not None else None,
                llm_profile=row.profile if row is not None else "",
                llm_context=row.context if row is not None else 0,
                llm_temperature=llm_temperature,
                llm_max_tokens=llm_max_tokens,
                llm_system=llm_system,
                image_name=image_name,
                image_path=image_path,
                width=preset[0],
                height=preset[1],
                steps=preset[2],
                cfg_scale=preset[3],
                rationale=_rationale(scene, attr, has_image_model=image_path is not None),
                basis=_basis(row, image_path, image_family),
            )
        )
    combos.sort(key=lambda item: (-item.score, item.llm_name, item.image_name))
    return tuple(combos[:limit])


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _family_group(family: str) -> str:
    if family in _HEAVY_FAMILIES:
        return "heavy"
    if family in _FAST_FAMILIES:
        return "fast"
    return "mid"


def _pick_image(image_paths: Sequence[Path], attribute: str) -> tuple[Path | None, str]:
    """按属性挑第一个家族匹配度最高的扩散模型；同分时保持传入顺序。"""
    best: tuple[int, int, Path, str] | None = None
    for index, path in enumerate(image_paths):
        family = _llmpaint.detect_family(path)
        score = _IMAGE_SCORES[attribute][_family_group(family)]
        key = (score, -index)
        if best is None or key > (best[0], best[1]):
            best = (score, -index, path, family)
    if best is None:
        return None, ""
    return best[2], best[3]


def _image_preset(attribute: str, family: str) -> tuple[int, int, int, float]:
    """出图参数：``(宽, 高, 步数, CFG)``，按家族上限压住分辨率。"""
    group = _family_group(family) if family else "mid"
    if group == "heavy":
        return {
            "quality": (1024, 1024, 28, 3.5),
            "balanced": (768, 768, 24, 3.5),
            "speed": (512, 512, 16, 2.5),
        }[attribute]
    if group == "fast":
        return {
            "quality": (768, 768, 28, 7.0),
            "balanced": (512, 512, 22, 7.0),
            "speed": (512, 512, 16, 6.0),
        }[attribute]
    return {
        "quality": (1024, 1024, 30, 7.0),
        "balanced": (768, 768, 25, 7.0),
        "speed": (512, 512, 20, 7.0),
    }[attribute]


def _llm_params(scene: str, attribute: str) -> tuple[float, int, str]:
    """LLM 节点参数：``(温度, 输出上限, 系统提示)``。"""
    if scene == "prompt-image":
        temperature = {"quality": 0.35, "balanced": 0.45, "speed": 0.55}[attribute]
        return temperature, {"quality": 512, "balanced": 384, "speed": 256}[attribute], _PROMPT_SYSTEM
    temperature = {"quality": 0.6, "balanced": 0.7, "speed": 0.8}[attribute]
    return temperature, {"quality": 2048, "balanced": 1024, "speed": 768}[attribute], ""


def _rationale(scene: str, attribute: str, *, has_image_model: bool) -> str:
    parts = [f"{SCENES[scene]}按「{_ATTRIBUTE_LABELS[attribute]}」匹配"]
    if scene == "prompt-image":
        parts.append("润色后直接出图")
    if attribute == "speed":
        parts.append("优先响应速度")
    elif attribute == "quality":
        parts.append("优先画质上限")
    if not has_image_model:
        parts.append("出图模型沿用画图页当前选择")
    return "；".join(parts)


def _basis(row: Recommendation | None, image_path: Path | None, family: str) -> str:
    llm_basis = row.basis if row is not None else "无 LLM 节点"
    image_basis = (
        f"{_llmpaint.FAMILIES.get(family, _llmpaint.FAMILIES['unknown']).label} · {image_path.stem}"
        if image_path is not None
        else "未检测到扩散模型"
    )
    return f"{llm_basis} · {image_basis}"
