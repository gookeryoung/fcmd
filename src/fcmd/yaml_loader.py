"""YAML 任务编排（GitHub Actions 风格，简化版）。

从 YAML 文件加载为 :class:`~fcmd.dag.Graph`，支持 ``jobs``/``needs``/
``cmd``/``run``/``env``/``cwd``/``timeout``/``retry``/``strategy`` 等核心字段。

不支持 pyflowx 的 ``strategy.matrix`` 矩阵扇出与 ``if`` 条件（需额外
``conditions`` 模块）；如需复杂编排，请直接用 :func:`fcmd.graph` 编程式构建。

Schema
------
.. code-block:: yaml

    strategy: thread              # 图级默认策略
    defaults:                     # 图级默认值
      retry: {max_attempts: 3}
      timeout: 300
      env: {CI: "true"}
      cwd: /tmp

    jobs:
      setup:
        cmd: ["git", "clone"]

      build:
        needs: [setup]
        cmd: ["uv", "build"]
        timeout: 120

      deploy:
        needs: [build]
        run: "twine upload dist/*"
        env: {TWINE_TOKEN: "..."}
        continue-on-error: true

字段映射
--------
| YAML 字段 | TaskSpec 字段 | 说明 |
|-----------|---------------|------|
| cmd | cmd | 命令列表 |
| run | cmd | shell 字符串（合并到 cmd 字段） |
| needs | depends_on | 任务依赖列表 |
| env / cwd / timeout / retry | 同名 | 透传 |
| continue-on-error / allow-upstream-skip | 同名（hyphen 兼容 underscore） |
| tags | tags | 自由标签 |
| verbose | verbose | 详细输出 |
| strategy | strategy | 单任务策略覆盖 |
"""

from __future__ import annotations

__all__ = [
    "load_yaml",
    "parse_yaml_string",
]

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-not-found]

from fcmd.dag import Graph, GraphDefaults
from fcmd.task import RetryPolicy, TaskSpec


def _safe_load(text: str) -> Any:
    """调用 ``yaml.safe_load`` 并将 YAMLError 包装为 ValueError。

    便于调用方统一捕获 ``ValueError`` 处理所有 YAML 解析失败场景。
    """
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败: {exc}") from exc


def parse_yaml_string(text: str) -> Graph:
    """从 YAML 字符串解析任务图。

    Parameters
    ----------
    text:
        YAML 格式的字符串。

    Returns
    -------
    Graph
        解析后的任务图。

    Raises
    ------
    ValueError
        YAML 结构不符合 schema 时。
    """
    data = _safe_load(text)
    return _build_graph(data)


def load_yaml(path: str | Path) -> Graph:
    """从 YAML 文件加载任务图。

    Parameters
    ----------
    path:
        YAML 文件路径。

    Returns
    -------
    Graph
        解析后的任务图。
    """
    data = _safe_load(Path(path).read_text(encoding="utf-8"))
    return _build_graph(data)


def _build_graph(data: Any) -> Graph:
    """从解析后的 YAML 字典构建 Graph。"""
    if not isinstance(data, Mapping):
        raise ValueError(f"YAML 根节点必须是映射，收到: {type(data).__name__}")

    jobs = data.get("jobs")
    if not jobs:
        raise ValueError("YAML 缺少 'jobs' 或 jobs 为空")
    if not isinstance(jobs, Mapping):
        raise ValueError(f"jobs 必须是映射，收到: {type(jobs).__name__}")

    defaults = _parse_defaults(data.get("defaults"))
    strategy = data.get("strategy")
    if strategy:
        from dataclasses import replace

        defaults = replace(defaults, strategy=str(strategy))

    specs = _build_specs(jobs)
    return Graph.from_specs(specs, defaults=defaults)


def _parse_defaults(data: Any) -> GraphDefaults:
    """解析 defaults 字段为 GraphDefaults。"""
    if not data:
        return GraphDefaults()
    if not isinstance(data, Mapping):
        raise ValueError(f"defaults 必须是映射，收到: {type(data).__name__}")

    kwargs: dict[str, Any] = {}
    if "retry" in data:
        kwargs["retry"] = _parse_retry(data["retry"])
    if "timeout" in data:
        kwargs["timeout"] = float(data["timeout"])
    if "verbose" in data:
        kwargs["verbose"] = bool(data["verbose"])
    if "env" in data:
        kwargs["env"] = dict(data["env"])
    if "cwd" in data:
        kwargs["cwd"] = Path(data["cwd"])
    if "tags" in data:
        kwargs["tags"] = tuple(data["tags"])
    if "continue_on_error" in data or "continue-on-error" in data:
        kwargs["continue_on_error"] = bool(_get_field(data, "continue_on_error"))
    if "strategy" in data:
        kwargs["strategy"] = str(data["strategy"])
    return GraphDefaults(**kwargs)


