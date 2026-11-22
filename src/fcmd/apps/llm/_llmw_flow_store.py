"""工作流持久化：``${FCMD_HOME}/flows/<id>.json`` 的读写与目录列举。

文件名使用工作流 id（生成时即固定），重命名不会产生孤儿文件；写入走原子替换。
"""

from __future__ import annotations

import re
from pathlib import Path

from fcmd.apps.llm import _llmconfig
from fcmd.apps.llm._llmw_flow_model import FlowError, FlowGraph
from fcmd.infras.jsonio import read_json_file, write_json_atomic

__all__ = ["delete_flow", "flows_dir", "list_flows", "load_flow", "save_flow"]

_SAFE_ID = re.compile(r"[^0-9a-zA-Z_-]")


def flows_dir(home: Path | None = None) -> Path:
    """工作流目录：``${FCMD_HOME:-~/.fcmd}/flows``。"""
    return _llmconfig.home_dir(home) / "flows"


def _flow_path(flow_id: str, home: Path | None = None) -> Path:
    safe = _SAFE_ID.sub("", flow_id)
    if not safe:
        raise FlowError("工作流 id 非法")
    return flows_dir(home) / f"{safe}.json"


def save_flow(flow: FlowGraph, home: Path | None = None) -> Path:
    """原子写工作流；返回文件路径。"""
    flow.validate_ids()
    path = _flow_path(flow.id, home)
    write_json_atomic(path, flow.to_dict())
    return path


def load_flow(path: Path) -> FlowGraph:
    """读取单个工作流文件；损坏 / 版本不支持时抛 :class:`FlowError`。"""
    try:
        raw = read_json_file(path)
    except FileNotFoundError:
        raise FlowError(f"工作流不存在: {path}") from None
    except (OSError, ValueError) as exc:
        raise FlowError(f"读取工作流失败: {path}: {exc}") from exc
    return FlowGraph.from_dict(raw)


def list_flows(home: Path | None = None) -> list[FlowGraph]:
    """列出全部工作流（按名称排序）；损坏文件跳过，不阻塞页面。"""
    directory = flows_dir(home)
    if not directory.is_dir():
        return []
    flows: list[FlowGraph] = []
    for path in sorted(directory.glob("*.json")):
        try:
            flows.append(load_flow(path))
        except FlowError:
            continue
    return sorted(flows, key=lambda flow: flow.name.casefold())


def delete_flow(flow_id: str, home: Path | None = None) -> bool:
    """删除工作流文件；不存在时返回 ``False``。"""
    path = _flow_path(flow_id, home)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True
