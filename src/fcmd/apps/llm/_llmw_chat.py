"""llmw「对话」页的共享逻辑：服务可用性判定与会话 Markdown 导出。

Qt 对话页（``_llmw_qt_chat.py``）复用这里的口径；本模块不创建窗口，
CI 无显示环境也能直接单测。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from fcmd.apps.llm.llmbase import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    ServeState,
    http_base_url,
)
from fcmd.apps.llm.llmchat import ChatMessage
from fcmd.apps.llm.llmserve import process_alive

if TYPE_CHECKING:
    pass

__all__ = ["format_session_markdown"]

_DEFAULT_URL = http_base_url(DEFAULT_HOST, DEFAULT_PORT)
_CUSTOM = "自定义"


def _state_available(state: ServeState) -> bool:
    """服务状态对应的进程仍存活即视为可用。"""
    return process_alive(state.pid, state.create_time)


def _first_available(states: Sequence[ServeState]) -> ServeState | None:
    """按登记顺序返回第一个可用服务。"""
    return next((state for state in states if _state_available(state)), None)


def format_session_markdown(
    messages: Sequence[ChatMessage],
    *,
    model: str = "",
    base_url: str = "",
) -> str:
    """把对话历史导出为 Markdown 文本；系统提示与思考内容不写入。"""
    lines = ["# 对话记录", ""]
    if model:
        lines.extend([f"- 模型：`{model}`", ""])
    if base_url:
        lines.extend([f"- 服务：{base_url}", ""])
    for message in messages:
        if message.role == "system":
            continue
        lines.extend(["## 你" if message.role == "user" else "## 助手", ""])
        if message.content:
            lines.extend([message.content, ""])
        for attachment in message.attachments:
            name = attachment.path.name if attachment.path is not None else "image"
            target = attachment.path.as_posix() if attachment.path is not None else ""
            lines.extend([f"![{name}]({target})", ""])
    return "\n".join(lines).rstrip() + "\n"
