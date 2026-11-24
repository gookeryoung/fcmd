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
from fcmd.core.result import emit_result, report_error
from fcmd.infras import data


def _report_ini_error(subcommand: str, exc: Exception, json_output: bool) -> None:
    """按异常类型呈现 INI 读写错误（文件缺失 4 / 解析或键缺失 3）。"""
    if isinstance(exc, FileNotFoundError):
        report_error(
            "inifile",
            subcommand,
            code="FILE_NOT_FOUND",
            message=str(exc),
            field="file",
            expected="存在的 INI 文件",
            json_output=json_output,
        )
        return
    if isinstance(exc, (KeyError, configparser.NoSectionError, configparser.NoOptionError)):
        report_error(
            "inifile",
            subcommand,
            code="NOT_FOUND",
            message=f"未找到: {exc.args[0] if exc.args else exc}",
            json_output=json_output,
        )
        return
    report_error(
        "inifile",
        subcommand,
        code="INVALID_ARGUMENT",
        message=f"INI 解析失败: {exc}",
        field="file",
        expected="语法正确的 INI 文件",
        json_output=json_output,
    )


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
def get(file: Path, section: str, key: str, json_output: bool = False) -> None:
    """获取指定 section.key 的值。"""
    try:
        value = data.get_ini_value(file, section, key)
    except (FileNotFoundError, KeyError, configparser.Error) as exc:
        _report_ini_error("get", exc, json_output)
        return
    if json_output:
        emit_result("inifile", "get", {"section": section, "key": key, "value": value})
        return
    print(value)


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
def set(file: Path, section: str, key: str, value: str, json_output: bool = False) -> None:
    """设置 section.key 的值并写回文件。"""
    try:
        data.set_ini_value(file, section, key, value)
    except (FileNotFoundError, configparser.Error) as exc:
        _report_ini_error("set", exc, json_output)
        return
    if json_output:
        emit_result("inifile", "set", {"section": section, "key": key, "value": value, "file": str(file)})
        return
    print(f"已设置 [{section}] {key} = {value}")


@fx.tool(
    "inifile",
    subcommand="list",
    help="列出所有 section",
    param_help={"file": "INI 文件路径"},
)
def list_sections(file: Path, json_output: bool = False) -> None:
    """列出所有 section。"""
    try:
        sections = data.list_ini_sections(file)
    except (FileNotFoundError, configparser.Error) as exc:
        _report_ini_error("list", exc, json_output)
        return
    if json_output:
        emit_result("inifile", "list", {"sections": sections})
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
def keys(file: Path, section: str, json_output: bool = False) -> None:
    """列出 section 的所有 key。"""
    try:
        key_list = data.list_ini_keys(file, section)
    except (FileNotFoundError, KeyError, configparser.Error) as exc:
        _report_ini_error("keys", exc, json_output)
        return
    if json_output:
        emit_result("inifile", "keys", {"section": section, "keys": key_list})
        return
    if not key_list:
        print(f"section {section!r} 无 key")
        return
    for key in key_list:
        print(key)
