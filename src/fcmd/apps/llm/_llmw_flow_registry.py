"""工作流节点注册表：按 ``type`` 查找节点定义，供模型校验与执行器调度。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from fcmd.apps.llm._llmw_flow_model import FlowError, NodeDefinition

__all__ = ["NodeRegistry", "default_registry"]


class NodeRegistry(Mapping[str, NodeDefinition]):
    """节点定义注册表；内置节点由 :func:`default_registry` 惰性装配。"""

    def __init__(self) -> None:
        self._nodes: dict[str, NodeDefinition] = {}

    def register(self, definition: NodeDefinition) -> NodeDefinition:
        """登记一个节点类型；重复类型直接报错，避免模板/执行口径分叉。"""
        if definition.type in self._nodes:
            raise FlowError(f"节点类型重复: {definition.type}")
        self._nodes[definition.type] = definition
        return definition

    def __getitem__(self, node_type: str) -> NodeDefinition:
        return self._nodes[node_type]

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._nodes))

    def __len__(self) -> int:
        return len(self._nodes)

    def require(self, node_type: str) -> NodeDefinition:
        definition = self.get(node_type)
        if definition is None:
            raise FlowError(f"未知节点类型: {node_type}")
        return definition

    def all(self) -> tuple[NodeDefinition, ...]:
        return tuple(self._nodes[key] for key in sorted(self._nodes))

    def by_category(self) -> dict[str, tuple[NodeDefinition, ...]]:
        grouped: dict[str, list[NodeDefinition]] = {}
        for definition in self.all():
            grouped.setdefault(definition.category, []).append(definition)
        return {category: tuple(items) for category, items in grouped.items()}


_BUILTIN: NodeRegistry | None = None


def default_registry() -> NodeRegistry:
    """返回内置节点注册表（首次调用时装配，模块级缓存）。"""
    global _BUILTIN  # noqa: PLW0603 - 注册表只装配一次
    if _BUILTIN is None:
        registry = NodeRegistry()
        from fcmd.apps.llm._llmw_flow_nodes import register_builtin_nodes

        register_builtin_nodes(registry)
        _BUILTIN = registry
    return _BUILTIN