def _parse_retry(data: Any) -> RetryPolicy:
    """解析 retry 字段为 RetryPolicy。"""
    if not isinstance(data, Mapping):
        raise ValueError(f"retry 必须是映射，收到: {type(data).__name__}")
    kwargs: dict[str, Any] = {}
    if "max_attempts" in data:
        kwargs["max_attempts"] = int(data["max_attempts"])
    if "delay" in data:
        kwargs["delay"] = float(data["delay"])
    if "backoff" in data:
        kwargs["backoff"] = float(data["backoff"])
    if "jitter" in data:
        kwargs["jitter"] = float(data["jitter"])
    return RetryPolicy(**kwargs)


def _get_field(data: Mapping[str, Any], name: str) -> Any:
    """获取字段值，支持 hyphen/underscore 兼容。"""
    if name in data:
        return data[name]
    hyphen = name.replace("_", "-")
    if hyphen in data:
        return data[hyphen]
    return None


def _build_specs(jobs: Mapping[str, Any]) -> list[TaskSpec[Any]]:
    """从 jobs 构建 TaskSpec 列表。"""
    specs: list[TaskSpec[Any]] = []
    for job_id, job_data in jobs.items():
        specs.append(_build_spec(job_id, job_data))
    return specs


def _build_spec(job_id: str, job_data: Mapping[str, Any]) -> TaskSpec[Any]:
    """构建单个 TaskSpec。"""
    if not isinstance(job_data, Mapping):
        raise ValueError(f"job {job_id!r} 必须是映射，收到: {type(job_data).__name__}")

    task_cmd = _parse_cmd(job_id, job_data)
    depends_on = _parse_needs(job_data)

    kwargs: dict[str, Any] = {"cmd": task_cmd, "depends_on": tuple(depends_on)}
    kwargs.update(_parse_optional_fields(job_data))
    return TaskSpec(name=job_id, **kwargs)


def _parse_cmd(job_id: str, job_data: Mapping[str, Any]) -> list[str] | str:
    """解析 cmd / run 字段。

    ``cmd`` 为列表，``run`` 为 shell 字符串；二者必居其一。
    """
    cmd_val = _get_field(job_data, "cmd")
    run_val = _get_field(job_data, "run")
    if cmd_val is not None:
        if isinstance(cmd_val, list):
            return [str(a) for a in cmd_val]
        return str(cmd_val)
    if run_val is not None:
        return str(run_val)
    raise ValueError(f"job {job_id!r} 必须提供 cmd 或 run")


def _parse_needs(job_data: Mapping[str, Any]) -> list[str]:
    """解析 needs 字段，支持列表或单字符串。"""
    needs = _get_field(job_data, "needs")
    if not needs:
        return []
    if isinstance(needs, str):
        return [needs]
    if not isinstance(needs, list):
        raise ValueError(f"needs 必须是列表或字符串，收到: {type(needs).__name__}")
    return [str(n) for n in needs]


def _parse_optional_fields(job_data: Mapping[str, Any]) -> dict[str, Any]:
    """解析所有可选字段，返回 kwargs 字典。"""
    kwargs: dict[str, Any] = {}

    timeout = _get_field(job_data, "timeout")
    if timeout is not None:
        kwargs["timeout"] = float(timeout)

    retry = _get_field(job_data, "retry")
    if retry is not None:
        kwargs["retry"] = _parse_retry(retry)

    cwd = _get_field(job_data, "cwd")
    if cwd is not None:
        kwargs["cwd"] = Path(str(cwd))

    env = _get_field(job_data, "env")
    if env is not None:
        if not isinstance(env, Mapping):
            raise ValueError(f"env 必须是映射，收到: {type(env).__name__}")
        kwargs["env"] = dict(env)

    for bool_field in ("verbose", "allow_upstream_skip", "continue_on_error"):
        val = _get_field(job_data, bool_field)
        if val is not None:
            kwargs[bool_field] = bool(val)

    strategy = _get_field(job_data, "strategy")
    if strategy is not None:
        # 单任务策略覆盖（不支持 matrix）
        kwargs["strategy"] = str(strategy)

    tags = _get_field(job_data, "tags")
    if tags:
        if not isinstance(tags, list):
            raise ValueError(f"tags 必须是列表，收到: {type(tags).__name__}")
        kwargs["tags"] = tuple(str(t) for t in tags)

    return kwargs
