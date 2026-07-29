"""xmltool 工具测试。

验证 ``fcmd.cli.xmltool`` 模块：
- 工具注册与四子命令结构（pretty/minify/extract/validate）
- ``read_xml``/``write_xml`` 基础读写
- ``pretty_xml``/``minify_xml`` 格式化与压缩
- ``extract_xml`` XPath 查询（含元素文本与属性值）
- ``validate_xml`` 良构校验
- CLI 子命令端到端
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.xmltool import (
    extract_xml,
    minify_xml,
    pretty_xml,
    read_xml,
    validate_xml,
    write_xml,
)


# ============================================================================ #
# 辅助函数
# ============================================================================ #
def _write_xml_file(path: Path, content: str) -> None:
    """写入 XML 文件。"""
    path.write_text(content, encoding="utf-8")


_SAMPLE_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<root>\n"
    "  <item id='1'>apple</item>\n"
    "  <item id='2'>banana</item>\n"
    "  <group>\n"
    "    <item id='3'>cherry</item>\n"
    "  </group>\n"
    "</root>\n"
)


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """xmltool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "xmltool" in list_tools()

    def test_subcommands(self) -> None:
        """xmltool 有 pretty/minify/extract/validate 四个子命令。"""
        subs = list_subcommands("xmltool")
        assert set(subs) == {"pretty", "minify", "extract", "validate"}


