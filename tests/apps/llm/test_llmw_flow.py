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
