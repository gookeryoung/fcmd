"""sysinfo - 收集并打印当前环境的系统信息。

默认输出保持既有的 ``键: 值`` 文本表；``--json`` 为增量开关，把同一份
收集结果按英文稳定字段呈现，供机器读取。
"""

from __future__ import annotations

from typing import Any

import fcmd as fx
from fcmd.core.result import emit_result
from fcmd.infras.system import collect_system_info

__all__ = ["sysinfo"]

# 文本键 → JSON 顶层字段名；值沿用文本中的字符串，避免二次格式化失真。
_SCALAR_FIELDS: dict[str, str] = {
    "操作系统": "os",
    "平台": "platform",
    "架构": "arch",
    "Python 版本": "python",
    "Python 路径": "python_path",
    "处理器": "cpu",
    "CPU 核心数": "cpu_cores",
    "工作目录": "cwd",
    "开机时间": "boot_time",
}

# 文本键 → (JSON 分组名, 组内字段名)；缺失项直接省略，不补空值。
_GROUPED_FIELDS: dict[str, tuple[str, str]] = {
    "内存总量": ("memory", "total"),
    "内存已用": ("memory", "used"),
    "内存可用": ("memory", "available"),
    "交换区总量": ("swap", "total"),
    "交换区已用": ("swap", "used"),
    "磁盘总量": ("disk", "total"),
    "磁盘已用": ("disk", "used"),
    "磁盘可用": ("disk", "free"),
}


def _to_mapping(info: dict[str, str]) -> dict[str, Any]:
    """把文本键值表转换为 JSON 字段；平台不支持的信息不编造，直接省略。"""
    data: dict[str, Any] = {}
    for label, name in _SCALAR_FIELDS.items():
        if label in info:
            data[name] = info[label]
    for label, (group, name) in _GROUPED_FIELDS.items():
        if label in info:
            data.setdefault(group, {})[name] = info[label]
    return data


@fx.tool(
    "sysinfo",
    help="收集并打印系统信息",
    param_help={"json_output": "以 JSON 输出（--json 同义）"},
    examples=[
        "fcmd sysinfo",
        "fcmd sysinfo --json",
        "fcmd sysinfo --json > sysinfo.json",
    ],
)
def sysinfo(json_output: bool = False) -> None:
    """收集并打印当前环境的系统信息（Python/平台/内存/磁盘/CPU）。"""
    info = collect_system_info()
    if json_output:
        emit_result("sysinfo", None, _to_mapping(info))
        return
    print("=" * 50)
    print("系统信息")
    print("=" * 50)
    for key, value in info.items():
        print(f"  {key:16s}: {value}")
    print("=" * 50)