# ============================================================================ #
# read_xml / write_xml
# ============================================================================ #
class TestReadXml:
    """read_xml 读取测试。"""

    def test_basic(self, tmp_path: Path) -> None:
        """基本读取返回根元素。"""
        path = tmp_path / "a.xml"
        _write_xml_file(path, _SAMPLE_XML)
        root = read_xml(path)
        assert root.tag == "root"
        assert len(root.findall("item")) == 2

    def test_nonexistent(self, tmp_path: Path) -> None:
        """不存在抛 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError, match="文件不存在"):
            read_xml(tmp_path / "no.xml")

    def test_parse_error(self, tmp_path: Path) -> None:
        """良构错误抛 ET.ParseError。"""
        path = tmp_path / "broken.xml"
        _write_xml_file(path, "<root><item>unclosed</root>")
        with pytest.raises(ET.ParseError):
            read_xml(path)


class TestWriteXml:
    """write_xml 写入测试。"""

    def test_basic(self, tmp_path: Path) -> None:
        """写入后可重新读取。"""
        path = tmp_path / "out.xml"
        root = ET.Element("root")
        child = ET.SubElement(root, "item")
        child.set("id", "1")
        child.text = "hello"
        write_xml(path, root, indent=4)
        # 重新读取验证
        loaded = read_xml(path)
        assert loaded.tag == "root"
        items = loaded.findall("item")
        assert len(items) == 1
        assert items[0].get("id") == "1"
        assert items[0].text == "hello"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        """自动创建父目录。"""
        path = tmp_path / "sub" / "deep" / "out.xml"
        root = ET.Element("r")
        write_xml(path, root)
        assert path.exists()


# ============================================================================ #
# pretty_xml / minify_xml
# ============================================================================ #
class TestPrettyXml:
    """pretty_xml 格式化测试。"""

    def test_default_indent(self) -> None:
        """默认 2 空格缩进。"""
        root = ET.fromstring("<root><item>text</item></root>")
        out = pretty_xml(root)
        assert "<root>" in out
        assert "  <item>text</item>" in out

    def test_custom_indent(self) -> None:
        """自定义缩进。"""
        root = ET.fromstring("<root><item>text</item></root>")
        out = pretty_xml(root, indent=4)
        assert "    <item>text</item>" in out

    def test_does_not_mutate_input(self) -> None:
        """格式化不修改原元素。"""
        root = ET.fromstring("<root><item>text</item></root>")
        original = ET.tostring(root, encoding="unicode")
        pretty_xml(root)
        # 原元素结构不变（无缩进文本）
        assert ET.tostring(root, encoding="unicode") == original


class TestMinifyXml:
    """minify_xml 压缩测试。"""

    def test_removes_whitespace(self) -> None:
        """移除纯空白文本节点。"""
        # 先用 pretty_xml 生成缩进版本，再 minify 应回到单行结构
        root = ET.Element("root")
        child = ET.SubElement(root, "item")
        child.text = "value"
        pretty = pretty_xml(root, indent=2)
        # 重新解析缩进后的 XML，得到带空白文本的元素
        indented_root = ET.fromstring(pretty)
        mini = minify_xml(indented_root)
        # 压缩后无换行
        assert "\n" not in mini
        assert "<item>value</item>" in mini

    def test_preserves_meaningful_text(self) -> None:
        """保留有意义文本。"""
        root = ET.fromstring("<root><item>hello world</item></root>")
        mini = minify_xml(root)
        assert "hello world" in mini

    def test_does_not_mutate_input(self) -> None:
        """压缩不修改原元素。"""
        root = ET.fromstring("<root><item>text</item></root>")
        original = ET.tostring(root, encoding="unicode")
        minify_xml(root)
        assert ET.tostring(root, encoding="unicode") == original


# ============================================================================ #
# extract_xml
# ============================================================================ #
class TestExtractXml:
    """extract_xml XPath 提取测试。"""

    def test_extract_element_text(self) -> None:
        """提取元素文本。"""
        root = ET.fromstring("<root><item>apple</item><item>banana</item></root>")
        values = extract_xml(root, ".//item")
        assert values == ["apple", "banana"]

    def test_extract_attribute(self) -> None:
        """提取属性值。"""
        root = ET.fromstring("<root><item id='1'>a</item><item id='2'>b</item></root>")
        values = extract_xml(root, ".//item/@id")
        assert values == ["1", "2"]

    def test_no_match_returns_empty(self) -> None:
        """无命中返回空列表。"""
        root = ET.fromstring("<root></root>")
        assert extract_xml(root, ".//missing") == []

    def test_nested_path(self) -> None:
        """嵌套路径提取。"""
        root = ET.fromstring("<root><group><item>x</item></group></root>")
        values = extract_xml(root, ".//group/item")
        assert values == ["x"]

    def test_invalid_xpath_raises_parse_error(self) -> None:
        """无效 XPath 抛 ET.ParseError。"""
        root = ET.fromstring("<root/>")
        with pytest.raises(ET.ParseError, match="XPath 语法错误"):
            extract_xml(root, "invalid[[[")

    def test_attribute_path_empty_attr_raises(self) -> None:
        """属性路径以 /@ 结尾但无属性名时抛 ET.ParseError。"""
        root = ET.fromstring("<root><item/></root>")
        with pytest.raises(ET.ParseError, match="XPath 语法错误"):
            extract_xml(root, ".//item/@")

    def test_attribute_missing_on_element_skipped(self) -> None:
        """元素未含目标属性时跳过（不报错）。"""
        root = ET.fromstring("<root><item id='1'>a</item><item>b</item></root>")
        # 第二个 item 无 id 属性，应被跳过
        values = extract_xml(root, ".//item/@id")
        assert values == ["1"]

    def test_attribute_findall_error_raises_parse_error(self) -> None:
        """属性分支中 element path 无效时抛 ET.ParseError。"""
        root = ET.fromstring("<root/>")
        with pytest.raises(ET.ParseError, match="XPath 语法错误"):
            extract_xml(root, "invalid[[[/@id")

    def test_attribute_only_on_root(self) -> None:
        """路径仅为 ``/@attr`` 时取根元素属性。"""
        root = ET.fromstring("<root id='r1'/>")
        values = extract_xml(root, "/@id")
        assert values == ["r1"]


# ============================================================================ #
# validate_xml
# ============================================================================ #
class TestValidateXml:
    """validate_xml 良构校验测试。"""

    def test_valid_xml_passes(self, tmp_path: Path) -> None:
        """良构 XML 校验通过（无异常）。"""
        path = tmp_path / "ok.xml"
        _write_xml_file(path, _SAMPLE_XML)
        validate_xml(path)  # 无异常即通过

    def test_nonexistent_raises(self, tmp_path: Path) -> None:
        """文件不存在抛 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError, match="文件不存在"):
            validate_xml(tmp_path / "no.xml")

    def test_broken_xml_raises_parse_error(self, tmp_path: Path) -> None:
        """非良构 XML 抛 ET.ParseError。"""
        path = tmp_path / "broken.xml"
        _write_xml_file(path, "<root><unclosed></root>")
        with pytest.raises(ET.ParseError):
            validate_xml(path)


