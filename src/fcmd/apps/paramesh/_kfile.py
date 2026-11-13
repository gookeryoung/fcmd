"""LS-DYNA 关键字网格片段输出。

只输出网格相关卡片（节点、单元与命名集合），不写部件、截面、材料，不写
*KEYWORD / *END。``*PART`` 与 ``*SECTION_SHELL`` 由 deck 按 manifest 的
``parts[].role`` / ``parts[].section`` 生成，避免网格片段与材料卡重复。
"""

from __future__ import annotations

from fcmd.apps.paramesh._mesh import (
    SET_AXIS,
    SET_END_END,
    SET_END_START,
    SET_GAP_WALL,
    SET_IF_PROP_WALL,
    SET_NRB,
    SET_OUTER,
    SET_PART_ALL,
    SET_PART_FLUID,
    SET_PART_WALL,
    SET_PROP_GAP,
    SET_REGION_GAP,
    SET_REGION_PROP,
    SET_REGION_WALL,
    SET_WALL_INNER,
    Mesh,
)

__all__ = [
    "NODE_SET_ORDER",
    "NODE_SET_SID_BASE",
    "PART_SET_ORDER",
    "PART_SET_SID_BASE",
    "node_set_id",
    "part_set_id",
    "render_keyword",
]

NODE_SET_ORDER: tuple[str, ...] = (
    SET_AXIS,
    SET_OUTER,
    SET_END_START,
    SET_END_END,
    SET_NRB,
    SET_PROP_GAP,
    SET_GAP_WALL,
    SET_IF_PROP_WALL,
    SET_WALL_INNER,
    SET_REGION_PROP,
    SET_REGION_GAP,
    SET_REGION_WALL,
)
PART_SET_ORDER: tuple[str, ...] = (SET_PART_ALL, SET_PART_FLUID, SET_PART_WALL)

NODE_SET_SID_BASE = 1
"""节点集合 SID 段起点（1–99，按 ``NODE_SET_ORDER`` 固定顺序）。"""
PART_SET_SID_BASE = 101
"""部件集合 SID 段起点（101–199，与节点集错开）。"""

_FIELDS_PER_LINE = 8


def node_set_id(name: str) -> int:
    """节点集合 SID：``NODE_SET_ORDER`` 中的位置 + 1。"""
    return NODE_SET_SID_BASE + NODE_SET_ORDER.index(name)


def part_set_id(name: str) -> int:
    """部件集合 SID：``PART_SET_ORDER`` 中的位置 + 101。"""
    return PART_SET_SID_BASE + PART_SET_ORDER.index(name)


def _fmt(value: float) -> str:
    if abs(value) < 5e-13:
        return "0.0"
    return repr(float(value))


def _data_lines(tokens: list[str]) -> list[str]:
    lines: list[str] = []
    for start in range(0, len(tokens), _FIELDS_PER_LINE):
        lines.append(", ".join(tokens[start : start + _FIELDS_PER_LINE]))
    return lines


def _set_header(sid: int) -> str:
    """``*SET_*_LIST`` 的首张数据卡：SID, DA1, DA2, DA3, DA4。"""
    return f"{sid}, 0, 0, 0, 0"


def render_keyword(mesh: Mesh, mesh_id: str) -> str:
    """渲染网格片段；同一输入产出逐字节一致的文本。"""
    lines: list[str] = [
        "$ paramesh 网格片段（paramesh-mesh/1）",
        f"$ mesh id: {mesh_id}",
        f"$ model: {mesh.model.model_id}",
        f"$ model digest: {mesh.model.digest}",
        "$ 坐标：x=轴向(z)，y=径向(r)，z=0；已按 x 轴为对称轴生成（ELFORM=14）",
        "$ 本片段只含节点 / 单元 / 命名集合；部件与截面由 deck 按 manifest.parts[] 生成",
        "*NODE",
    ]
    for node in mesh.nodes:
        lines.append(f"{node.node_id}, {_fmt(node.z)}, {_fmt(node.r)}, 0.0")
    lines.append("*ELEMENT_SHELL")
    for element in mesh.elements:
        n1, n2, n3, n4 = element.nodes
        lines.append(f"{element.element_id}, {element.part_id}, {n1}, {n2}, {n3}, {n4}")
    for name in NODE_SET_ORDER:
        ids = mesh.node_sets.get(name)
        if not ids:
            continue
        lines.append("*SET_NODE_LIST_TITLE")
        lines.append(name)
        lines.append(_set_header(node_set_id(name)))
        lines.extend(_data_lines([str(node_id) for node_id in ids]))
    for name in PART_SET_ORDER:
        part_set = next((item for item in mesh.part_sets if item.name == name), None)
        if part_set is None or not part_set.part_ids:
            continue
        lines.append("*SET_PART_LIST_TITLE")
        lines.append(name)
        lines.append(_set_header(part_set_id(name)))
        lines.extend(_data_lines([str(pid) for pid in part_set.part_ids]))
    return "\n".join(lines) + "\n"
