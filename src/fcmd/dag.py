"""DAG 构建、校验、分层与可视化。

使用自实现的 Kahn 算法进行拓扑排序。图以增量方式构建并即时校验，
使配置错误在构建时（而非执行时）快速失败。

支持：
* 图级默认值 :class:`GraphDefaults`，TaskSpec 字段为 ``None`` 时回退。
* 自动依赖推断：``depends_on`` 为空的纯 fn 任务，从必需参数名匹配图中任务名。
* 软依赖：仅用于上下文注入，不参与拓扑分层。
* 字符串引用与 :func:`compose` 编程式组合（P1 阶段实现）。
"""

from __future__ import annotations

__all__ = [
    "Graph",
    "GraphDefaults",
]

import inspect
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

from .context import is_context_annotation
from .errors import CycleError, DuplicateTaskError, MissingDependencyError
from .task import RetryPolicy, TaskSpec


def _topological_layers(deps: Mapping[str, tuple[str, ...]]) -> tuple[list[list[str]], list[str] | None]:
    """Kahn 算法分层拓扑排序。

    返回 ``(layers, cycle_nodes)``：无环时 ``cycle_nodes`` 为 ``None``；
    有环时为参与环的未处理节点列表（非精确环路径，仅指示存在环）。
    """
    # 入度（依赖数）与反向邻接表
    in_degree: dict[str, int] = {}
    dependents: dict[str, list[str]] = {}
    for name, d in deps.items():
        in_degree[name] = len(d)
        dependents.setdefault(name, [])
    for name, d in deps.items():
        for dep in d:
            if dep in dependents:
                dependents[dep].append(name)

    # 初始层：入度为 0 的节点
    current = sorted(name for name, deg in in_degree.items() if deg == 0)
    layers: list[list[str]] = []
    processed = 0

    while current:
        layers.append(current)
        nxt: list[str] = []
        for node in current:
            for dependent in dependents[node]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    nxt.append(dependent)
            processed += 1
        nxt.sort()
        current = nxt

    if processed < len(deps):
        cycle_nodes = [name for name, deg in in_degree.items() if deg > 0]
        return layers, cycle_nodes

    return layers, None


@dataclass
class GraphDefaults:
    """图级默认值。TaskSpec 对应字段为 ``None`` 时回退到此处。

    仅对可空字段生效（retry/timeout/strategy/env/cwd/tags/
    continue_on_error/verbose）。非空字段（name/fn/cmd）不回退。
    """

    retry: RetryPolicy | None = None
    timeout: float | None = None
    strategy: str | None = None
    tags: tuple[str, ...] = ()
    env: Mapping[str, str] | None = None
    cwd: Path | None = None
    continue_on_error: bool = False
    verbose: bool = False


def _prune_deps(spec: TaskSpec[Any], keep: Callable[[str], bool]) -> TaskSpec[Any]:
    """返回新 spec，其 ``depends_on`` / ``soft_depends_on`` 仅保留 ``keep(dep)`` 为真的依赖。"""
    return replace(
        spec,
        depends_on=tuple(d for d in spec.depends_on if keep(d)),
        soft_depends_on=tuple(d for d in spec.soft_depends_on if keep(d)),
    )