# ============================================================================ #
# CLI 子命令测试
# ============================================================================ #
class TestXmltoolCLI:
    """``xmltool`` 通过 ``run_tool`` 调用测试。"""

    def test_pretty_via_run_tool(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd xmltool pretty <file> 打印格式化 XML。"""
        path = tmp_path / "in.xml"
        _write_xml_file(path, "<root><item>x</item></root>")
        code = run_tool("xmltool", ["pretty", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "<root>" in out
        assert "<item>x</item>" in out

    def test_pretty_custom_indent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--indent 4 应用 4 空格缩进。"""
        path = tmp_path / "in.xml"
        _write_xml_file(path, "<root><item>x</item></root>")
        code = run_tool("xmltool", ["pretty", str(path), "--indent", "4"])
        assert code == 0
        out = capsys.readouterr().out
        assert "    <item>x</item>" in out

    def test_pretty_nonexistent_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """文件不存在打印错误，返回 0。"""
        code = run_tool("xmltool", ["pretty", str(tmp_path / "no.xml")])
        assert code == 0
        out = capsys.readouterr().out
        assert "文件不存在" in out

    def test_pretty_broken_xml(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """非良构 XML 打印解析错误，返回 0。"""
        path = tmp_path / "broken.xml"
        _write_xml_file(path, "<root><unclosed></root>")
        code = run_tool("xmltool", ["pretty", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "XML 解析失败" in out

    def test_minify_via_run_tool(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd xmltool minify <file> 压缩输出。"""
        # 写入已缩进的 XML
        path = tmp_path / "in.xml"
        _write_xml_file(path, "<root>\n  <item>x</item>\n</root>\n")
        code = run_tool("xmltool", ["minify", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        # 框架输出包裹状态行，实际 XML 内容应在中间一行
        assert "<root><item>x</item></root>" in out

    def test_minify_nonexistent_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """文件不存在打印错误。"""
        code = run_tool("xmltool", ["minify", str(tmp_path / "no.xml")])
        assert code == 0
        out = capsys.readouterr().out
        assert "文件不存在" in out

    def test_minify_broken_xml(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """非良构 XML 打印解析错误。"""
        path = tmp_path / "broken.xml"
        _write_xml_file(path, "<root><unclosed></root>")
        code = run_tool("xmltool", ["minify", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "XML 解析失败" in out

    def test_extract_via_run_tool(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd xmltool extract <file> <xpath> 逐行打印命中值。"""
        path = tmp_path / "in.xml"
        _write_xml_file(path, "<root><item>a</item><item>b</item></root>")
        code = run_tool("xmltool", ["extract", str(path), ".//item"])
        assert code == 0
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line]
        assert "a" in lines
        assert "b" in lines

    def test_extract_no_match(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """无命中时打印提示。"""
        path = tmp_path / "in.xml"
        _write_xml_file(path, "<root/>")
        code = run_tool("xmltool", ["extract", str(path), ".//missing"])
        assert code == 0
        out = capsys.readouterr().out
        assert "无命中" in out

    def test_extract_invalid_xpath(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """无效 XPath 打印错误。"""
        path = tmp_path / "in.xml"
        _write_xml_file(path, "<root/>")
        code = run_tool("xmltool", ["extract", str(path), "invalid[[["])
        assert code == 0
        out = capsys.readouterr().out
        assert "XPath 语法错误" in out

    def test_extract_nonexistent_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """文件不存在打印错误。"""
        code = run_tool("xmltool", ["extract", str(tmp_path / "no.xml"), ".//item"])
        assert code == 0
        out = capsys.readouterr().out
        assert "文件不存在" in out

    def test_extract_broken_xml(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """非良构 XML 打印解析错误。"""
        path = tmp_path / "broken.xml"
        _write_xml_file(path, "<root><unclosed></root>")
        code = run_tool("xmltool", ["extract", str(path), ".//item"])
        assert code == 0
        out = capsys.readouterr().out
        assert "XML 解析失败" in out

    def test_validate_valid_xml(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """良构 XML 校验通过。"""
        path = tmp_path / "ok.xml"
        _write_xml_file(path, _SAMPLE_XML)
        code = run_tool("xmltool", ["validate", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "良构校验通过" in out

    def test_validate_nonexistent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """文件不存在打印错误。"""
        code = run_tool("xmltool", ["validate", str(tmp_path / "no.xml")])
        assert code == 0
        out = capsys.readouterr().out
        assert "文件不存在" in out

    def test_validate_broken_xml(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """非良构 XML 打印失败。"""
        path = tmp_path / "broken.xml"
        _write_xml_file(path, "<root><unclosed></root>")
        code = run_tool("xmltool", ["validate", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "良构校验失败" in out
