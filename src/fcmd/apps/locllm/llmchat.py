"""llmchat - 与 OpenAI 兼容服务进行多轮流式对话。

只依赖 :class:`fcmd.apps.locllm.llmbase.Endpoint`，因此也可直连 vLLM、LM Studio
或远程推理服务；独立命令需要按服务名定位时，才在函数内部懒导入
:mod:`fcmd.apps.locllm.llmenv`（与 llmbench 同口径）。

请求走 ``/v1/chat/completions`` 的 SSE 流式响应，维护完整 ``messages`` 多轮
历史；兼容思考型模型的 ``reasoning_content`` 流，思考与最终回答分开处理。
``/clear`` 清空历史，``/exit`` 退出交互模式。

独立命令：``llmchat [--prompt 提示词] [--name 服务名 | --base-url 地址]``。
"""

from __future__ import annotations

import base64
import json
import sys
import threading
from codecs import IncrementalDecoder, getincrementaldecoder
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

import fcmd
from fcmd.console import get_console
from fcmd.apps.locllm.llmbase import DEFAULT_ALIAS, DEFAULT_API_KEY, Endpoint, Opener, ServeError

__all__ = [
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_EDGE",
    "Attachment",
    "ChatDelta",
    "ChatMessage",
    "ChatSession",
    "build_payload",
    "image_size",
    "resolve_endpoint",
    "stream_chat",
    "stream_chat_events",
]

ChatRole = Literal["system", "user", "assistant"]
DeltaKind = Literal["content", "reasoning"]

_CHUNK_SIZE = 4096
_EXIT_WORDS = frozenset({"/exit", "/quit", "exit", "quit"})

#: 单张图片的原始字节上限
MAX_IMAGE_BYTES = 8 * 1024 * 1024
#: 图片长边上限（超出后按需缩放，视觉模型常用上限）
MAX_IMAGE_EDGE = 1568


@dataclass(frozen=True, slots=True)
class ChatDelta:
    """一段流式增量：``content`` 为回答，``reasoning`` 为思考过程。

    思考型模型（如 Gemma 4、Qwen3 thinking）会先流式输出 ``reasoning_content``，
    只有思考结束后才输出 ``content``；必须区分两者，否则会误判为“无输出”。
    """

    kind: DeltaKind
    text: str


_IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)
_JPEG_SOF_MARKERS = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})


