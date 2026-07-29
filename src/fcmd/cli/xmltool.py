"""xmltool - XML 处理工具。

基于标准库 ``xml.etree.ElementTree`` 提供 XML 格式化、压缩、XPath 提取与良构校验。

示例
----
    fcmd xmltool pretty data.xml                # 格式化打印（默认 2 空格缩进）
    fcmd xmltool pretty data.xml --indent 4    # 4 空格缩进
    fcmd xmltool minify data.xml                # 压缩为单行
    fcmd xmltool extract data.xml ".//item/@id" # XPath 提取属性值
    fcmd xmltool validate data.xml              # 良构校验
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from pathlib import Path

import fcmd

__all__ = [
    "extract_xml",
    "minify_xml",
    "pretty_xml",
    "read_xml",
    "validate_xml",
    "write_xml",
]


# ============================================================================
# 公共函数
# ============================================================================


def read_xml(filepath: Path) -> ET.Element:
    """读取 XML 文件并解析为根元素。

    Parameters
    ----------
    filepath:
        XML 文件路径

    Returns
    -------
    xml.etree.ElementTree.Element
        根元素

    Raises
    ------
    FileNotFoundError
        文件不存在
    ET.ParseError
        XML 良构错误
    """
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    return ET.parse(filepath).getroot()


def write_xml(filepath: Path, element: ET.Element, indent: int = 2) -> None:
    """写入 XML 文件（自动缩进）。

    Parameters
    ----------
    filepath:
        目标 XML 文件路径
    element:
        待序列化的根元素
    indent:
        缩进空格数（默认 2）
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)
    clone = copy.deepcopy(element)
    ET.indent(clone, space=" " * indent)
    tree = ET.ElementTree(clone)
    tree.write(filepath, encoding="utf-8", xml_declaration=True)


def pretty_xml(element: ET.Element, indent: int = 2) -> str:
    """格式化为多行 XML 字符串。

    Parameters
    ----------
    element:
        待格式化的根元素
    indent:
        缩进空格数（默认 2）

    Returns
    -------
    str
        多行 XML 文本
    """
    clone = copy.deepcopy(element)
    ET.indent(clone, space=" " * indent)
    return ET.tostring(clone, encoding="unicode")


def minify_xml(element: ET.Element) -> str:
    """压缩为单行 XML 字符串。

    注意：``ET.tostring`` 不会自动去除源 XML 中的空白文本节点；
    若源 XML 已被 ``ET.indent`` 处理过，压缩结果可能仍含换行。
    对纯结构输出建议从原始未缩进元素调用。

    Parameters
    ----------
    element:
        待压缩的根元素

    Returns
    -------
    str
        单行 XML 文本
    """
    clone = copy.deepcopy(element)
    # 移除元素间纯空白文本节点（保留有意义文本）
    for elem in clone.iter():
        if elem.text is not None and not elem.text.strip():
            elem.text = None
        if elem.tail is not None and not elem.tail.strip():
            elem.tail = None
    return ET.tostring(clone, encoding="unicode")


def extract_xml(element: ET.Element, xpath: str) -> list[str]:
    """按 XPath 提取文本值列表。

    支持元素文本与属性值。属性提取通过路径尾部的 ``/@attr`` 表示，
    如 ``.//item/@id`` 会先查找所有 ``item`` 元素，再取其 ``id`` 属性。

    注意：``xml.etree.ElementTree.findall`` 仅支持 XPath 子集
    （不支持 ``text()``、``string()`` 等函数），本函数仅做元素/属性值提取。

    Parameters
    ----------
    element:
        待查询的根元素
    xpath:
        XPath 表达式（如 ``.//item`` 或 ``.//item/@id``）

    Returns
    -------
    list[str]
        命中结果的字符串列表；元素文本去除首尾空白；无命中返回空列表

    Raises
    ------
    ET.ParseError
        XPath 语法错误时
    """
    # 属性提取分支：路径以 /@attr 结尾
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

    # 元素文本提取分支
    try:
        result = element.findall(xpath)
    except (SyntaxError, TypeError) as exc:
        raise ET.ParseError(f"XPath 语法错误: {xpath}") from exc
    return [item.text.strip() if item.text else "" for item in result]


def validate_xml(filepath: Path) -> None:
    """校验 XML 文件是否良构。

    仅校验良构性（well-formed），不做 DTD/Schema 校验。

    Parameters
    ----------
    filepath:
        XML 文件路径

    Raises
    ------
    FileNotFoundError
        文件不存在
    ET.ParseError
        XML 良构错误时（含错误位置）
    """
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    ET.parse(filepath)


# ============================================================================
# CLI 子命令
# ============================================================================


def _read_or_print(filepath: Path) -> ET.Element | None:
    """读取 XML 文件，失败时打印错误并返回 ``None``。"""
    try:
        return read_xml(filepath)
    except FileNotFoundError as exc:
        print(str(exc))
        return None
    except ET.ParseError as exc:
        print(f"XML 解析失败: {exc}")
        return None


@fcmd.tool("xmltool", subcommand="pretty", help="格式化打印 XML")
def xml_pretty_cmd(file: Path, indent: int = 2) -> None:
    """格式化打印 XML 文件内容。

    Parameters
    ----------
    file:
        XML 文件路径
    indent:
        缩进空格数（默认 2）
    """
    root = _read_or_print(file)
    if root is None:
        return
    print(pretty_xml(root, indent=indent))


@fcmd.tool("xmltool", subcommand="minify", help="压缩 XML 为单行")
def xml_minify_cmd(file: Path) -> None:
    """压缩 XML 文件为单行输出。

    Parameters
    ----------
    file:
        XML 文件路径
    """
    root = _read_or_print(file)
    if root is None:
        return
    print(minify_xml(root))


@fcmd.tool("xmltool", subcommand="extract", help="按 XPath 提取值")
def xml_extract_cmd(file: Path, xpath: str) -> None:
    """按 XPath 提取 XML 文件中的值并逐行打印。

    Parameters
    ----------
    file:
        XML 文件路径
    xpath:
        XPath 表达式（如 ``.//item/@id`` 或 ``.//item/title``）
    """
    root = _read_or_print(file)
    if root is None:
        return
    try:
        values = extract_xml(root, xpath)
    except ET.ParseError as exc:
        print(str(exc))
        return
    if not values:
        print(f"无命中: {xpath}")
        return
    for value in values:
        print(value)


@fcmd.tool("xmltool", subcommand="validate", help="校验 XML 良构性")
def xml_validate_cmd(file: Path) -> None:
    """校验 XML 文件是否良构。

    Parameters
    ----------
    file:
        XML 文件路径
    """
    try:
        validate_xml(file)
    except FileNotFoundError as exc:
        print(str(exc))
        return
    except ET.ParseError as exc:
        print(f"良构校验失败: {exc}")
        return
    print(f"良构校验通过: {file}")


def main() -> None:
    """``xmltool`` 入口：等价于 ``fcmd xmltool <args>``。"""
    from fcmd.cli._common import run_tool_main

    run_tool_main("xmltool")


if __name__ == "__main__":
    main()
