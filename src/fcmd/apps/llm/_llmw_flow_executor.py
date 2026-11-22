"""工作流执行器：拓扑执行、节点缓存、取消、事件回调。

不依赖 Qt；GUI 通过 :func:`execute` 的 ``on_event`` 接收进度，CLI 可直接复用。
节点边界检查取消，节点内部的长请求按自身超时结束。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from fcmd.apps.llm._llmw_flow_model import (
    FILES,
    FlowCancelled,
    FlowContext,
    FlowError,
    FlowGraph,
    NodeDefinition,
)
from fcmd.apps.llm._llmw_flow_registry import NodeRegistry, default_registry

__all__ = ["ExecutionResult", "NodeEvent", "execute"]

Cache = dict[str, dict[str, Any]]


@dataclass(frozen=True, slots=True)
class NodeEvent:
    """执行进度事件：``start`` / ``done`` / ``cached`` / ``log``。"""

    kind: str
    node_id: str
    node_type: str
    label: str
    message: str = ""


@dataclass(slots=True)
class ExecutionResult:
    """一次执行的输出与日志。"""

    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    duration: float = 0.0

    def text_outputs(self) -> list[str]:
        """所有节点产生的文本输出（调试 / 结果展示用）。"""
        texts: list[str] = []
        for values in self.outputs.values():
            texts.extend(value for value in values.values() if isinstance(value, str))
        return texts


def _cache_key(node_id: str, definition: NodeDefinition, params: Mapping[str, Any], inputs: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {"node": node_id, "type": definition.type, "params": dict(params), "inputs": inputs},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def execute(  # noqa: PLR0913 - 执行器入口参数成组出现
    flow: FlowGraph,
    *,
    registry: NodeRegistry | None = None,
    cancel: Any = None,
    cache: Cache | None = None,
    on_event: Callable[[NodeEvent], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> ExecutionResult:
    """按拓扑顺序执行工作流；节点失败时抛 :class:`FlowError`，取消时抛 :class:`FlowCancelled`。"""
    registry = registry or default_registry()
    flow.validate(registry)
    events = on_event or (lambda _event: None)
    context = FlowContext(cancel=cancel, log=log or (lambda _message: None))
    result = ExecutionResult()
    started = time.monotonic()
    for node_id in flow.order():
        context.check_cancelled()
        node = flow.node(node_id)
        assert node is not None  # validate 已保证存在
        definition = registry.require(node.type)
        inputs = _collect_inputs(flow, result, node_id)
        key = _cache_key(node_id, definition, node.params, inputs)
        if cache is not None and key in cache:
            result.outputs[node_id] = dict(cache[key])
            events(NodeEvent("cached", node_id, node.type, definition.label, "命中缓存"))
            _collect_files(result, definition, result.outputs[node_id])
            continue
        events(NodeEvent("start", node_id, node.type, definition.label))
        try:
            outputs = definition.run(context, inputs, node.params)
        except FlowCancelled:
            raise
        except FlowError as exc:
            raise FlowError(f"{definition.label}（{node.id}）执行失败: {exc}") from exc
        except Exception as exc:  # 节点内部异常统一转成用户可读错误
            raise FlowError(f"{definition.label}（{node.id}）执行失败: {exc}") from exc
        result.outputs[node_id] = dict(outputs)
        if cache is not None:
            cache[key] = dict(outputs)
        _collect_files(result, definition, outputs)
        message = _describe_outputs(definition, outputs)
        result.log.append(f"{definition.label}: {message}")
        events(NodeEvent("done", node_id, node.type, definition.label, message))
    result.duration = time.monotonic() - started
    return result


def _collect_inputs(flow: FlowGraph, result: ExecutionResult, node_id: str) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for target_port, (source, source_port) in flow.inputs_for(node_id).items():
        upstream = result.outputs.get(source)
        if upstream is None:
            raise FlowError(f"节点 {node_id} 的上游 {source} 尚未执行")
        inputs[target_port] = upstream[source_port]
    return inputs


def _collect_files(result: ExecutionResult, definition: NodeDefinition, outputs: Mapping[str, Any]) -> None:
    file_ports = {port.name for port in definition.outputs if port.type == FILES}
    for port in file_ports:
        value = outputs.get(port)
        if isinstance(value, list):
            result.files.extend(str(item) for item in value)


def _describe_outputs(definition: NodeDefinition, outputs: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for port in definition.outputs:
        value = outputs.get(port.name)
        if value is None:
            continue
        if isinstance(value, list):
            parts.append(f"{len(value)} 项")
        elif isinstance(value, str):
            preview = value.strip().replace("\n", " ")
            parts.append(preview[:60] + ("…" if len(preview) > 60 else ""))
        else:
            parts.append(str(value))
    return " / ".join(parts) or "完成"