@dataclass
class Graph:
    """校验后的有向无环任务图。

    通过添加 :class:`~fcmd.task.TaskSpec` 实例构建。每次 ``add`` 都
    执行即时校验（重名、缺失依赖），:meth:`validate` / :meth:`layers`
    执行完整 DAG 校验（环检测）与拓扑分层。

    图仅持有*配置*；运行时状态存于 :class:`~fcmd.report.RunReport`。
    这使图可安全重复运行并在线程间共享。
    """

    specs: dict[str, TaskSpec[Any]] = field(default_factory=dict)
    deps: dict[str, tuple[str, ...]] = field(default_factory=dict)
    defaults: GraphDefaults = field(default_factory=GraphDefaults)
    namespace: str | None = None

    # resolved_spec 缓存：避免执行期每个任务多次重复 dataclasses.replace 判断。
    # 在 specs / defaults 变更时失效。
    _resolved_cache: dict[str, TaskSpec[Any]] = field(default_factory=dict)

    # layers() 缓存：避免重复 run() 调用时重算拓扑排序。
    # 在 specs 变更时失效。
    _layers_cache: list[list[str]] | None = field(default=None)

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _auto_infer_deps_single(self, spec: TaskSpec[Any], all_names: set[str]) -> TaskSpec[Any]:
        """对 ``depends_on`` 为空的纯 fn 任务，从必需参数名自动推断依赖。

        仅匹配 ``all_names`` 中任务名的必需参数（无默认值、非 Context 标注、
        非 ``*args``/``**kwargs``）被加入 ``depends_on``。显式声明
        ``depends_on`` 的任务、cmd 任务、无 fn 的任务不受影响。
        已在 ``soft_depends_on`` 中声明的参数名不会被重复加入 ``depends_on``。

        参数
        ----
        spec:
            待推断的 TaskSpec。
        all_names:
            当前可见的任务名集合（``from_specs`` 时为全部任务名，
            ``add`` 时为图中已有任务名）。
        """
        if spec.depends_on or spec.cmd is not None or spec.fn is None:
            return spec
        try:
            sig = inspect.signature(spec.fn)
        except (TypeError, ValueError):
            return spec
        soft = set(spec.soft_depends_on)
        inferred: list[str] = []
        for pname, param in sig.parameters.items():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            if param.default is not inspect.Parameter.empty:
                continue
            if is_context_annotation(param.annotation):
                continue
            if pname in soft:
                continue
            if pname in all_names and pname != spec.name:
                inferred.append(pname)
        if inferred:
            return replace(spec, depends_on=tuple(inferred))
        return spec

    def add(self, spec: TaskSpec[Any]) -> Graph:
        """注册一个任务 spec，并即时校验。返回 ``self`` 支持链式调用。

        对 ``depends_on`` 为空的纯 fn 任务，自动从必需参数名推断依赖
        （匹配图中已有任务名的参数被加入 ``depends_on``）。
        """
        spec = self._auto_infer_deps_single(spec, set(self.specs) | {spec.name})
        if spec.name in self.specs:
            raise DuplicateTaskError(spec.name)
        self.specs[spec.name] = spec
        self.deps[spec.name] = spec.depends_on
        self._validate_references()
        return self

    @classmethod
    def from_specs(
        cls,
        specs: Iterable[TaskSpec[Any] | str],
        defaults: GraphDefaults | None = None,
        *,
        namespace: str | None = None,
    ) -> Graph:
        """从可迭代的 task spec 构建图。

        先收集所有 spec，再统一校验。允许前向引用。字符串引用由
        :func:`compose` 或 :class:`GraphComposer` 解析展开（P1 阶段实现）。

        参数
        ----
        specs:
            TaskSpec 对象或字符串引用的列表。
        defaults:
            图级默认值。``None`` 使用空 :class:`GraphDefaults`。
        namespace:
            可选命名空间，用于子图合并时加前缀（P1 阶段实现）。
        """
        graph = cls(defaults=defaults or GraphDefaults(), namespace=namespace)
        collected: list[TaskSpec[Any]] = []
        for spec in specs:
            if isinstance(spec, str):
                # P1 阶段由 GraphComposer 解析字符串引用
                raise ValueError(f"字符串引用 {spec!r} 暂未支持，请使用 TaskSpec 对象。")
            if isinstance(spec, TaskSpec):
                collected.append(spec)
            else:
                raise TypeError(f"from_specs 只接受 TaskSpec 或 str，收到: {type(spec)}")

        # 自动推断 depends_on：对 depends_on 为空的纯 fn 任务，
        # 从必需参数名匹配图中任务名，消除显式声明 depends_on 的样板代码。
        all_names = {spec.name for spec in collected}
        expanded = [graph._auto_infer_deps_single(spec, all_names) for spec in collected]

        for spec in expanded:
            if spec.name in graph.specs:
                raise DuplicateTaskError(spec.name)
            graph.specs[spec.name] = spec
            graph.deps[spec.name] = spec.depends_on

        graph._validate_references()
        graph.validate()
        return graph

    @classmethod
    def from_yaml(cls, path: str | Path) -> Graph:
        """从 YAML 文件加载任务图（GitHub Actions 风格，简化版）。

        Parameters
        ----------
        path:
            YAML 文件路径。

        Returns
        -------
        Graph
            解析后的任务图，支持 ``jobs``/``needs``/``cmd``/``run``/
            ``env``/``cwd``/``timeout``/``retry``/``strategy``/``defaults``
            等字段。不支持 ``strategy.matrix`` 矩阵扇出与 ``if`` 条件。

        Raises
        ------
        ValueError
            YAML 结构不符合 schema 时。
        """
        from fcmd.yaml_loader import load_yaml

        # pyrefly 在 src-layout 下将 fcmd.dag 与本模块识别为不同类型，cast 绕过。
        return cast("Graph", load_yaml(path))

    # ------------------------------------------------------------------ #
    # 校验
    # ------------------------------------------------------------------ #
    def _validate_references(self) -> None:
        """确保每个硬依赖名都存在于图中。软依赖允许缺失（用 defaults 回退）。"""
        for name, spec in self.specs.items():
            for dep in spec.depends_on:
                if dep not in self.specs:
                    raise MissingDependencyError(name, dep)
            # 软依赖是可选输入：缺失时由 defaults 提供默认值，不报错。

    def validate(self) -> None:
        """执行完整 DAG 校验。存在环时抛出 :class:`CycleError`。

        顺带填充 :attr:`_layers_cache`（无环时），使后续 :meth:`layers`
        直接命中缓存，避免 :func:`_topological_layers` 二次计算。
        """
        self._validate_references()
        layers, cycle_nodes = _topological_layers(self.deps)
        if cycle_nodes is not None:
            raise CycleError(cycle_nodes)
        self._layers_cache = layers

    # ------------------------------------------------------------------ #
    # 内省
    # ------------------------------------------------------------------ #
    @property
    def names(self) -> list[str]:
        """所有已注册任务名（按插入顺序）。"""
        return list(self.specs.keys())

    def spec(self, name: str) -> TaskSpec[Any]:
        """返回 ``name`` 的 spec；不存在则 ``KeyError``。"""
        return self.specs[name]

    def resolved_spec(self, name: str) -> TaskSpec[Any]:
        """返回应用图级默认值后的 spec（不修改原图）。

        对于 ``retry``/``timeout``/``strategy``/``env``/``cwd`` 等可空
        字段，若 spec 字段为默认空值且图级默认值非空，则用
        :func:`dataclasses.replace` 生成带默认值的副本。

        结果按 ``name`` 缓存；specs / defaults 变更时缓存失效。
        """
        cached = self._resolved_cache.get(name)
        if cached is not None:
            return cached
        spec = self.specs[name]
        d = self.defaults
        overrides: dict[str, Any] = {}
        if spec.retry == RetryPolicy() and d.retry is not None:
            overrides["retry"] = d.retry
        if spec.timeout is None and d.timeout is not None:
            overrides["timeout"] = d.timeout
        if spec.strategy is None and d.strategy is not None:
            overrides["strategy"] = d.strategy
        if spec.env is None and d.env is not None:
            overrides["env"] = d.env
        if spec.cwd is None and d.cwd is not None:
            overrides["cwd"] = d.cwd
        if not spec.continue_on_error and d.continue_on_error:
            overrides["continue_on_error"] = True
        if not spec.verbose and d.verbose:
            overrides["verbose"] = True
        if not spec.tags and d.tags:
            overrides["tags"] = d.tags
        resolved = spec if not overrides else replace(spec, **overrides)
        self._resolved_cache[name] = resolved
        return resolved

    def dependencies(self, name: str) -> tuple[str, ...]:
        """``name`` 的直接硬依赖前驱。"""
        return self.deps[name]

    def all_deps(self, name: str) -> tuple[str, ...]:
        """``name`` 的硬依赖 + 软依赖。"""
        spec = self.specs[name]
        return tuple(spec.depends_on) + tuple(spec.soft_depends_on)

    def all_specs(self) -> Mapping[str, TaskSpec[Any]]:
        """name -> spec 的只读视图。"""
        return self.specs

    def layers(self) -> list[list[str]]:
        """将任务分组为可并行执行的层（Kahn 算法）。

        同层任务无相互硬依赖，可并发执行。软依赖不参与分层。
        层按执行顺序返回。图有环时抛出 :class:`CycleError`。

        结果按实例缓存；specs 变更时失效（:meth:`add`）。

        .. note::
            本方法假定图已通过 :meth:`validate` 校验（由 :func:`fcmd.run`
            在入口统一执行一次）。若直接调用本方法，需自行先校验。
        """
        if self._layers_cache is not None:
            return self._layers_cache
        result, cycle_nodes = _topological_layers(self.deps)
        if cycle_nodes is not None:
            raise CycleError(cycle_nodes)
        self._layers_cache = result
        return result

    # ------------------------------------------------------------------ #
    # 子图
    # ------------------------------------------------------------------ #
    def subgraph_with_deps(self, names: Iterable[str]) -> Graph:
        """返回包含 ``names`` 及其所有传递依赖的新图。

        与 :meth:`subgraph_by_names` 不同，本方法会沿 ``depends_on`` 和
        ``soft_depends_on`` 向上遍历，确保被选中的任务所需的上游全部包含在内，
        使子图可独立执行。

        参数
        ----
        names:
            需要执行的任务名。每个名称必须存在于当前图中。
        """
        seeds: set[str] = set(names)
        for n in seeds:
            if n not in self.specs:
                raise KeyError(f"Unknown task name: {n!r}")
        # BFS 收集传递依赖（硬依赖 + 软依赖）
        closure: set[str] = set()
        queue: list[str] = list(seeds)
        while queue:
            name = queue.pop()
            if name in closure:
                continue
            closure.add(name)
            for dep in self.all_deps(name):
                if dep not in closure:
                    queue.append(dep)
        kept: list[TaskSpec[Any]] = [
            _prune_deps(spec, lambda d: d in closure) for spec in self.specs.values() if spec.name in closure
        ]
        return Graph.from_specs(kept, defaults=self.defaults)

    # ------------------------------------------------------------------ #
    # 可视化
    # ------------------------------------------------------------------ #
    def to_mermaid(self, orientation: str = "TD") -> str:
        """将 DAG 渲染为 Mermaid ``graph`` 定义字符串。"""
        valid = {"TD", "TB", "BT", "LR", "RL"}
        orientation = orientation.upper()
        if orientation not in valid:
            raise ValueError(f"Invalid orientation {orientation!r}; expected one of {sorted(valid)}.")
        lines: list[str] = [f"graph {orientation}"]
        for name in self.specs:
            lines.append(f'    {name}["{name}"]')
        for name, deps in self.deps.items():
            for dep in deps:
                lines.append(f"    {dep} --> {name}")
        # 软依赖用虚线
        for name, spec in self.specs.items():
            for dep in spec.soft_depends_on:
                lines.append(f"    {dep} -.-> {name}")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------ #
    # 调试
    # ------------------------------------------------------------------ #
    def describe(self) -> str:
        """用于调试的人类可读多行摘要。"""
        out: list[str] = [f"Graph(tasks={len(self.specs)})"]
        for layer_idx, layer in enumerate(self.layers(), 1):
            out.append(f"  Layer {layer_idx}: {layer}")
        return "\n".join(out)

    def __repr__(self) -> str:
        return f"Graph(tasks={len(self.specs)})"

    def __len__(self) -> int:
        return len(self.specs)

    def __contains__(self, name: object) -> bool:
        return name in self.specs
