"""inifile - INI 配置读写：取值、设值、section 与 key 列举。

示例
----
    fcmd inifile get config.ini database host
    fcmd inifile set config.ini database port 5432
    fcmd inifile list config.ini
    fcmd inifile keys config.ini database
"""

from __future__ import annotations

import configparser
from pathlib import Path

import fcmd as fx
from fcmd.infras import data


@fx.tool(
    "inifile",
    subcommand="get",
    help="获取指定 section.key 的值",
    param_help={
        "file": "INI 文件路径",
        "section": "section 名",
        "key": "key 名",
    },
)
def get(file: Path, section: str, key: str) -> None:
    """获取指定 section.key 的值。"""
    try:
        print(data.get_ini_value(file, section, key))
    except (FileNotFoundError, KeyError, configparser.Error) as exc:
        print(f"错误: {exc}")


@fx.tool(
    "inifile",
    subcommand="set",
    help="设置 section.key 的值并写回文件",
    param_help={
        "file": "INI 文件路径",
        "section": "section 名",
        "key": "key 名",
        "value": "要设置的值",
    },
)
def set(file: Path, section: str, key: str, value: str) -> None:
    """设置 section.key 的值并写回文件。"""
    try:
        data.set_ini_value(file, section, key, value)
    except (FileNotFoundError, configparser.Error) as exc:
        print(f"错误: {exc}")
        return
    print(f"已设置 [{section}] {key} = {value}")


@fx.tool(
    "inifile",
    subcommand="list",
    help="列出所有 section",
    param_help={"file": "INI 文件路径"},
)
def list_sections(file: Path) -> None:
    """列出所有 section。"""
    try:
        sections = data.list_ini_sections(file)
    except (FileNotFoundError, configparser.Error) as exc:
        print(f"错误: {exc}")
        return
    if not sections:
        print("无 section")
        return
    for section in sections:
        print(section)


@fx.tool(
    "inifile",
    subcommand="keys",
    help="列出 section 的所有 key",
    param_help={
        "file": "INI 文件路径",
        "section": "section 名",
    },
)
def keys(file: Path, section: str) -> None:
    """列出 section 的所有 key。"""
    try:
        key_list = data.list_ini_keys(file, section)
    except (FileNotFoundError, KeyError, configparser.Error) as exc:
        print(f"错误: {exc}")
        return
    if not key_list:
        print(f"section {section!r} 无 key")
        return
    for key in key_list:
        print(key)
