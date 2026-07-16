"""fcmd 数据模型层。

抽离 CLI 工具中的共性数据结构与行为，提供领域模型。

子模块
------
- ``command``：命令执行模型（``CommandResult``/``run_command``）
- ``filefilter``：文件过滤模型（``IgnoreSpec``/``should_ignore``/``to_shutil_ignore``）
- ``version``：版本号模型（``Version``/``BumpPart``/``parse_version``）
"""

from __future__ import annotations

from fcmd.models.command import CommandResult, run_command
from fcmd.models.filefilter import IgnoreSpec, should_ignore, to_shutil_ignore
from fcmd.models.version import BumpPart, Version, parse_version

__all__ = [
    "BumpPart",
    "CommandResult",
    "IgnoreSpec",
    "Version",
    "parse_version",
    "run_command",
    "should_ignore",
    "to_shutil_ignore",
]
