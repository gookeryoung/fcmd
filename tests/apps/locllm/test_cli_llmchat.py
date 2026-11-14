"""llmchat 与 locllmw 内嵌对话页测试。

覆盖 SSE 解析（跨块多字节字符、注释行、非法 JSON、错误事件、[DONE]）、
HTTP / 网络错误映射、会话历史与回滚、取消生成、服务解析与 CLI 交互分支。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from urllib.error import URLError
from urllib.request import Request

import pytest

from fcmd.apps.locllm import _locllmw_chat, llmchat, llmenv
from fcmd.apps.locllm.llmbase import DEFAULT_API_KEY, Endpoint, ServeError, ServeState
from fcmd.apps.locllm.llmchat import Attachment, ChatDelta, ChatMessage, ChatSession
from fcmd.core.toolkit import _TOOL_REGISTRY


# ---------------------------------------------------------------------- #
# 测试替身
# ---------------------------------------------------------------------- #
class _StreamResponse:
    """最小流式响应替身：每次最多返回 ``chunk_size`` 字节。"""

    def __init__(self, payload: bytes, status: int = 200, chunk_size: int = 7) -> None:
        self._payload = payload
        self._offset = 0
        self._chunk_size = chunk_size
        self.status = status

    def read1(self, size: int) -> bytes:
        step = min(size, self._chunk_size)
        chunk = self._payload[self._offset : self._offset + step]
        self._offset += len(chunk)
        return chunk

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk = self._payload[self._offset :]
        else:
            chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def __enter__(self) -> _StreamResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class _StrResponse:
    """返回 str 而非 bytes 的响应，用于覆盖编码分支。"""

    def __init__(self, payload: bytes) -> None:
        self._inner = _StreamResponse(payload)
        self.status = self._inner.status

    def read1(self, size: int) -> Any:
        return self._inner.read1(size).decode("utf-8")

    def __enter__(self) -> _StrResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class _NoReadResponse:
    """没有 read / read1 的响应对象。"""

    status = 200

    def __enter__(self) -> _NoReadResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class _StrErrorResponse(_NoReadResponse):
    """错误响应体为 str。"""

    status = 500

    def read(self) -> Any:
        return "boom"


def _sse(*contents: str) -> bytes:
    """把若干增量文本编码为 OpenAI SSE 响应。"""
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False) + "\n"
        for text in contents
    ]
    return ("".join(lines) + "data: [DONE]\n").encode("utf-8")


def _opener(
    payload: bytes,
    status: int = 200,
    seen: list[Request] | None = None,
    chunk_size: int = 7,
) -> Any:
    """返回固定响应的 opener 替身。"""

    def _open(request: Request, timeout: float = 2.0) -> _StreamResponse:
        if seen is not None:
            seen.append(request)
        return _StreamResponse(payload, status, chunk_size)

    return _open


def _fail_opener(request: Request, timeout: float = 2.0) -> Any:
    """模拟网络不可用。"""
    raise URLError("offline")


def _endpoint(model: str = "m") -> Endpoint:
    return Endpoint("http://127.0.0.1:8080", "sk-test", model)


def _state(name: str = "qwen") -> ServeState:
    return ServeState(
        name=name,
        pid=4321,
        create_time=0.0,
        host="127.0.0.1",
        port=8080,
        model="D:/models/m.gguf",
        profile="balanced",
        context=65536,
        engine="llama-server.exe",
        alias="qwen3-coder-30b-a3b",
        api_key=DEFAULT_API_KEY,
        log="log.txt",
        started_at="2026-11-09T12:00:00+08:00",
    )


def _fake_events(endpoint: Endpoint, messages: Any, **kwargs: Any) -> Iterator[ChatDelta]:
    """CLI 测试用：固定返回两段回答增量。"""
    yield ChatDelta("content", "你")
    yield ChatDelta("content", "好")


def _reasoning_events(endpoint: Endpoint, messages: Any, **kwargs: Any) -> Iterator[ChatDelta]:
    """CLI 测试用：先思考后回答。"""
    yield ChatDelta("reasoning", "让我想想")
    yield ChatDelta("content", "好")


def _reasoning_only_events(endpoint: Endpoint, messages: Any, **kwargs: Any) -> Iterator[ChatDelta]:
    """CLI 测试用：只产出思考内容。"""
    yield ChatDelta("reasoning", "只有思考")


# ---------------------------------------------------------------------- #
# 注册
# ---------------------------------------------------------------------- #
def test_tools_registered() -> None:
    assert set(_TOOL_REGISTRY["llmchat"]) == {None}
    # 对话已整合进 locllmw，独立窗口工具不再注册。
    assert "llmchatw" not in _TOOL_REGISTRY


def test_help_describes_parameters() -> None:
    from fcmd.core.toolkit import get_tool
    from fcmd.core.toolkit.args import _build_parser_for_tool

    text = _build_parser_for_tool(get_tool("llmchat")).format_help()
    assert "单次提问内容" in text
    assert "系统提示词" in text


# ---------------------------------------------------------------------- #
# SSE 解析
# ---------------------------------------------------------------------- #
def test_stream_chat_parses_sse_and_builds_request() -> None:
    seen: list[Request] = []
    deltas = list(
        llmchat.stream_chat(
            _endpoint(),
            [ChatMessage("user", "hi")],
            max_tokens=64,
            temperature=0.2,
            top_p=0.5,
            opener=_opener(_sse("你", "好"), seen=seen),
        )
    )

    assert deltas == ["你", "好"]
    request = seen[0]
    assert request.full_url == "http://127.0.0.1:8080/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer sk-test"
    assert request.data is not None
    assert isinstance(request.data, bytes)
    payload = json.loads(request.data)
    assert payload == {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 64,
        "temperature": 0.2,
        "top_p": 0.5,
        "stream": True,
    }


def test_stream_chat_handles_split_multibyte_and_noise() -> None:
    payload = (
        b"data: not-json\n"
        b'data: {"choices":[]}\n'
        b"data: []\n"
        b'data: {"choices":"x"}\n'
        b'data: {"choices":["x"]}\n'
        b'data: {"choices":[{}]}\n'
        b'data: {"choices":[{"delta":{"content":1}}]}\n'
        b": keepalive\n"
        b"event: ping\n"
        b"\n" + _sse("中文")
    )
    assert list(llmchat.stream_chat(_endpoint(), [], opener=_opener(payload, chunk_size=3))) == ["中文"]


def test_stream_chat_tail_without_newline() -> None:
    payload = b'data: {"choices":[{"delta":{"content":"end"}}]}'
    assert list(llmchat.stream_chat(_endpoint(), [], opener=_opener(payload))) == ["end"]


def test_stream_chat_accepts_str_chunks() -> None:
    def opener(request: Request, timeout: float = 2.0) -> _StrResponse:
        return _StrResponse(_sse("ok"))

    assert list(llmchat.stream_chat(_endpoint(), [], opener=opener)) == ["ok"]


def test_stream_chat_events_separate_reasoning_and_content() -> None:
    payload = (
        b'data: {"choices":[{"delta":{"reasoning_content":"think-1"}}]}\n'
        b'data: {"choices":[{"delta":{"reasoning":"think-2"}}]}\n'
        b'data: {"choices":[{"delta":{"content":"answer"}}]}\n'
        b"data: [DONE]\n"
    )
    events = list(llmchat.stream_chat_events(_endpoint(), [], opener=_opener(payload)))
    assert events == [
        ChatDelta("reasoning", "think-1"),
        ChatDelta("reasoning", "think-2"),
        ChatDelta("content", "answer"),
    ]


def test_stream_chat_ignores_trailing_blank_line() -> None:
    payload = b'data: {"choices":[{"delta":{"content":"end"}}]}\n\n'
    assert list(llmchat.stream_chat(_endpoint(), [], opener=_opener(payload))) == ["end"]


def test_stream_chat_done_without_newline_exits_cleanly() -> None:
    payload = b'data: {"choices":[{"delta":{"content":"end"}}]}\ndata: [DONE]'
    assert list(llmchat.stream_chat(_endpoint(), [], opener=_opener(payload))) == ["end"]


def test_stream_chat_returns_content_only_when_reasoning_present() -> None:
    payload = (
        b'data: {"choices":[{"delta":{"reasoning_content":"think"}}]}\n'
        b'data: {"choices":[{"delta":{"content":"answer"}}]}\n'
        b"data: [DONE]\n"
    )
    assert list(llmchat.stream_chat(_endpoint(), [], opener=_opener(payload))) == ["answer"]


def test_stream_chat_surfaces_error_event() -> None:
    payload = b'data: {"error":{"message":"bad request"}}\n'
    with pytest.raises(ServeError, match="bad request"):
        list(llmchat.stream_chat(_endpoint(), [], opener=_opener(payload)))


def test_stream_chat_http_error_reads_body() -> None:
    with pytest.raises(ServeError, match="500") as info:
        list(llmchat.stream_chat(_endpoint(), [], opener=_opener(b"boom", status=500)))
    assert "boom" in str(info.value)


def test_stream_chat_http_error_with_str_body() -> None:
    def opener(request: Request, timeout: float = 2.0) -> _StrErrorResponse:
        return _StrErrorResponse()

    with pytest.raises(ServeError, match="boom"):
        list(llmchat.stream_chat(_endpoint(), [], opener=opener))


def test_stream_chat_http_error_without_body() -> None:
    class _Bare(_NoReadResponse):
        status = 503

    def opener(request: Request, timeout: float = 2.0) -> _Bare:
        return _Bare()

    with pytest.raises(ServeError, match=r"http://127\.0\.0\.1:8080/v1/chat/completions"):
        list(llmchat.stream_chat(_endpoint(), [], opener=opener))


def test_stream_chat_wraps_network_error() -> None:
    with pytest.raises(ServeError, match="请求失败"):
        list(llmchat.stream_chat(_endpoint(), [], opener=_fail_opener))


def test_stream_chat_rejects_unreadable_response() -> None:
    def opener(request: Request, timeout: float = 2.0) -> _NoReadResponse:
        return _NoReadResponse()

    with pytest.raises(ServeError, match="不支持读取"):
        list(llmchat.stream_chat(_endpoint(), [], opener=opener))


def test_stream_chat_default_opener(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Request, float]] = []

    def _urlopen(request: Request, timeout: float) -> _StreamResponse:
        calls.append((request, timeout))
        return _StreamResponse(_sse("ok"))

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    assert list(llmchat.stream_chat(_endpoint(), [], timeout=9.0)) == ["ok"]
    assert calls[0][1] == 9.0


# ---------------------------------------------------------------------- #
# 会话
# ---------------------------------------------------------------------- #
def test_session_keeps_multi_turn_history() -> None:
    seen: list[Request] = []
    session = ChatSession(_endpoint(), system="sys", opener=_opener(_sse("a"), seen=seen))

    assert session.ask("q1") == "a"
    assert session.ask("q2") == "a"

    assert session.messages == [
        ChatMessage("system", "sys"),
        ChatMessage("user", "q1"),
        ChatMessage("assistant", "a"),
        ChatMessage("user", "q2"),
        ChatMessage("assistant", "a"),
    ]
    assert seen[1].data is not None
    assert isinstance(seen[1].data, bytes)
    second = json.loads(seen[1].data)
    assert [item["role"] for item in second["messages"]] == ["system", "user", "assistant", "user"]


def test_session_rollback_on_failure() -> None:
    session = ChatSession(_endpoint(), system="sys", opener=_fail_opener)
    with pytest.raises(ServeError):
        session.ask("q")
    assert session.messages == [ChatMessage("system", "sys")]


def test_session_empty_prompt() -> None:
    session = ChatSession(_endpoint())
    with pytest.raises(ServeError, match="不能为空"):
        session.ask("   ")


def test_session_cancel_stops_stream() -> None:
    session = ChatSession(_endpoint(), opener=_opener(_sse("a", "b", "c")))

    def on_delta(_text: str) -> None:
        session.cancel()

    assert session.ask("q", on_delta=on_delta) == "a"
    assert session.messages[-1] == ChatMessage("assistant", "a")


def test_session_reset_restores_system_prompt() -> None:
    session = ChatSession(_endpoint(), system="sys", opener=_opener(_sse("a")))
    session.ask("q")
    session.reset()
    assert session.messages == [ChatMessage("system", "sys")]


def test_session_without_reply_keeps_no_assistant_message() -> None:
    session = ChatSession(_endpoint(), opener=_opener(b"data: [DONE]\n"))
    assert session.ask("q") == ""
    assert session.messages == [ChatMessage("user", "q")]


def test_session_tracks_reasoning_and_keeps_only_answer_in_history() -> None:
    payload = (
        b'data: {"choices":[{"delta":{"reasoning_content":"think-1"}}]}\n'
        b'data: {"choices":[{"delta":{"reasoning_content":"think-2"}}]}\n'
        b'data: {"choices":[{"delta":{"content":"answer"}}]}\n'
        b"data: [DONE]\n"
    )
    session = ChatSession(_endpoint(), opener=_opener(payload))
    reasoning: list[str] = []
    reply = session.ask("q", on_reasoning=reasoning.append)

    assert reasoning == ["think-1", "think-2"]
    assert session.last_had_reasoning is True
    assert reply == "answer"
    assert session.messages == [ChatMessage("user", "q"), ChatMessage("assistant", "answer")]


def test_session_reasoning_only_leaves_no_assistant_message() -> None:
    payload = b'data: {"choices":[{"delta":{"reasoning_content":"think"}}]}\ndata: [DONE]\n'
    session = ChatSession(_endpoint(), opener=_opener(payload))

    assert session.ask("q") == ""
    assert session.last_had_reasoning is True
    assert session.messages == [ChatMessage("user", "q")]


def test_session_ask_resets_reasoning_flag() -> None:
    reasoning_payload = b'data: {"choices":[{"delta":{"reasoning_content":"think"}}]}\ndata: [DONE]\n'
    session = ChatSession(_endpoint(), opener=_opener(reasoning_payload))
    session.ask("q")
    session.opener = _opener(_sse("answer"))
    session.ask("q2")
    assert session.last_had_reasoning is False


# ---------------------------------------------------------------------- #
# 服务解析
# ---------------------------------------------------------------------- #
def test_resolve_endpoint_base_url_strips_v1() -> None:
    assert llmchat.resolve_endpoint(None, "http://127.0.0.1:8000/v1", "k", "m") == Endpoint(
        "http://127.0.0.1:8000", "k", "m"
    )


def test_resolve_endpoint_base_url_trailing_slash() -> None:
    assert llmchat.resolve_endpoint(None, "http://127.0.0.1:8000/", "k", "m").base_url == "http://127.0.0.1:8000"


def test_resolve_endpoint_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fcmd.apps.locllm.llmenv.require_state", lambda name, home=None: _state(name))
    assert llmchat.resolve_endpoint("alpha", None, "k", "m").model == "qwen3-coder-30b-a3b"


def test_resolve_endpoint_single_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fcmd.apps.locllm.llmenv.list_states", lambda home=None: [_state("alpha")])
    assert llmchat.resolve_endpoint(None, None, "k", "m").base_url == "http://127.0.0.1:8080"


def test_resolve_endpoint_no_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fcmd.apps.locllm.llmenv.list_states", lambda home=None: [])
    with pytest.raises(ServeError, match="没有已登记的服务"):
        llmchat.resolve_endpoint(None, None, "k", "m")


def test_resolve_endpoint_multiple_states(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fcmd.apps.locllm.llmenv.list_states", lambda home=None: [_state("a"), _state("b")])
    with pytest.raises(ServeError, match="--name"):
        llmchat.resolve_endpoint(None, None, "k", "m")


# ---------------------------------------------------------------------- #
# CLI
# ---------------------------------------------------------------------- #
def test_chat_cmd_single_prompt(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmchat, "resolve_endpoint", lambda *args, **kwargs: _endpoint())
    monkeypatch.setattr(llmchat, "stream_chat_events", _fake_events)

    llmchat.chat_cmd(prompt="hi")

    out = capsys.readouterr().out
    assert "http://127.0.0.1:8080" in out
    assert "AI> 你好" in out


def test_chat_cmd_empty_reply(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmchat, "resolve_endpoint", lambda *args, **kwargs: _endpoint())
    monkeypatch.setattr(llmchat, "stream_chat_events", lambda endpoint, messages, **kwargs: iter(()))

    llmchat.chat_cmd(prompt="hi")

    assert "（无输出）" in capsys.readouterr().out


def test_chat_cmd_reasoning_then_answer(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmchat, "resolve_endpoint", lambda *args, **kwargs: _endpoint())
    monkeypatch.setattr(llmchat, "stream_chat_events", _reasoning_events)

    llmchat.chat_cmd(prompt="hi")

    out = capsys.readouterr().out
    assert "思考中…" in out
    assert "AI> 好" in out


def test_chat_cmd_reasoning_only_hint(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmchat, "resolve_endpoint", lambda *args, **kwargs: _endpoint())
    monkeypatch.setattr(llmchat, "stream_chat_events", _reasoning_only_events)

    llmchat.chat_cmd(prompt="hi")

    assert "仅生成思考内容" in capsys.readouterr().out


def test_chat_cmd_interactive(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    answers = iter(["第一问", "   ", "/clear", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(llmchat, "resolve_endpoint", lambda *args, **kwargs: _endpoint())
    monkeypatch.setattr(llmchat, "stream_chat_events", _fake_events)

    llmchat.chat_cmd()

    out = capsys.readouterr().out
    assert "AI> 你好" in out
    assert "历史已清空" in out


@pytest.mark.parametrize("word", ["/exit", "/quit", "exit", "quit"])
def test_chat_cmd_exit_words(word: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt="": word)
    monkeypatch.setattr(llmchat, "resolve_endpoint", lambda *args, **kwargs: _endpoint())
    llmchat.chat_cmd()
    assert "输入内容开始对话" in capsys.readouterr().out


def test_chat_cmd_interactive_eof(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def _raise(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", _raise)
    monkeypatch.setattr(llmchat, "resolve_endpoint", lambda *args, **kwargs: _endpoint())
    llmchat.chat_cmd()
    assert "输入内容开始对话" in capsys.readouterr().out


def test_chat_cmd_interactive_interrupt(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def _interrupted(endpoint: Endpoint, messages: Any, **kwargs: Any) -> Iterator[ChatDelta]:
        yield ChatDelta("content", "a")
        raise KeyboardInterrupt

    answers = iter(["问", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(llmchat, "resolve_endpoint", lambda *args, **kwargs: _endpoint())
    monkeypatch.setattr(llmchat, "stream_chat_events", _interrupted)

    llmchat.chat_cmd()

    out = capsys.readouterr().out
    assert "AI> a" in out
    assert "已中断本轮生成" in out


# ---------------------------------------------------------------------- #
# locllmw 内嵌对话页（纯逻辑）
# ---------------------------------------------------------------------- #
def test_chat_page_default_url() -> None:
    assert _locllmw_chat._DEFAULT_URL == "http://127.0.0.1:8080"


def test_format_session_markdown_skips_system_and_keeps_turns() -> None:
    messages = [
        ChatMessage("system", "你是助手"),
        ChatMessage("user", "你好"),
        ChatMessage("assistant", "你好！"),
    ]
    text = _locllmw_chat.format_session_markdown(messages, model="qwen", base_url="http://127.0.0.1:8080")

    assert text.startswith("# 对话记录")
    assert "- 模型：`qwen`" in text
    assert "- 服务：http://127.0.0.1:8080" in text
    assert "## 你\n\n你好" in text
    assert "## 助手\n\n你好！" in text
    assert "你是助手" not in text


def test_format_session_markdown_empty_history() -> None:
    assert _locllmw_chat.format_session_markdown([ChatMessage("system", "sys")]) == "# 对话记录\n"


def test_format_session_markdown_renders_image_attachments(tmp_path: Path) -> None:
    image = tmp_path / "shot.png"
    image.write_bytes(b"x")
    messages = [
        ChatMessage(
            "user",
            "",
            (
                Attachment(kind="image", mime="image/png", size=1, path=image),
                Attachment(kind="image", mime="image/png", size=1),
            ),
        ),
        ChatMessage("assistant", "ok"),
    ]
    text = _locllmw_chat.format_session_markdown(messages)

    assert f"![shot.png]({image.as_posix()})" in text
    assert "![image]()" in text
    assert "## 助手\n\nok" in text


class _FakeVar:
    """模拟 tk.StringVar 的读写。"""

    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class _FakeBox:
    """模拟 ttk.Combobox 的 values 配置。"""

    def __init__(self) -> None:
        self.values: list[str] = []

    def configure(self, **kwargs: Any) -> None:
        self.values = list(kwargs["values"])


def _chat_page(states: list[ServeState], service: str = _locllmw_chat._CUSTOM) -> Any:
    """构造只含服务刷新所需属性的对话页替身。"""
    page: Any = object.__new__(_locllmw_chat._LocllmWindowChat)
    page._chat_states = states
    page._chat_manual_choice = False
    page._chat_service_var = _FakeVar(service)
    page._chat_url_var = _FakeVar()
    page._chat_model_var = _FakeVar()
    page._chat_api_key_var = _FakeVar()
    page._chat_status_var = _FakeVar("就绪")
    page._chat_service_box = _FakeBox()
    page._chat_service_labels = dict[str, str]()
    appended: list[tuple[str, str]] = []
    page._appended = appended
    page._chat_append = lambda tag, text: appended.append((tag, text))
    return page


def test_chat_page_refresh_selects_first_available(monkeypatch: pytest.MonkeyPatch) -> None:
    dead = _state("dead")
    alive = replace(_state("alive"), pid=1234)
    monkeypatch.setattr(llmenv, "list_states", lambda: [dead, alive])
    monkeypatch.setattr(_locllmw_chat, "process_alive", lambda pid, created: pid == alive.pid)

    page = _chat_page([])
    _locllmw_chat._LocllmWindowChat._chat_refresh_services(page)

    assert page._chat_service_var.get() == "alive（运行中）"
    assert page._chat_url_var.get() == alive.base_url
    assert page._chat_model_var.get() == alive.alias
    assert page._chat_status_var.get() == "已选择服务 alive（运行中）"


def test_chat_page_refresh_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state("dead")
    monkeypatch.setattr(llmenv, "list_states", lambda: [state])
    monkeypatch.setattr(_locllmw_chat, "process_alive", lambda pid, created: False)

    page = _chat_page([])
    _locllmw_chat._LocllmWindowChat._chat_refresh_services(page)

    assert page._chat_service_var.get() == "dead（已退出）"
    assert page._chat_service_box.values == [_locllmw_chat._CUSTOM, "dead（已退出）"]
    assert page._chat_status_var.get() == "服务 dead 的进程已退出：去「运行」页重新启动，或点「刷新服务列表」"


def test_chat_page_refresh_without_services(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llmenv, "list_states", lambda: [])

    page = _chat_page([])
    _locllmw_chat._LocllmWindowChat._chat_refresh_services(page)

    assert page._chat_service_box.values == [_locllmw_chat._CUSTOM]
    assert page._chat_status_var.get() == "没有已登记的服务：去「运行」页启动，或点「刷新服务列表」"


def test_chat_page_refresh_keeps_manual_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    alive = _state("alive")
    monkeypatch.setattr(llmenv, "list_states", lambda: [alive])
    monkeypatch.setattr(_locllmw_chat, "process_alive", lambda pid, created: True)

    page = _chat_page([])
    page._chat_manual_choice = True
    _locllmw_chat._LocllmWindowChat._chat_refresh_services(page)

    assert page._chat_service_var.get() == _locllmw_chat._CUSTOM
    assert page._chat_status_var.get() == "未选择已登记服务：可直接填写下方地址接入其它 OpenAI 兼容服务"


def test_chat_page_select_unavailable_service(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state("dead")
    monkeypatch.setattr(_locllmw_chat, "process_alive", lambda pid, created: False)

    page = _chat_page([state], service="dead（已退出）")
    page._chat_service_labels = {"dead（已退出）": "dead"}
    _locllmw_chat._LocllmWindowChat._chat_on_service_selected(page)

    assert page._chat_url_var.get() == state.base_url
    assert page._chat_status_var.get() == "服务 dead 的进程已退出：去「运行」页重新启动，或点「刷新服务列表」"
    assert page._appended[0][0] == "error"


class _ReasoningTwiceSession:
    def ask(self, prompt: str, *, on_delta: Any, on_reasoning: Any) -> None:
        assert prompt == "hi"
        on_reasoning("t1")
        on_reasoning("t2")
        on_delta("a")
        on_delta("b")


def test_ask_and_print_writes_thinking_marker_once(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[str] = []
    monkeypatch.setattr(llmchat, "_write", written.append)

    llmchat._ask_and_print(cast(Any, _ReasoningTwiceSession()), "hi")

    assert written == ["思考中…", "\n", "AI> ", "a", "b", "\n"]
