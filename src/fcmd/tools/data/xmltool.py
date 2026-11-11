"""xmltool - XML 格式化、压缩、XPath 提取与良构校验。

示例
----
    fcmd xmltool pretty data.xml --indent 4
    fcmd xmltool minify data.xml
    fcmd xmltool extract data.xml ".//item/@id"
    fcmd xmltool validate data.xml
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import fcmd as fx
from fcmd.infras import data


def _read_or_report(file: Path) -> ET.Element | None:
    """读取 XML，失败时打印错误并返回 None（与既有输出一致）。"""
    try:
        return data.read_xml(file)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return None
    except ET.ParseError as exc:
        print(f"XML 解析失败: {exc}")
        return None


@fx.tool(
    "xmltool",
    subcommand="pretty",
    help="格式化打印 XML",
    param_help={
        "file": "XML 文件路径",
        "indent": "缩进空格数（默认 2）",
    },
)
def pretty(file: Path, indent: int = 2) -> None:
    """格式化打印 XML。"""
    root = _read_or_report(file)
    if root is None:
        return
    print(data.pretty_xml(root, indent=indent))


@fx.tool(
    "xmltool",
    subcommand="minify",
    help="压缩 XML 为单行",
    param_help={"file": "XML 文件路径"},
)
def minify(file: Path) -> None:
    """压缩 XML 为单行。"""
    root = _read_or_report(file)
    if root is None:
        return
    print(data.minify_xml(root))


@fx.tool(
    "xmltool",
    subcommand="extract",
    help="按 XPath 提取值",
    param_help={
        "file": "XML 文件路径",
        "xpath": "XPath 表达式（如 .//item/@id 或 .//item/title）",
    },
)
def extract(file: Path, xpath: str) -> None:
    """按 XPath 提取值。"""
    root = _read_or_report(file)
    if root is None:
        return
    try:
        values = data.extract_xml(root, xpath)
    except ET.ParseError as exc:
        print(f"错误: {exc}")
        return
    if not values:
        print(f"无命中: {xpath}")
        return
    for value in values:
        print(value)


@fx.tool(
    "xmltool",
    subcommand="validate",
    help="校验 XML 良构性",
    param_help={"file": "XML 文件路径"},
)
def validate(file: Path) -> None:
    """校验 XML 良构性。"""
    try:
        data.validate_xml(file)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return
    except ET.ParseError as exc:
        print(f"良构校验失败: {exc}")
        return
    print(f"良构校验通过: {file}")
