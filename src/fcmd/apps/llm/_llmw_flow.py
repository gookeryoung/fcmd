"""llmw 工作流：类型化节点 + DAG 执行 + 持久化 + 内置模板。

- :mod:`fcmd.apps.llm._llmw_flow_model`：节点 / 端口 / 连线 / 校验（纯数据）；
- :mod:`fcmd.apps.llm._llmw_flow_registry`：节点注册表；
- :mod:`fcmd.apps.llm._llmw_flow_nodes`：内置节点（LLM / sd.cpp / 保存）；
- :mod:`fcmd.apps.llm._llmw_flow_executor`：拓扑执行、缓存、取消与事件；
- :mod:`fcmd.apps.llm._llmw_flow_store`：``${FCMD_HOME}/flows/*.json`` 读写；
- :mod:`fcmd.apps.llm._llmw_flow_templates`：内置模板。

本模块不导入 Qt，供 GUI 与后续 CLI 共用。
"""

from __future__ import annotations

from fcmd.apps.llm._llmw_flow_executor import ExecutionResult, NodeEvent, execute
from fcmd.apps.llm._llmw_flow_model import (
    FILES,
    FLOW_SCHEMA_VERSION,
    IMAGES,
    TEXT,
    FlowCancelled,
    FlowContext,
    FlowError,
    FlowGraph,
    FlowLink,
    FlowNode,
    NodeDefinition,
    ParamSpec,
    PortSpec,
)
from fcmd.apps.llm._llmw_flow_registry import NodeRegistry, default_registry
from fcmd.apps.llm._llmw_flow_store import delete_flow, flows_dir, list_flows, load_flow, save_flow
from fcmd.apps.llm._llmw_flow_templates import builtin_templates, default_output_dir

__all__ = [
    "FILES",
    "FLOW_SCHEMA_VERSION",
    "IMAGES",
    "TEXT",
    "ExecutionResult",
    "FlowCancelled",
    "FlowContext",
    "FlowError",
    "FlowGraph",
    "FlowLink",
    "FlowNode",
    "NodeDefinition",
    "NodeEvent",
    "NodeRegistry",
    "ParamSpec",
    "PortSpec",
    "builtin_templates",
    "default_output_dir",
    "default_registry",
    "delete_flow",
    "execute",
    "flows_dir",
    "list_flows",
    "load_flow",
    "save_flow",
]
