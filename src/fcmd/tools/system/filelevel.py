"""filelevel - 文件名等级标记（PUB/NOR/INT/CON/CLA）设置、清除与配置。"""

from __future__ import annotations

from pathlib import Path

import fcmd as fx
from fcmd.infras.fs import (
    config_path,
    default_config,
    load_filelevel_config,
    save_filelevel_config,
    set_file_level,
    update_brackets,
    update_levels,
)

__all__ = ["config", "set_level"]


@fx.tool(
    "filelevel",
    subcommand="set",
    help="设置文件等级",
    param_help={
        "files": "文件路径列表",
        "level": "文件等级（0 清除；正整数使用配置中的等级）",
    },
)
def set_level(files: list[Path], level: int = 0) -> None:
    """为文件名添加或清除等级标记（PUB/NOR/INT/CON/CLA）。

    等级映射与括号字符从 ``${FCMD_HOME:-~/.fcmd}/filelevel.json`` 读取；
    用 ``fcmd filelevel config`` 查看或修改，改完下一次 ``set`` 立即生效。
    """
    for message in set_file_level(files, level):
        print(message)


@fx.tool(
    "filelevel",
    subcommand="config",
    help="查看 / 修改等级配置",
    param_help={
        "init": "配置文件不存在时写入一份默认配置（已存在不覆盖）",
        "reset": "把配置文件重置为内置默认值",
        "level": "修改已有等级，格式 N=标记，可一次写多个",
        "bracket_left": "左括号字符集（空串保持原值）",
        "bracket_right": "右括号字符集（空串保持原值）",
    },
    examples=[
        "fcmd filelevel config",
        "fcmd filelevel config --init",
        "fcmd filelevel config --level 1=PUBLIC 4=CONFIDENTIAL",
        "fcmd filelevel config --bracket-left ' ([_' --bracket-right ' )]_'",
        "fcmd filelevel config --reset",
    ],
)
def config(
    init: bool = False,
    reset: bool = False,
    level: list[str] | None = None,
    bracket_left: str = "",
    bracket_right: str = "",
) -> None:
    """查看当前等级配置与文件路径；可初始化、重置或原地修改等级 / 括号。

    默认只展示，不写文件；``--init`` 在缺失时落一份默认配置，``--reset`` 覆盖为
    默认值，``--level 1=PUBLIC`` 修改已有等级，``--bracket-left/right`` 改括号。
    """
    path = config_path()
    current, warnings = load_filelevel_config(path)
    for warning in warnings:
        print(f"警告: {warning}")
    dirty = False
    if reset and init:
        raise ValueError("--init 与 --reset 只能选一个")
    if reset:
        current = default_config()
        dirty = True
        print("已重置为内置默认配置")
    elif init:
        if path.exists():
            print(f"配置文件已存在，未覆盖: {path}")
        else:
            current = default_config()
            dirty = True
            print(f"已写入默认配置: {path}")
    if level:
        current = update_levels(current, level)
        dirty = True
    if bracket_left or bracket_right:
        current = update_brackets(current, left=bracket_left, right=bracket_right)
        dirty = True
    if dirty:
        saved = save_filelevel_config(current, path)
        print(f"已保存配置: {saved}")

    print(f"配置文件: {path}{'' if path.exists() else '（不存在，当前使用内置默认值）'}")
    print("等级:")
    for key in sorted(current.levels, key=int):
        value = current.levels[key]
        print(f"  {key}: {'（清除）' if key == '0' else value}")
    print(f"括号: 左 {current.left_brackets!r} · 右 {current.right_brackets!r}")
