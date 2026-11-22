"""内置工作流模板：文生图、提示词润色后出图、LLM 问答保存。

模板只提供起点（节点顺序 + 默认参数），用户在「工作流」页可继续增删节点 / 改参数；
服务地址默认对齐 llmw 的 llama.cpp（8080）与 sd.cpp（1234）。
"""

from __future__ import annotations

from pathlib import Path

from fcmd.apps.llm._llmw_flow_model import FlowGraph
from fcmd.apps.llm._llmw_flow_nodes import IMAGE_SAVE, IMAGE_TXT2IMG, LLM_CHAT, TEXT_INPUT, TEXT_SAVE
from fcmd.apps.llm._llmw_flow_registry import default_registry

__all__ = ["builtin_templates", "default_output_dir"]

_DESCRIPTIONS = {
    "文生图": "文本提示词 → sd.cpp 文生图 → 保存图片（先在运行页启动画图服务）",
    "提示词润色 → 出图": "文本 → LLM 润色 → sd.cpp 文生图 → 保存图片（先启动 LLM 与画图服务）",
    "LLM 问答 → 保存": "文本 → LLM 对话 → 保存为 Markdown / 文本文件（先启动 LLM 服务）",
}


def builtin_templates() -> list[FlowGraph]:
    """返回全新模板实例（每次调用重新生成 id，避免覆盖用户工作流）。"""
    templates = [
        FlowGraph.from_steps(
            "文生图",
            [
                (TEXT_INPUT, {"text": "a serene mountain lake at sunrise, ultra detailed"}),
                (IMAGE_TXT2IMG, {"width": 512, "height": 512, "steps": 25}),
                (IMAGE_SAVE, {"directory": str(default_output_dir())}),
            ],
            description=_DESCRIPTIONS["文生图"],
        ),
        FlowGraph.from_steps(
            "提示词润色 → 出图",
            [
                (TEXT_INPUT, {"text": "画一只在月球上喝咖啡的猫"}),
                (
                    LLM_CHAT,
                    {
                        "system": "你是提示词工程师。把用户输入改写成适合文生图模型的英文提示词，只输出提示词。",
                        "temperature": 0.4,
                        "max_tokens": 512,
                    },
                ),
                (IMAGE_TXT2IMG, {"width": 768, "height": 768, "steps": 30}),
                (IMAGE_SAVE, {"directory": str(default_output_dir())}),
            ],
            description=_DESCRIPTIONS["提示词润色 → 出图"],
        ),
        FlowGraph.from_steps(
            "LLM 问答 → 保存",
            [
                (TEXT_INPUT, {"text": "用简洁的中文解释什么是 KV cache。"}),
                (LLM_CHAT, {"temperature": 0.7, "max_tokens": 1024}),
                (TEXT_SAVE, {"path": str(default_output_dir() / "answer.md")}),
            ],
            description=_DESCRIPTIONS["LLM 问答 → 保存"],
        ),
    ]
    registry = default_registry()
    for flow in templates:
        flow.rechain(registry)
    return templates


def default_output_dir() -> Path:
    """默认输出目录：``${FCMD_HOME}/flows/out``。"""
    from fcmd.apps.llm._llmconfig import home_dir

    return home_dir() / "flows" / "out"
