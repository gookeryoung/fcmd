"""``fcmd.infras.data.xml``（由 ``infras/data.py`` 拆分）。"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from pathlib import Path

from .csv import _require_file

__all__ = ["extract_xml", "minify_xml", "pretty_xml", "read_xml", "validate_xml", "write_xml"]


def _indent_xml(element: ET.Element, space: str = "  ") -> None:
    """给 XML 元素树添加缩进。"""
    ET.indent(element, space=space)


def read_xml(filepath: Path) -> ET.Element:
    """读取 XML 文件并解析为根元素。"""
    _require_file(filepath)
    return ET.parse(filepath).getroot()


def write_xml(filepath: Path, element: ET.Element, indent: int = 2) -> None:
    """写入 XML 文件（自动缩进）。"""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    clone = copy.deepcopy(element)
    _indent_xml(clone, " " * indent)
    ET.ElementTree(clone).write(filepath, encoding="utf-8", xml_declaration=True)


def pretty_xml(element: ET.Element, indent: int = 2) -> str:
    """格式化为多行 XML 字符串。"""
    clone = copy.deepcopy(element)
    _indent_xml(clone, " " * indent)
    return ET.tostring(clone, encoding="unicode")


def minify_xml(element: ET.Element) -> str:
    """压缩为单行 XML 字符串。"""
    clone = copy.deepcopy(element)
    for elem in clone.iter():
        if elem.text is not None and not elem.text.strip():
            elem.text = None
        if elem.tail is not None and not elem.tail.strip():
            elem.tail = None
    return ET.tostring(clone, encoding="unicode")


def extract_xml(element: ET.Element, xpath: str) -> list[str]:
    """按 XPath 提取文本值列表。"""
    if "/@" in xpath:
        idx = xpath.rfind("/@")
        element_path = xpath[:idx]
        attr_name = xpath[idx + 2 :]
        if not attr_name:
            raise ET.ParseError(f"XPath 语法错误: {xpath}")
        if not element_path:
            element_path = "."
        try:
            elements = element.findall(element_path)
        except (SyntaxError, TypeError) as exc:
            raise ET.ParseError(f"XPath 语法错误: {xpath}") from exc
        values: list[str] = []
        for el in elements:
            val = el.get(attr_name)
            if val is not None:
                values.append(val)
        return values

    try:
        result = element.findall(xpath)
    except (SyntaxError, TypeError) as exc:
        raise ET.ParseError(f"XPath 语法错误: {xpath}") from exc
    return [item.text.strip() if item.text else "" for item in result]


def validate_xml(filepath: Path) -> None:
    """校验 XML 文件是否良构。"""
    _require_file(filepath)
    ET.parse(filepath)
