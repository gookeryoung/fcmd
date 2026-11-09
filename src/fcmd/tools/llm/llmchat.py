"""llmchat - 与 OpenAI 兼容服务进行多轮流式对话。

只依赖 :class:`fcmd.tools.llm.llmbase.Endpoint`，因此也可直连 vLLM、LM Studio
或远程推理服务；独立命令需要按服务名定位时，才在函数内部懒导入
:mod:`fcmd.tools.llm.llmenv`（与 llmbench 同口径）。

请求走 ``/v1/chat/completions`` 的 SSE 流式响应，维护完整 ``messages`` 多轮
历史；兼容思考型模型的 ``reasoning_content`` 流，思考与最终回答分开处理。
``/clear`` 清空历史，``/exit`` 退出交互模式。

独立命令：``llmchat [--prompt 提示词] [--name 服务名 | --base-url 地址]``。
"""

from __future__ import annotations

import json
import sys
import threading
from codecs import IncrementalDecoder, getincrementaldecoder
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import fcmd
from fcmd.console import get_console
from fcmd.tools.llm.llmbase import DEFAULT_ALIAS, DEFAULT_API_KEY, Endpoint, Opener, ServeError

__all__ = [
    "ChatDelta",
    "ChatMessage",
    "ChatSession",
    "build_payload",
    "resolve_endpoint",
    "stream_chat",
    "stream_chat_events",
]

ChatRole = Literal["system", "user", "assistant"]
DeltaKind = Literal["content", "reasoning"]

_CHUNK_SIZE = 4096
_EXIT_WORDS = frozenset({"/exit", "/quit", "exit", "quit"})


@dataclass(frozen=True, slots=True)
class ChatDelta:
    """一段流式增量：``content`` 为回答，``reasoning`` 为思考过程。

    思考型模型（如 Gemma 4、Qwen3 thinking）会先流式输出 ``reasoning_content``，
    只有思考结束后才输出 ``content``；必须区分两者，否则会误判为“无输出”。
    """

    kind: DeltaKind
    text: str


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """一条对话消息。"""

    role: ChatRole
    content: str

    def to_dict(self) -> dict[str, str]:
        """转换为 OpenAI 兼容的消息字典。"""
        return {"role": self.role, "content": self.content}


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
    ) -> str:
        """发送一轮提问，返回完整回复（思考与回答分别增量回调）。"""
        text = prompt.strip()
        if not text:
            raise ServeError("提示词不能为空")
        self._cancel.clear()
        self.last_had_reasoning = False
        self.messages.append(ChatMessage("user", text))
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

    from fcmd.tools.llm import llmenv

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
