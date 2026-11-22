"""llmw 工作流：模型校验、执行器、缓存、持久化与模板。

执行器测试使用本地假节点，不依赖 llama.cpp / sd.cpp 服务。
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.llm._llmw_flow import (
    FILES,
    TEXT,
    FlowCancelled,
    FlowError,
    FlowGraph,
    FlowLink,
    FlowNode,
    NodeDefinition,
    NodeRegistry,
    ParamSpec,
    PortSpec,
    builtin_templates,
    default_registry,
    delete_flow,
    execute,
    list_flows,
    load_flow,
    save_flow,
)


def _registry(calls: list[str] | None = None, *, fail: bool = False) -> NodeRegistry:
    calls = calls if calls is not None else []
    registry = NodeRegistry()

    def source(_ctx: Any, _inputs: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
        calls.append("source")
        return {"text": str(params.get("text", ""))}

    def upper(_ctx: Any, inputs: Mapping[str, Any], _params: Mapping[str, Any]) -> dict[str, Any]:
        calls.append("upper")
        if fail:
            raise RuntimeError("boom")
        return {"text": str(inputs["text"]).upper()}

    def sink(_ctx: Any, inputs: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
        calls.append("sink")
        return {"files": [str(Path(str(params["path"])) / f"{inputs['text']}.txt")]}

    registry.register(
        NodeDefinition(
            "source",
            "输入",
            "输入",
            "",
            (),
            (PortSpec("text", TEXT),),
            (ParamSpec("text", "文本", default="hello"),),
            source,
        )
    )
    registry.register(
        NodeDefinition(
            "upper",
            "大写",
            "处理",
            "",
            (PortSpec("text", TEXT),),
            (PortSpec("text", TEXT),),
            (),
            upper,
        )
    )
    registry.register(
        NodeDefinition(
            "sink",
            "保存",
            "保存",
            "",
            (PortSpec("text", TEXT),),
            (PortSpec("files", FILES),),
            (ParamSpec("path", "路径", default="out"),),
            sink,
        )
    )
    return registry


def _chain() -> FlowGraph:
    flow = FlowGraph.from_steps("测试", [("source", {"text": "hi"}), ("upper", {}), ("sink", {"path": "out"})])
    flow.rechain(_registry())
    return flow


def test_chain_validates_and_orders() -> None:
    flow = _chain()
    flow.validate(_registry())
    assert flow.order() == ["n1", "n2", "n3"]
    assert flow.inputs_for("n2") == {"text": ("n1", "text")}


def test_missing_required_input_rejected() -> None:
    flow = FlowGraph.from_steps("坏链", [("source", {}), ("sink", {"path": "out"})])
    with pytest.raises(FlowError, match="缺少输入"):
        flow.validate(_registry())


def test_unknown_node_type_rejected() -> None:
    flow = FlowGraph.new("未知")
    flow.nodes = [FlowNode("n1", "missing")]
    with pytest.raises(FlowError, match="未知节点类型"):
        flow.validate(_registry())


def test_cycle_rejected() -> None:
    flow = FlowGraph.new("环")
    flow.nodes = [FlowNode("n1", "upper"), FlowNode("n2", "upper")]
    flow.links = [FlowLink("n1", "text", "n2", "text"), FlowLink("n2", "text", "n1", "text")]
    with pytest.raises(FlowError, match="存在环"):
        flow.validate(_registry())


def test_serialization_round_trip() -> None:
    flow = _chain()
    payload = flow.to_dict()
    restored = FlowGraph.from_dict(payload)
    assert restored.to_dict() == payload


def test_unsupported_schema_version_rejected() -> None:
    payload = _chain().to_dict()
    payload["schema_version"] = 99
    with pytest.raises(FlowError, match="版本不支持"):
        FlowGraph.from_dict(payload)


def test_execute_collects_outputs_and_files() -> None:
    calls: list[str] = []
    result = execute(_chain(), registry=_registry(calls), cache={})
    assert calls == ["source", "upper", "sink"]
    assert result.outputs["n2"]["text"] == "HI"
    assert result.files == [str(Path("out", "HI.txt"))]
    assert result.duration >= 0


def test_execute_cache_skips_second_run() -> None:
    calls: list[str] = []
    cache: dict[str, dict[str, Any]] = {}
    registry = _registry(calls)
    execute(_chain(), registry=registry, cache=cache)
    execute(_chain(), registry=registry, cache=cache)
    assert calls == ["source", "upper", "sink"]


def test_execute_wraps_node_failure() -> None:
    with pytest.raises(FlowError, match="执行失败: boom"):
        execute(_chain(), registry=_registry(fail=True))


def test_execute_cancel_before_node() -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(FlowCancelled):
        execute(_chain(), registry=_registry(), cancel=cancel)


def test_store_round_trip(tmp_path: Path) -> None:
    flow = _chain()
    path = save_flow(flow, home=tmp_path)
    assert path.is_file()
    assert [item.id for item in list_flows(tmp_path)] == [flow.id]
    loaded = load_flow(path)
    assert loaded.name == flow.name
    assert delete_flow(flow.id, home=tmp_path) is True
    assert list_flows(tmp_path) == []
    assert delete_flow(flow.id, home=tmp_path) is False


def test_builtin_templates_are_valid() -> None:
    registry = default_registry()
    for flow in builtin_templates():
        flow.validate(registry)


def test_port_type_mismatch_rejected() -> None:
    flow = FlowGraph.new("错类型")
    flow.nodes = [FlowNode("a", "source"), FlowNode("b", "upper")]
    flow.links = [FlowLink("a", "text", "b", "text")]
    # 合法连线后替换成不存在的源端口，校验应拦下
    flow.links = [FlowLink("a", "files", "b", "text")]
    with pytest.raises(FlowError, match="没有输出端口"):
        flow.validate(_registry())


# --------------------------------------------------------------------------- #
# 内置节点（不连真实服务：健康检查 / HTTP 客户端全部打桩）
# --------------------------------------------------------------------------- #
def _node_run(
    node_type: str, inputs: dict[str, Any] | None = None, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    from fcmd.apps.llm._llmw_flow import FlowContext

    definition = default_registry().require(node_type)
    return definition.run(FlowContext(), inputs or {}, params or {})


def test_text_input_node() -> None:
    from fcmd.apps.llm._llmw_flow_nodes import TEXT_INPUT

    assert _node_run(TEXT_INPUT, params={"text": "你好"}) == {"text": "你好"}


def test_llm_chat_node_success_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from fcmd.apps.llm import _llmw_flow_nodes as nodes

    with pytest.raises(FlowError, match="提示词为空"):
        _node_run(nodes.LLM_CHAT, {"prompt": "  "})
    monkeypatch.setattr(nodes.llmserve, "check_health", lambda *_args, **_kwargs: False)
    with pytest.raises(FlowError, match="未就绪"):
        _node_run(nodes.LLM_CHAT, {"prompt": "hi"})

    class _Session:
        def __init__(self, endpoint: Any, **kwargs: Any) -> None:
            self.endpoint = endpoint
            self.kwargs = kwargs

        def ask(self, prompt: str) -> str:
            return f"回答:{prompt}"

    monkeypatch.setattr(nodes.llmserve, "check_health", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(nodes.llmchat, "ChatSession", _Session)
    result = _node_run(
        nodes.LLM_CHAT,
        {"prompt": "hi"},
        {"host": "127.0.0.1", "port": 8080, "system": "sys", "max_tokens": 16, "temperature": 0.2},
    )
    assert result == {"text": "回答:hi"}
    with pytest.raises(FlowError, match="必须是整数"):
        _node_run(nodes.LLM_CHAT, {"prompt": "hi"}, {"port": "bad"})


def test_image_txt2img_node_success_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from fcmd.apps.llm import _llmw_flow_nodes as nodes

    with pytest.raises(FlowError, match="提示词为空"):
        _node_run(nodes.IMAGE_TXT2IMG, {"prompt": ""})
    monkeypatch.setattr(nodes._llmpaint_server, "check_paint_health", lambda *_args, **_kwargs: False)
    with pytest.raises(FlowError, match="未就绪"):
        _node_run(nodes.IMAGE_TXT2IMG, {"prompt": "cat"})

    captured: dict[str, Any] = {}

    def _txt2img(host: str, port: int, payload: dict[str, Any]) -> list[str]:
        captured.update(host=host, port=port, payload=payload)
        return ["base64-image"]

    monkeypatch.setattr(nodes._llmpaint_server, "check_paint_health", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(nodes._llmpaint_server, "txt2img", _txt2img)
    result = _node_run(
        nodes.IMAGE_TXT2IMG,
        {"prompt": "cat", "negative": "blur"},
        {"width": 768, "height": 512, "steps": 12, "cfg_scale": 5.5, "batch": 2},
    )
    assert result == {"images": ["base64-image"]}
    assert captured["payload"]["prompt"] == "cat"
    assert captured["payload"]["negative_prompt"] == "blur"
    with pytest.raises(FlowError, match="必须是数字"):
        _node_run(nodes.IMAGE_TXT2IMG, {"prompt": "cat"}, {"cfg_scale": "bad"})


def test_image_save_and_text_save_nodes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fcmd.apps.llm import _llmw_flow_nodes as nodes

    with pytest.raises(FlowError, match="没有可保存"):
        _node_run(nodes.IMAGE_SAVE, {"images": []})
    with pytest.raises(FlowError, match="输出目录"):
        _node_run(nodes.IMAGE_SAVE, {"images": ["x"]}, {"directory": ""})

    target = tmp_path / "out"
    monkeypatch.setattr(nodes._llmpaint, "save_images", lambda items, directory: [directory / "a.png"])
    assert _node_run(nodes.IMAGE_SAVE, {"images": ["x"]}, {"directory": str(target)}) == {
        "files": [str(target / "a.png")]
    }

    with pytest.raises(FlowError, match="保存路径"):
        _node_run(nodes.TEXT_SAVE, {"text": "x"}, {"path": ""})
    text_path = tmp_path / "nested" / "answer.md"
    assert _node_run(nodes.TEXT_SAVE, {"text": "hello"}, {"path": str(text_path)}) == {"files": [str(text_path)]}
    assert text_path.read_text(encoding="utf-8") == "hello"


def test_registry_and_model_errors() -> None:
    from fcmd.apps.llm._llmw_flow import FlowContext, FlowLink, FlowNode, NodeRegistry

    registry = NodeRegistry()
    definition = default_registry().require("text.input")
    registry.register(definition)
    with pytest.raises(FlowError, match="重复"):
        registry.register(definition)
    with pytest.raises(FlowError, match="未知节点"):
        registry.require("missing")
    assert registry.by_category()
    assert registry.get("missing") is None
    assert FlowContext().cancel is None

    with pytest.raises(FlowError, match="节点必须是对象"):
        FlowNode.from_dict([])
    with pytest.raises(FlowError, match="缺少 id"):
        FlowNode.from_dict({"type": "x"})
    with pytest.raises(FlowError, match="缺少 type"):
        FlowNode.from_dict({"id": "n1"})
    with pytest.raises(FlowError, match="连线必须是对象"):
        FlowLink.from_dict([])
    with pytest.raises(FlowError, match="连线字段不完整"):
        FlowLink.from_dict({"source": "a"})

    duplicate = FlowGraph("x", "重复")
    duplicate.nodes = [FlowNode("n1", "text.input"), FlowNode("n1", "text.input")]
    with pytest.raises(FlowError, match="节点 id 重复"):
        duplicate.validate_ids()
    empty = FlowGraph("x", "空")
    with pytest.raises(FlowError, match="工作流为空"):
        empty.validate(default_registry())
    bad_link = FlowGraph("x", "坏连线")
    bad_link.nodes = [FlowNode("n1", "text.input")]
    bad_link.links = [FlowLink("n1", "text", "missing", "prompt")]
    with pytest.raises(FlowError, match="不存在的节点"):
        bad_link.validate_ids()
    with pytest.raises(FlowError, match="不存在的节点"):
        bad_link.validate(default_registry())
    with pytest.raises(FlowError, match="JSON 对象"):
        FlowGraph.from_dict([])
    with pytest.raises(FlowError, match="缺少名称"):
        FlowGraph.from_dict({"schema_version": 1})
    unknown = FlowGraph.from_steps("未知", [("missing", {}), ("text.input", {})])
    with pytest.raises(FlowError, match="未注册"):
        unknown.rechain(default_registry())


def test_executor_events_text_outputs_and_store_errors(tmp_path: Path) -> None:
    from fcmd.apps.llm._llmw_flow import NodeEvent, load_flow, save_flow

    events: list[NodeEvent] = []
    result = execute(_chain(), registry=_registry(), on_event=events.append)
    assert [event.kind for event in events] == ["start", "done", "start", "done", "start", "done"]
    assert result.text_outputs() == ["hi", "HI"]

    with pytest.raises(FlowError, match="id 非法"):
        save_flow(FlowGraph("!!!", "坏 id"), home=tmp_path)
    with pytest.raises(FlowError, match="不存在"):
        load_flow(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(FlowError, match="读取工作流失败"):
        load_flow(bad)
    assert list_flows(tmp_path) == []
