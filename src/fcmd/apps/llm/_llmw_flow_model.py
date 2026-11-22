"""llmw 工作流的数据模型：节点、端口、连线与 DAG 校验。

本模块不导入 Qt 与服务实现，只保存可序列化的配置，便于单测、CLI 与后续画布共用。
执行语义见 :mod:`fcmd.apps.llm._llmw_flow_executor`，节点实现见
:mod:`fcmd.apps.llm._llmw_flow_nodes`。
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from typing import Any

__all__ = [
    "FILES",
    "FLOW_SCHEMA_VERSION",
    "IMAGES",
    "TEXT",
    "FlowCancelled",
    "FlowContext",
    "FlowError",
    "FlowGraph",
    "FlowLink",
    "FlowNode",
    "NodeDefinition",
    "ParamSpec",
    "PortSpec",
]

FLOW_SCHEMA_VERSION = 1

TEXT = "TEXT"
IMAGES = "IMAGES"
FILES = "FILES"
"""端口类型：文本 / 待保存图片（base64 列表）/ 已落盘文件路径列表。"""


class FlowError(ValueError):
    """工作流配置或执行错误（给用户看的业务错误）。"""


class FlowCancelled(RuntimeError):
    """用户取消执行；执行器在节点边界检查。"""


@dataclass(slots=True)
class FlowContext:
    """执行上下文：取消标记与日志回调（由执行器注入，节点不直接碰 Qt）。"""

    cancel: Any = None
    """``threading.Event``；为 ``None`` 时不可取消。"""
    log: Callable[[str], None] = lambda _message: None

    def check_cancelled(self) -> None:
        """节点边界检查取消；已取消时抛 :class:`FlowCancelled`。"""
        if self.cancel is not None and self.cancel.is_set():
            raise FlowCancelled("工作流已取消")


@dataclass(frozen=True, slots=True)
class PortSpec:
    """节点输入 / 输出端口。"""

    name: str
    type: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """节点参数的表单元信息（画布 / 表单共用）。"""

    name: str
    label: str
    kind: str = "text"
    default: Any = ""
    choices: tuple[str, ...] = ()
    minimum: int = 0
    maximum: int = 2_000_000_000
    hint: str = ""
    directory: bool = False


@dataclass(frozen=True, slots=True)
class NodeDefinition:
    """节点类型定义；``run`` 的签名见 :mod:`fcmd.apps.llm._llmw_flow_nodes`。"""

    type: str
    label: str
    category: str
    description: str
    inputs: tuple[PortSpec, ...]
    outputs: tuple[PortSpec, ...]
    params: tuple[ParamSpec, ...]
    run: Callable[[FlowContext, Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class FlowNode:
    """工作流中的一个节点实例。"""

    id: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "params": dict(self.params)}

    @classmethod
    def from_dict(cls, raw: Any) -> FlowNode:
        if not isinstance(raw, dict):
            raise FlowError("节点必须是对象")
        node_id = raw.get("id")
        node_type = raw.get("type")
        if not isinstance(node_id, str) or not node_id:
            raise FlowError("节点缺少 id")
        if not isinstance(node_type, str) or not node_type:
            raise FlowError(f"节点 {node_id} 缺少 type")
        params = raw.get("params")
        return cls(node_id, node_type, dict(params) if isinstance(params, dict) else {})


@dataclass(frozen=True, slots=True)
class FlowLink:
    """一条端口连线：上游节点输出 → 下游节点输入。"""

    source: str
    source_port: str
    target: str
    target_port: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "source_port": self.source_port,
            "target": self.target,
            "target_port": self.target_port,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> FlowLink:
        if not isinstance(raw, dict):
            raise FlowError("连线必须是对象")
        values = [raw.get(key) for key in ("source", "source_port", "target", "target_port")]
        if not all(isinstance(value, str) and value for value in values):
            raise FlowError("连线字段不完整")
        source, source_port, target, target_port = (str(value) for value in values)
        return cls(source, source_port, target, target_port)


@dataclass(slots=True)
class FlowGraph:
    """一张可序列化的工作流；只持有配置，运行状态由执行器返回。"""

    id: str
    name: str
    description: str = ""
    nodes: list[FlowNode] = field(default_factory=list)
    links: list[FlowLink] = field(default_factory=list)
    schema_version: int = FLOW_SCHEMA_VERSION

    @classmethod
    def new(cls, name: str, description: str = "") -> FlowGraph:
        """创建空工作流（自动生成稳定 id）。"""
        return cls(id=uuid.uuid4().hex, name=name, description=description)

    @classmethod
    def from_steps(
        cls,
        name: str,
        steps: Sequence[tuple[str, Mapping[str, Any]]],
        *,
        description: str = "",
    ) -> FlowGraph:
        """按线性步骤创建并自动连线（模板 / 默认工作流用）。"""
        flow = cls.new(name, description)
        flow.nodes = [
            FlowNode(f"n{index + 1}", node_type, dict(params)) for index, (node_type, params) in enumerate(steps)
        ]
        return flow

    def node(self, node_id: str) -> FlowNode | None:
        return next((node for node in self.nodes if node.id == node_id), None)

    def node_ids(self) -> set[str]:
        return {node.id for node in self.nodes}

    def validate_ids(self) -> None:
        """保存前的最小结构校验：id 非空 / 节点 id 唯一 / 连线两端节点存在。"""
        if not self.id.strip():
            raise FlowError("工作流缺少 id")
        ids = self.node_ids()
        if len(ids) != len(self.nodes):
            raise FlowError("节点 id 重复")
        for link in self.links:
            if link.source not in ids or link.target not in ids:
                raise FlowError(f"连线引用了不存在的节点: {link.source} -> {link.target}")

    # ------------------------------------------------------------------ #
    # 校验与拓扑
    # ------------------------------------------------------------------ #
    def validate(self, registry: Mapping[str, NodeDefinition]) -> None:  # noqa: PLR0912 - 校验项逐条列出更易读
        """校验节点类型 / 端口 / 必填输入 / 重复输入 / 有环；不合法时抛 :class:`FlowError`。"""
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise FlowError("节点 id 重复")
        if not self.nodes:
            raise FlowError("工作流为空，请先添加节点")
        for node in self.nodes:
            definition = registry.get(node.type)
            if definition is None:
                raise FlowError(f"未知节点类型: {node.type}")
            for spec in definition.params:
                node.params.setdefault(spec.name, copy.deepcopy(spec.default))
        incoming: dict[str, str] = {}
        for link in self.links:
            source = self.node(link.source)
            target = self.node(link.target)
            if source is None or target is None:
                raise FlowError(f"连线引用了不存在的节点: {link.source} -> {link.target}")
            source_def = registry[source.type]
            target_def = registry[target.type]
            out_spec = next((port for port in source_def.outputs if port.name == link.source_port), None)
            in_spec = next((port for port in target_def.inputs if port.name == link.target_port), None)
            if out_spec is None:
                raise FlowError(f"节点 {source.type} 没有输出端口 {link.source_port}")
            if in_spec is None:
                raise FlowError(f"节点 {target.type} 没有输入端口 {link.target_port}")
            if out_spec.type != in_spec.type:
                raise FlowError(f"端口类型不匹配: {out_spec.type} -> {in_spec.type}")
            key = f"{link.target}:{link.target_port}"
            if key in incoming:
                raise FlowError(f"输入端口被重复连线: {link.target}.{link.target_port}")
            incoming[key] = link.source
        for node in self.nodes:
            definition = registry[node.type]
            for spec in definition.inputs:
                if spec.required and f"{node.id}:{spec.name}" not in incoming:
                    raise FlowError(f"节点 {node.id}（{definition.label}）缺少输入: {spec.name}")
        self.order()  # 环检测

    def order(self) -> list[str]:
        """拓扑顺序（节点 id）；有环时抛 :class:`FlowError`。"""
        deps: dict[str, set[str]] = {node.id: set() for node in self.nodes}
        for link in self.links:
            deps.setdefault(link.target, set()).add(link.source)
        sorter = TopologicalSorter({node: set(upstream) for node, upstream in deps.items()})
        try:
            sorter.prepare()
        except CycleError as exc:
            cycle = ", ".join(str(item) for item in exc.args[1]) if len(exc.args) > 1 else ""
            raise FlowError(f"工作流存在环: {cycle}") from exc
        return [node for layer in _layers(sorter) for node in layer]

    def inputs_for(self, node_id: str) -> dict[str, tuple[str, str]]:
        """目标输入端口 → ``(上游节点 id, 上游端口)``。"""
        return {link.target_port: (link.source, link.source_port) for link in self.links if link.target == node_id}

    def rechain(self, registry: Mapping[str, NodeDefinition]) -> None:
        """按当前节点顺序重建线性连线；相邻节点没有兼容端口时抛 :class:`FlowError`。"""
        links: list[FlowLink] = []
        for source_node, target_node in zip(self.nodes, self.nodes[1:], strict=False):
            source_def = registry.get(source_node.type)
            target_def = registry.get(target_node.type)
            if source_def is None or target_def is None:
                raise FlowError("节点类型未注册，无法连线")
            matched = next(
                (
                    (output, input_spec)
                    for output in source_def.outputs
                    for input_spec in target_def.inputs
                    if output.type == input_spec.type
                ),
                None,
            )
            if matched is None:
                raise FlowError(f"{source_def.label} 的输出无法接到 {target_def.label}")
            output, input_spec = matched
            links.append(FlowLink(source_node.id, output.name, target_node.id, input_spec.name))
        self.links = links

    # ------------------------------------------------------------------ #
    # 序列化
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "nodes": [node.to_dict() for node in self.nodes],
            "links": [link.to_dict() for link in self.links],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> FlowGraph:
        if not isinstance(raw, dict):
            raise FlowError("工作流文件必须是 JSON 对象")
        version = raw.get("schema_version", FLOW_SCHEMA_VERSION)
        if version != FLOW_SCHEMA_VERSION:
            raise FlowError(f"工作流版本不支持: {version}（当前 {FLOW_SCHEMA_VERSION}）")
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise FlowError("工作流缺少名称")
        flow_id = raw.get("id")
        if not isinstance(flow_id, str) or not flow_id:
            flow_id = uuid.uuid4().hex
        nodes_raw = raw.get("nodes")
        links_raw = raw.get("links")
        nodes = [FlowNode.from_dict(item) for item in nodes_raw] if isinstance(nodes_raw, list) else []
        links = [FlowLink.from_dict(item) for item in links_raw] if isinstance(links_raw, list) else []
        return cls(
            id=flow_id,
            name=name.strip(),
            description=str(raw.get("description") or ""),
            nodes=nodes,
            links=links,
            schema_version=FLOW_SCHEMA_VERSION,
        )


def _layers(sorter: TopologicalSorter[str]) -> list[list[str]]:
    layers: list[list[str]] = []
    while sorter.is_active():
        layer = sorted(sorter.get_ready())
        layers.append(layer)
        for node in layer:
            sorter.done(node)
    return layers
