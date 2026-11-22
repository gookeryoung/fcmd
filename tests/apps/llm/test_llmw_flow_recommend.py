"""工作流智能推荐：场景 / 属性推导、模型组合选择与参数写回口径。"""

from __future__ import annotations

from pathlib import Path

from fcmd.apps.llm._llmw_flow import FlowGraph
from fcmd.apps.llm._llmw_flow_nodes import IMAGE_SAVE, IMAGE_TXT2IMG, LLM_CHAT, TEXT_INPUT, TEXT_SAVE
from fcmd.apps.llm._llmw_flow_recommend import (
    SCENES,
    detect_attribute,
    detect_scene,
    recommend_flows,
)
from fcmd.apps.llm._llmw_flow_templates import builtin_templates
from fcmd.apps.llm._llmw_hardware import HardwareSnapshot
from fcmd.apps.llm.llmbase import ModelEntry


def _flow(*steps: tuple[str, dict[str, object]]) -> FlowGraph:
    return FlowGraph.from_steps("测试", list(steps))


def _models(tmp_path: Path) -> list[ModelEntry]:
    return [
        ModelEntry(
            root=tmp_path,
            path=tmp_path / "qwen3-8b-q4_k_m.gguf",
            size=5_000_000_000,
            profiles=("chat",),
        ),
        ModelEntry(
            root=tmp_path,
            path=tmp_path / "qwen3-30b-a3b-q5_k_m.gguf",
            size=20_000_000_000,
            profiles=("chat",),
        ),
    ]


def test_detect_scene_from_builtin_templates() -> None:
    scenes = {template.name: detect_scene(template) for template in builtin_templates()}
    assert scenes["文生图"] == "image"
    assert scenes["提示词润色 → 出图"] == "prompt-image"
    assert scenes["LLM 问答 → 保存"] == "chat"
    assert SCENES["prompt-image"]


def test_detect_attribute_from_image_params() -> None:
    quality = _flow((TEXT_INPUT, {}), (IMAGE_TXT2IMG, {"width": 1024, "height": 768, "steps": 28}))
    speed = _flow((TEXT_INPUT, {}), (IMAGE_TXT2IMG, {"width": 512, "height": 512, "steps": 16}))
    balanced = _flow((TEXT_INPUT, {}), (IMAGE_TXT2IMG, {"width": 768, "height": 768, "steps": 24}))
    assert detect_attribute(quality) == "quality"
    assert detect_attribute(speed) == "speed"
    assert detect_attribute(balanced) == "balanced"


def test_recommend_prompt_image_picks_llm_and_matching_image(tmp_path: Path) -> None:
    flow = builtin_templates()[1]
    image = tmp_path / "flux1-dev.safetensors"
    rows = recommend_flows(flow, _models(tmp_path), HardwareSnapshot(gpu_mib=16_384), image_paths=[image])
    assert rows
    best = rows[0]
    assert best.scene == "prompt-image"
    assert best.llm_name
    assert best.llm_path is not None
    assert best.image_name == "flux1-dev"
    assert best.llm_system
    assert best.width >= 768
    assert best.score_parts[0] > 0
    assert best.score == max(row.score for row in rows)


def test_recommend_is_attribute_sensitive(tmp_path: Path) -> None:
    flow = _flow((TEXT_INPUT, {}), (IMAGE_TXT2IMG, {"width": 512, "height": 512, "steps": 16}))
    image = tmp_path / "sdxl-base.safetensors"
    speed = recommend_flows(flow, [], HardwareSnapshot(), image_paths=[image], attribute="speed")
    quality = recommend_flows(flow, [], HardwareSnapshot(), image_paths=[image], attribute="quality")
    assert speed and quality
    assert speed[0].steps < quality[0].steps
    assert speed[0].width <= quality[0].width
    assert quality[0].score > speed[0].score


def test_recommend_image_without_scanned_model_still_gives_params(tmp_path: Path) -> None:
    flow = _flow((TEXT_INPUT, {}), (IMAGE_TXT2IMG, {}))
    rows = recommend_flows(flow, [], HardwareSnapshot(), image_paths=[])
    assert len(rows) == 1
    assert rows[0].image_path is None
    assert rows[0].image_name == ""
    assert rows[0].width == 768
    assert "未选 LLM" not in rows[0].model_text
    assert rows[0].score_detail == "出图 60"


def test_recommend_returns_empty_for_text_or_missing_llm(tmp_path: Path) -> None:
    text_only = _flow((TEXT_INPUT, {}), (TEXT_SAVE, {}))
    assert recommend_flows(text_only, _models(tmp_path), HardwareSnapshot()) == ()
    chat_only = _flow((TEXT_INPUT, {}), (LLM_CHAT, {}))
    assert recommend_flows(chat_only, [], HardwareSnapshot()) == ()
    assert recommend_flows(chat_only, _models(tmp_path), HardwareSnapshot())


def test_recommend_skips_unfit_models(tmp_path: Path) -> None:
    """显存装不下最大模型时，候选仍应来自能放下的模型。"""
    flow = _flow((TEXT_INPUT, {}), (LLM_CHAT, {}))
    rows = recommend_flows(flow, _models(tmp_path), HardwareSnapshot(gpu_mib=8_192))
    assert rows
    assert all(row.llm_path is not None for row in rows)
    assert all(row.llm_path != tmp_path / "qwen3-30b-a3b-q5_k_m.gguf" for row in rows if row.llm_path)


def test_image_save_node_does_not_affect_scene() -> None:
    flow = _flow((TEXT_INPUT, {}), (IMAGE_TXT2IMG, {}), (IMAGE_SAVE, {}))
    assert detect_scene(flow) == "image"
