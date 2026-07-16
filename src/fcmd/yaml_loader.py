"""YAML 任务编排（GitHub Actions 风格，简化版）。

从 YAML 文件加载为 :class:`~fcmd.dag.Graph`，支持 ``jobs``/``needs``/
``cmd``/``run``/``env``/``cwd``/``timeout``/``retry``/``strategy`` 等核心字段，
以及 ``if`` 条件判断与 ``matrix`` 矩阵扇出（由 :mod:`fcmd.conditions` 提供）。

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

      # if 条件：基于上游状态/上下文/环境变量跳过任务
      notify:
        needs: [build]
        if: "failure()"           # 仅 build 失败时执行
        cmd: ["python", "-m", "notify"]

      # matrix 矩阵扇出：笛卡尔积展开为多个任务
      test:
        matrix:
          py: ["3.8", "3.9"]
          os: ["linux", "windows"]
        cmd: ["pytest"]
        # 展开为 test(py-3.8)(os-linux) / test(py-3.8)(os-windows) /
        #      test(py-3.9)(os-linux) / test(py-3.9)(os-windows) 四个任务

字段映射
--------
| YAML 字段 | TaskSpec 字段 | 说明 |
|-----------|---------------|------|
| cmd | cmd | 命令列表（支持 ``${{ matrix.X }}`` 替换） |
| run | cmd | shell 字符串（支持 ``${{ matrix.X }}`` 替换） |
| needs | depends_on | 任务依赖列表 |
| env / cwd / timeout / retry | 同名 | 透传（env/cwd/run 支持 matrix 替换） |
| continue-on-error / allow-upstream-skip | 同名（hyphen 兼容 underscore） |
| tags | tags | 自由标签 |
| verbose | verbose | 详细输出 |
| strategy | strategy | 单任务执行策略覆盖 |
| if | conditions | 条件表达式（转为单元素 Condition 元组） |
| matrix | — | 矩阵配置，展开为多个 TaskSpec |

matrix 展开
------------
``matrix`` 字段为映射，每个键对应值列表。笛卡尔积展开后每个组合产生一个
独立 TaskSpec，任务名追加 ``"(key1-value1)(key2-value2)"`` 后缀。
``${{ matrix.NAME }}`` 占位符在 ``cmd``/``run``/``env``/``cwd``/``tags``
字段中被替换为对应组合值。

限制：matrix 任务的 ``needs`` 不替换 matrix 变量；matrix 任务间不能直接
相互引用（需手动指定展开后的任务名）。如需复杂场景请用编程式 API。

if 表达式
----------
支持 ``success()`` / ``failure()`` / ``always()`` 状态检查，
``ctx.NAME == "value"`` 上下文比较，``vars.NAME`` 环境变量访问，
``not`` / ``and`` / ``or`` 逻辑组合。详见 :mod:`fcmd.conditions`。
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

from fcmd.conditions import (
    ConditionError,
    expand_matrix,
    matrix_suffix,
    parse_if,
    substitute_matrix_vars,
)
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
    """从 jobs 构建 TaskSpec 列表，支持 matrix 展开。"""
    specs: list[TaskSpec[Any]] = []
    for job_id, job_data in jobs.items():
        matrix = _parse_matrix(job_data)
        if matrix is None:
            specs.append(_build_spec(job_id, job_data))
            continue
        # matrix 展开：每个组合产生一个独立 TaskSpec
        combos = expand_matrix(matrix)
        for combo in combos:
            expanded_id = f"{job_id}{matrix_suffix(combo)}"
            specs.append(_build_spec(expanded_id, job_data, combo))
    return specs


def _parse_matrix(job_data: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """解析 matrix 字段，返回矩阵映射或 ``None``。"""
    matrix = _get_field(job_data, "matrix")
    if matrix is None:
        return None
    if not isinstance(matrix, Mapping):
        raise ValueError(f"matrix 必须是映射，收到: {type(matrix).__name__}")
    return matrix


def _build_spec(
    job_id: str,
    job_data: Mapping[str, Any],
    combo: Mapping[str, Any] | None = None,
) -> TaskSpec[Any]:
    """构建单个 TaskSpec。

    Parameters
    ----------
    job_id:
        任务名（matrix 展开后已含后缀）。
    job_data:
        原始 job 配置映射。
    combo:
        matrix 组合字典；非 ``None`` 时对 cmd/run/env/cwd/tags 字段做
        ``${{ matrix.X }}`` 占位符替换。``needs`` 字段不替换（限制见模块 docstring）。
    """
    if not isinstance(job_data, Mapping):
        raise ValueError(f"job {job_id!r} 必须是映射，收到: {type(job_data).__name__}")

    task_cmd = _parse_cmd(job_id, job_data, combo)
    depends_on = _parse_needs(job_data)

    kwargs: dict[str, Any] = {"cmd": task_cmd, "depends_on": tuple(depends_on)}
    kwargs.update(_parse_optional_fields(job_data, combo))

    # if 条件表达式 → conditions 元组
    if_expr = _get_field(job_data, "if")
    if if_expr is not None:
        try:
            kwargs["conditions"] = (parse_if(str(if_expr)),)
        except ConditionError as exc:
            raise ValueError(f"job {job_id!r} if 表达式错误: {exc}") from exc

    return TaskSpec(name=job_id, **kwargs)


def _parse_cmd(
    job_id: str,
    job_data: Mapping[str, Any],
    combo: Mapping[str, Any] | None = None,
) -> list[str] | str:
    """解析 cmd / run 字段，支持 matrix 变量替换。

    ``cmd`` 为列表，``run`` 为 shell 字符串；二者必居其一。
    """
    cmd_val = _get_field(job_data, "cmd")
    run_val = _get_field(job_data, "run")
    if cmd_val is not None:
        if isinstance(cmd_val, list):
            return [_substitute(str(a), combo) for a in cmd_val]
        return _substitute(str(cmd_val), combo)
    if run_val is not None:
        return _substitute(str(run_val), combo)
    raise ValueError(f"job {job_id!r} 必须提供 cmd 或 run")


def _parse_needs(job_data: Mapping[str, Any]) -> list[str]:
    """解析 needs 字段，支持列表或单字符串。

    needs 不替换 matrix 变量（限制见模块 docstring）。
    """
    needs = _get_field(job_data, "needs")
    if not needs:
        return []
    if isinstance(needs, str):
        return [needs]
    if not isinstance(needs, list):
        raise ValueError(f"needs 必须是列表或字符串，收到: {type(needs).__name__}")
    return [str(n) for n in needs]


def _substitute(text: str, combo: Mapping[str, Any] | None) -> str:
    """若提供 combo，替换 ``${{ matrix.X }}`` 占位符；否则原样返回。"""
    if combo is None:
        return text
    return substitute_matrix_vars(text, combo)


def _parse_optional_fields(
    job_data: Mapping[str, Any],
    combo: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """解析所有可选字段，返回 kwargs 字典。支持 matrix 变量替换。"""
    kwargs: dict[str, Any] = {}

    timeout = _get_field(job_data, "timeout")
    if timeout is not None:
        kwargs["timeout"] = float(timeout)

    retry = _get_field(job_data, "retry")
    if retry is not None:
        kwargs["retry"] = _parse_retry(retry)

    cwd = _get_field(job_data, "cwd")
    if cwd is not None:
        kwargs["cwd"] = Path(_substitute(str(cwd), combo))

    env = _get_field(job_data, "env")
    if env is not None:
        if not isinstance(env, Mapping):
            raise ValueError(f"env 必须是映射，收到: {type(env).__name__}")
        kwargs["env"] = {k: _substitute(str(v), combo) for k, v in env.items()}

    for bool_field in ("verbose", "allow_upstream_skip", "continue_on_error"):
        val = _get_field(job_data, bool_field)
        if val is not None:
            kwargs[bool_field] = bool(val)

    strategy = _get_field(job_data, "strategy")
    if strategy is not None:
        kwargs["strategy"] = str(strategy)

    tags = _get_field(job_data, "tags")
    if tags:
        if not isinstance(tags, list):
            raise ValueError(f"tags 必须是列表，收到: {type(tags).__name__}")
        kwargs["tags"] = tuple(_substitute(str(t), combo) for t in tags)

    return kwargs