def _detect_image_mime(data: bytes) -> str | None:
    """按文件头识别图片格式（不信任扩展名）。"""
    for signature, mime in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mime
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_size(data: bytes) -> tuple[int, int] | None:
    """读取 PNG/JPEG 的像素尺寸；无法解析（如 WEBP）时返回 ``None``。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data.startswith(b"\xff\xd8\xff"):
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:  # pragma: no cover - 异常 JPEG 结构
                index += 1
                continue
            marker = data[index + 1]
            if marker in _JPEG_SOF_MARKERS:
                height = int.from_bytes(data[index + 5 : index + 7], "big")
                width = int.from_bytes(data[index + 7 : index + 9], "big")
                return width, height
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:  # pragma: no cover - 无尺寸段
                index += 2
                continue
            segment = int.from_bytes(data[index + 2 : index + 4], "big")
            if segment < 2:  # pragma: no cover - 防御异常段长
                return None
            index += 2 + segment
    return None


def _resize_with_pillow(data: bytes, max_edge: int) -> bytes:
    """用 Pillow 等比缩放到长边 ``max_edge``；未安装 Pillow 时给出明确提示。"""
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 依赖环境
        raise ServeError(f"图片长边超过 {max_edge}，且未安装 Pillow，无法自动缩放；请手动缩小后重试") from exc
    with Image.open(BytesIO(data)) as image:
        image.thumbnail((max_edge, max_edge))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def _encode_data_url(mime: str, data: bytes) -> str:
    """把图片编码为 OpenAI 兼容的 data URL。"""
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


@dataclass(frozen=True, slots=True)
class Attachment:
    """一条消息的附件；S1 只处理图片，``text`` 预留给文档解析。"""

    kind: Literal["image", "text"]
    mime: str
    size: int
    data: bytes = b""
    path: Path | None = None
    text: str = ""

    @classmethod
    def image(
        cls,
        path: Path | str,
        *,
        max_bytes: int = MAX_IMAGE_BYTES,
        max_edge: int = MAX_IMAGE_EDGE,
        resize: Callable[[bytes, int], bytes] | None = None,
    ) -> Attachment:
        """读取并校验一张图片；超限或格式不支持时抛 :class:`ServeError`。"""
        source = Path(path).expanduser()
        try:
            raw = source.read_bytes()
        except OSError as exc:
            raise ServeError(f"读取图片失败: {source}（{exc}）") from exc
        mime = _detect_image_mime(raw)
        if mime is None:
            raise ServeError(f"不支持的图片格式（仅 png/jpeg/webp）: {source.name}")
        if len(raw) > max_bytes:
            raise ServeError(f"图片过大: {source.name} {len(raw)} 字节（上限 {max_bytes}）")
        size = image_size(raw)
        if size is not None and max(size) > max_edge:
            resizer = resize or _resize_with_pillow
            raw = resizer(raw, max_edge)
            mime = _detect_image_mime(raw) or mime
            if len(raw) > max_bytes:
                raise ServeError(f"图片缩放后仍过大: {source.name} {len(raw)} 字节（上限 {max_bytes}）")
        return cls(kind="image", mime=mime, size=len(raw), data=raw, path=source)

    def to_content_part(self) -> dict[str, Any]:
        """转换为 OpenAI 多模态 content 数组中的一项。"""
        if self.kind == "image":
            return {"type": "image_url", "image_url": {"url": _encode_data_url(self.mime, self.data)}}
        return {"type": "text", "text": self.text}


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """一条对话消息；带图片附件时序列化为多模态 content 数组。"""

    role: ChatRole
    content: str
    attachments: tuple[Attachment, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """转换为 OpenAI 兼容的消息字典（无附件时保持字符串 content）。"""
        if not self.attachments:
            return {"role": self.role, "content": self.content}
        parts: list[dict[str, Any]] = []
        if self.content:
            parts.append({"type": "text", "text": self.content})
        parts.extend(attachment.to_content_part() for attachment in self.attachments)
        return {"role": self.role, "content": parts}


@dataclass
class ChatSession:
    """维护多轮历史的一次对话会话。

    ``ask`` 会把用户输入与助手回复追加到 :attr:`messages`，失败时回滚该轮输入，
    保证历史始终由完整的问答对组成。``cancel`` 可在流式生成过程中随时停止。
    """

    endpoint: Endpoint
    system: str | None = None
    max_tokens: int = 2048
    temperature: float = 0.7
    top_p: float = 0.9
    timeout: float = 180.0
    opener: Opener | None = None
    messages: list[ChatMessage] = field(default_factory=list)
    last_had_reasoning: bool = field(default=False, init=False)
    _cancel: threading.Event = field(default_factory=threading.Event, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.system:
            self.messages.append(ChatMessage("system", self.system))

    def cancel(self) -> None:
        """请求停止当前流式生成（下一次检查时生效）。"""
        self._cancel.set()

    def reset(self) -> None:
        """清空历史并恢复系统提示。"""
        self._cancel.clear()
        self.messages.clear()
        if self.system:
            self.messages.append(ChatMessage("system", self.system))

    def ask(
        self,
        prompt: str,
        on_delta: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        *,
        attachments: Sequence[Attachment] = (),
    ) -> str:
        """发送一轮提问，返回完整回复（思考与回答分别增量回调）。"""
        text = prompt.strip()
        if not text and not attachments:
            raise ServeError("提示词不能为空")
        self._cancel.clear()
        self.last_had_reasoning = False
        self.messages.append(ChatMessage("user", text, tuple(attachments)))
        parts: list[str] = []
        try:
            for event in stream_chat_events(
                self.endpoint,
                self.messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                top_p=self.top_p,
                timeout=self.timeout,
                opener=self.opener,
                is_cancelled=self._cancel.is_set,
            ):
                if event.kind == "reasoning":
                    self.last_had_reasoning = True
                    if on_reasoning is not None:
                        on_reasoning(event.text)
                    continue
                parts.append(event.text)
                if on_delta is not None:
                    on_delta(event.text)
        except BaseException:
            self.messages.pop()
            raise
        reply = "".join(parts)
        if reply:
            self.messages.append(ChatMessage("assistant", reply))
        return reply


def build_payload(
    endpoint: Endpoint,
    messages: Sequence[ChatMessage],
    *,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    top_p: float = 0.9,
) -> dict[str, Any]:
    """构造 OpenAI 兼容的流式请求体。"""
    return {
        "model": endpoint.model,
        "messages": [message.to_dict() for message in messages],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "stream": True,
    }


def stream_chat(  # noqa: PLR0913
    endpoint: Endpoint,
    messages: Sequence[ChatMessage],
    *,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    top_p: float = 0.9,
    timeout: float = 180.0,
    opener: Opener | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> Iterator[str]:
    """向 ``/v1/chat/completions`` 发起流式请求，只产出回答文本。"""
    for event in stream_chat_events(
        endpoint,
        messages,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        timeout=timeout,
        opener=opener,
        is_cancelled=is_cancelled,
    ):
        if event.kind == "content":
            yield event.text


def stream_chat_events(  # noqa: PLR0913
    endpoint: Endpoint,
    messages: Sequence[ChatMessage],
    *,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    top_p: float = 0.9,
    timeout: float = 180.0,
    opener: Opener | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> Iterator[ChatDelta]:
    """向 ``/v1/chat/completions`` 发起流式请求，区分思考与回答增量。"""
    payload = build_payload(
        endpoint,
        messages,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
    )
    url = endpoint.base_url + "/v1/chat/completions"
    for event in _stream_sse(url, payload, endpoint.api_key, timeout, opener):
        if is_cancelled is not None and is_cancelled():
            return
        yield event


def _stream_sse(
    url: str,
    payload: dict[str, Any],
    api_key: str,
    timeout: float,
    opener: Opener | None,
) -> Iterator[ChatDelta]:
    """POST 并解析 SSE 响应；网络 / HTTP 错误统一转 :class:`ServeError`。"""
    from urllib.request import Request, urlopen

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {api_key}",
    }
    open_fn = opener or (lambda request, seconds: urlopen(request, timeout=seconds))
    try:
        response = open_fn(Request(url, data=body, headers=headers, method="POST"), timeout)
    except OSError as exc:
        raise ServeError(f"请求失败: {url}（{exc}）") from exc

    with response:
        status = getattr(response, "status", 200)
        if status != 200:
            detail = _read_text(response).strip()
            raise ServeError(f"服务返回 {status}: {detail or url}")
        yield from _iter_events(_iter_bytes(response))


def _read_text(response: Any) -> str:
    """读取错误响应体（读取失败返回空串）。"""
    read = getattr(response, "read", None)
    if read is None:
        return ""
    data = read()
    if isinstance(data, bytes):
        return data.decode("utf-8", "replace")
    return str(data)


def _iter_bytes(response: Any) -> Iterator[bytes]:
    """按块读取响应体，优先使用 ``read1`` 以避免等待缓冲区填满。"""
    read = getattr(response, "read1", None) or getattr(response, "read", None)
    if read is None:
        raise ServeError("响应对象不支持读取")
    while True:
        chunk = read(_CHUNK_SIZE)
        if not chunk:
            return
        yield chunk.encode("utf-8") if isinstance(chunk, str) else chunk


def _iter_events(chunks: Iterable[bytes]) -> Iterator[ChatDelta]:
    """把 SSE 字节流解析为思考 / 回答增量（跨块多字节字符由增量解码器处理）。"""
    decoder: IncrementalDecoder = getincrementaldecoder("utf-8")(errors="replace")
    pending = ""
    for chunk in chunks:
        pending += decoder.decode(chunk)
        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            event = _decode_line(line)
            if event is None:
                return
            kind, text = event
            if text:
                yield ChatDelta("reasoning" if kind == "reasoning" else "content", text)
    pending += decoder.decode(b"", final=True)
    if pending.strip():
        event = _decode_line(pending)
        if event is not None and event[1]:
            kind, text = event
            yield ChatDelta("reasoning" if kind == "reasoning" else "content", text)


def _decode_line(line: str) -> tuple[str, str] | None:  # noqa: PLR0911
    """解析单行 SSE；返回 ``(kind, text)``，``None`` 表示 ``[DONE]``。

    ``kind`` 为 ``reasoning``（思考过程）或 ``content``（回答）；``text`` 为空表示
    该行无需输出（注释、心跳、空增量等）。
    """
    text = line.strip()
    if not text or text.startswith(":") or not text.startswith("data:"):
        return ("", "")
    data = text[len("data:") :].strip()
    if data == "[DONE]":
        return None
    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        return ("", "")
    if not isinstance(event, dict):
        return ("", "")
    error = event.get("error")
    if error is not None:
        raise ServeError(f"服务返回错误: {error}")
    choices = event.get("choices")
    if not isinstance(choices, list) or not choices:
        return ("", "")
    choice = choices[0]
    if not isinstance(choice, dict):
        return ("", "")
    delta = choice.get("delta")
    if not isinstance(delta, dict):
        return ("", "")
    reasoning = delta.get("reasoning_content")
    if not isinstance(reasoning, str):
        reasoning = delta.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        return ("reasoning", reasoning)
    content = delta.get("content")
    if isinstance(content, str) and content:
        return ("content", content)
    return ("", "")


def resolve_endpoint(
    name: str | None = None,
    base_url: str | None = None,
    api_key: str = DEFAULT_API_KEY,
    model: str = DEFAULT_ALIAS,
) -> Endpoint:
    """解析对话服务：显式 ``base_url`` → ``name`` → 唯一运行中的服务。"""
    if base_url:
        url = base_url.strip().rstrip("/")
        if url.endswith("/v1"):
            url = url[: -len("/v1")]
        return Endpoint(url, api_key, model)

    from fcmd.apps.locllm import llmenv

    if name:
        return llmenv.require_state(name).endpoint
    states = llmenv.list_states()
    if len(states) == 1:
        return states[0].endpoint
    if not states:
        raise ServeError("没有已登记的服务：用 --base-url 指定地址，或先 fcmd llmserve start")
    raise ServeError("多个服务在运行，用 --name 指定: " + ", ".join(state.name for state in states))


def _write(text: str) -> None:
    """直接写 stdout 并刷新，保证流式输出即时可见。"""
    sys.stdout.write(text)
    sys.stdout.flush()


def _ask_and_print(session: ChatSession, prompt: str) -> None:
    """发送一轮提问并把思考 / 回复流式打印到终端。"""
    state = {"thinking": False, "answered": False}

    def on_reasoning(_text: str) -> None:
        if not state["thinking"]:
            _write("思考中…")
            state["thinking"] = True

    def on_delta(text: str) -> None:
        if not state["answered"]:
            if state["thinking"]:
                _write("\n")
            _write("AI> ")
            state["answered"] = True
        _write(text)

    session.ask(prompt, on_delta=on_delta, on_reasoning=on_reasoning)
    if state["answered"]:
        _write("\n")
        return
    if state["thinking"]:
        _write("\n")
    if session.last_had_reasoning:
        _write("AI> （仅生成思考内容，未在 max_tokens 内产出回答，请调大 --max-tokens）\n")
    else:
        _write("AI> （无输出）\n")


@fcmd.tool("llmchat", help="与 OpenAI 兼容服务多轮流式对话（CLI）")
def chat_cmd(  # noqa: PLR0913, PLR0917
    prompt: str | None = None,
    name: str | None = None,
    base_url: str | None = None,
    api_key: str = DEFAULT_API_KEY,
    model: str = DEFAULT_ALIAS,
    system: str | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    top_p: float = 0.9,
    timeout: float = 180.0,
) -> None:
    """与模型对话；``--prompt`` 单次回答，省略时进入多轮交互模式。

    参数
    ----
    prompt:
        单次提问内容；省略时进入交互模式（``/clear`` 清空历史，``/exit`` 退出）。
    name:
        服务名（读 ``~/.fcmd/serve/<name>.json``）；省略且只有一个服务时自动选用。
    base_url:
        OpenAI 兼容地址（如 ``http://127.0.0.1:8080``），给出时忽略 ``name``。
    api_key:
        ``base_url`` 模式下的 API Key。
    model:
        ``base_url`` 模式下的模型名。
    system:
        系统提示词（可选），会作为首条消息。
    max_tokens:
        单轮生成 token 上限（默认 2048；思考型模型需留出思考预算）。
    temperature:
        采样温度（默认 0.7）。
    top_p:
        核采样阈值（默认 0.9）。
    timeout:
        请求超时秒数（默认 180）。
    """
    endpoint = resolve_endpoint(name, base_url, api_key, model)
    session = ChatSession(
        endpoint,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        timeout=timeout,
    )
    get_console().print(f"[cyan]服务[/cyan] {endpoint.base_url}  [cyan]模型[/cyan] {endpoint.model}")
    if prompt is not None:
        _ask_and_print(session, prompt)
        return

    get_console().print("输入内容开始对话；/clear 清空历史，/exit 退出")
    while True:
        try:
            line = input("你> ")
        except (EOFError, KeyboardInterrupt):
            _write("\n")
            break
        text = line.strip()
        if not text:
            continue
        if text.lower() in _EXIT_WORDS:
            break
        if text == "/clear":
            session.reset()
            get_console().print("历史已清空")
            continue
        try:
            _ask_and_print(session, text)
        except KeyboardInterrupt:
            session.cancel()
            _write("\n已中断本轮生成\n")


@fcmd.main("llmchat")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行
