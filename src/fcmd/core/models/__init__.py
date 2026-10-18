"""fcmd 数据模型层。

抽离 CLI 工具中的共性数据结构与行为，提供领域模型。

子模块
------
- ``command``：命令执行模型（``CommandResult``/``run_command``）
- ``filefilter``：文件过滤模型（``IgnoreSpec``/``should_ignore``/``should_skip_parts``/
  ``to_shutil_ignore``，忽略规则唯一出处）
- ``version``：版本号模型（``Version``/``BumpPart``/``parse_version``）
- ``pdf``：PDF 页码/拆分/合并模型（``PageSelection``/``SplitSpec``/``MergeSpec``，
  pdftool 专用，经 ``fcmd.core.models.pdf`` 深路径导入，不进本门面）
"""

from __future__ import annotations

from fcmd.core.filefilter import IgnoreSpec, should_ignore, to_shutil_ignore
from fcmd.core.models.command import CommandResult, run_command
from fcmd.core.version import BumpPart, Version, parse_version

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
