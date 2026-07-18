"""jsontool 工具测试。

验证 ``fcmd.cli.jsontool`` 模块：
- 工具注册与四子命令结构（pretty/minify/query/sort）
- ``read_json``/``write_json`` 基础读写
- ``pretty_json``/``minify_json`` 格式化与压缩
- ``query_json`` 点路径查询（含错误分支）
- ``sort_keys`` 递归排序
- CLI 子命令端到端
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.jsontool import (
    minify_json,
    pretty_json,
    query_json,
    read_json,
    sort_keys,
    write_json,
)


# ============================================================================ #
# 辅助函数
# ============================================================================ #
def _write_json_file(path: Path, data: Any) -> None:
    """写入 JSON 文件。"""
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """jsontool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "jsontool" in list_tools()

    def test_subcommands(self) -> None:
        """jsontool 有 pretty/minify/query/sort 四个子命令。"""
        subs = list_subcommands("jsontool")
        assert set(subs) == {"pretty", "minify", "query", "sort"}


# ============================================================================ #
# read_json / write_json
# ============================================================================ #
class TestReadJson:
    """read_json 读取测试。"""

    def test_basic(self, tmp_path: Path) -> None:
        """基本读取。"""
        path = tmp_path / "a.json"
        _write_json_file(path, {"a": 1, "b": [2, 3]})
        data = read_json(path)
        assert data == {"a": 1, "b": [2, 3]}

    def test_nonexistent(self, tmp_path: Path) -> None:
        """不存在抛 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError, match="文件不存在"):
            read_json(tmp_path / "no.json")


class TestWriteJson:
    """write_json 写入测试。"""

    def test_basic(self, tmp_path: Path) -> None:
        """基本写入。"""
        path = tmp_path / "out.json"
        write_json(path, {"a": 1})
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == {"a": 1}

    def test_create_parent_dir(self, tmp_path: Path) -> None:
        """自动创建父目录。"""
        path = tmp_path / "sub" / "deep" / "out.json"
        write_json(path, {"a": 1})
        assert path.exists()

    def test_custom_indent(self, tmp_path: Path) -> None:
        """自定义缩进。"""
        path = tmp_path / "out.json"
        write_json(path, {"a": 1}, indent=4)
        text = path.read_text(encoding="utf-8")
        assert "\n    " in text  # 4 空格缩进


# ============================================================================ #
# pretty_json / minify_json
# ============================================================================ #
class TestPrettyJson:
    """pretty_json 格式化测试。"""

    def test_basic(self) -> None:
        """基本格式化。"""
        text = pretty_json({"a": 1, "b": [2, 3]})
        assert '"a": 1' in text
        assert '"b": [' in text
        assert "\n" in text  # 多行

    def test_custom_indent(self) -> None:
        """自定义缩进。"""
        text = pretty_json({"a": 1}, indent=4)
        assert "\n    " in text

    def test_unicode_preserved(self) -> None:
        """中文不转义。"""
        text = pretty_json({"name": "张三"})
        assert "张三" in text
        assert "\\u" not in text


class TestMinifyJson:
    """minify_json 压缩测试。"""

    def test_basic(self) -> None:
        """基本压缩。"""
        text = minify_json({"a": 1, "b": [2, 3]})
        assert text == '{"a":1,"b":[2,3]}'

    def test_no_whitespace(self) -> None:
        """无空白字符。"""
        text = minify_json({"a": 1, "b": 2})
        assert " " not in text
        assert "\n" not in text

    def test_unicode_preserved(self) -> None:
        """中文不转义。"""
        text = minify_json({"name": "张三"})
        assert "张三" in text


# ============================================================================ #
# query_json
# ============================================================================ #
class TestQueryJson:
    """query_json 点路径查询测试。"""

    def test_empty_path(self) -> None:
        """空路径返回原对象。"""
        data = {"a": 1}
        assert query_json(data, "") is data

    def test_object_key(self) -> None:
        """对象键查询。"""
        data = {"a": {"b": {"c": 1}}}
        assert query_json(data, "a.b.c") == 1

    def test_list_index(self) -> None:
        """列表索引查询。"""
        data = {"list": [10, 20, 30]}
        assert query_json(data, "list.0") == 10
        assert query_json(data, "list.2") == 30

    def test_mixed_path(self) -> None:
        """混合对象与列表路径。"""
        data = {"a": {"b": [{"c": 1}, {"c": 2}]}}
        assert query_json(data, "a.b.0.c") == 1
        assert query_json(data, "a.b.1.c") == 2

    def test_key_missing(self) -> None:
        """键不存在抛 KeyError。"""
        with pytest.raises(KeyError, match="键不存在: x"):
            query_json({"a": 1}, "x")

    def test_index_out_of_range(self) -> None:
        """索引越界抛 IndexError。"""
        with pytest.raises(IndexError, match="列表索引越界"):
            query_json([1, 2], "5")

    def test_negative_index(self) -> None:
        """负索引抛 IndexError（仅允许非负）。"""
        with pytest.raises(IndexError, match="列表索引越界"):
            query_json([1, 2], "-1")

    def test_list_with_string_key(self) -> None:
        """对列表用字符串键抛 TypeError。"""
        with pytest.raises(TypeError, match="列表索引必须是整数"):
            query_json([1, 2], "abc")

    def test_scalar_subscript(self) -> None:
        """对非容器取子项抛 TypeError。"""
        with pytest.raises(TypeError, match="无法对非容器类型"):
            query_json(42, "a")

    def test_empty_segment(self) -> None:
        """路径含空段抛 ValueError。"""
        with pytest.raises(ValueError, match="路径格式错误"):
            query_json({"a": 1}, "a..b")


# ============================================================================ #
# sort_keys
# ============================================================================ #
class TestSortKeys:
    """sort_keys 递归排序测试。"""

    def test_basic(self) -> None:
        """基本排序。"""
        data = {"b": 2, "a": 1, "c": 3}
        result = sort_keys(data)
        assert list(result.keys()) == ["a", "b", "c"]

    def test_nested(self) -> None:
        """嵌套对象递归排序。"""
        data = {"z": {"y": 1, "x": 2}, "a": 3}
        result = sort_keys(data)
        assert list(result.keys()) == ["a", "z"]
        assert list(result["z"].keys()) == ["x", "y"]

    def test_list_preserved(self) -> None:
        """列表顺序保持不变，但元素递归排序。"""
        data = [{"b": 2, "a": 1}, {"d": 4, "c": 3}]
        result = sort_keys(data)
        assert list(result[0].keys()) == ["a", "b"]
        assert list(result[1].keys()) == ["c", "d"]

    def test_original_unchanged(self) -> None:
        """原对象不变。"""
        data = {"b": 2, "a": 1}
        result = sort_keys(data)
        assert list(data.keys()) == ["b", "a"]
        assert list(result.keys()) == ["a", "b"]

    def test_scalar(self) -> None:
        """标量原样返回。"""
        assert sort_keys(42) == 42
        assert sort_keys("hello") == "hello"
        assert sort_keys(None) is None


# ============================================================================ #
# CLI 子命令端到端
# ============================================================================ #
class TestCLISubcommands:
    """CLI 子命令端到端测试。"""

    def test_pretty(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """pretty 格式化打印。"""
        path = tmp_path / "a.json"
        _write_json_file(path, {"a": 1})
        code = run_tool("jsontool", ["pretty", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        # 提取 JSON 部分验证（框架含前缀/后缀）
        start = out.index("{")
        end = out.rindex("}") + 1
        assert json.loads(out[start:end]) == {"a": 1}

    def test_pretty_custom_indent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """pretty --indent 4。"""
        path = tmp_path / "a.json"
        _write_json_file(path, {"a": 1})
        code = run_tool("jsontool", ["pretty", str(path), "--indent", "4"])
        assert code == 0
        assert "\n    " in capsys.readouterr().out

    def test_pretty_nonexistent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """pretty 文件不存在提示。"""
        code = run_tool("jsontool", ["pretty", str(tmp_path / "no.json")])
        assert code == 0
        assert "文件不存在" in capsys.readouterr().out

    def test_minify(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """minify 压缩打印。"""
        path = tmp_path / "a.json"
        _write_json_file(path, {"a": 1, "b": 2})
        code = run_tool("jsontool", ["minify", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        # 框架前缀外，应含压缩 JSON 行
        assert '{"a":1,"b":2}' in out

    def test_minify_nonexistent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """minify 文件不存在提示。"""
        code = run_tool("jsontool", ["minify", str(tmp_path / "no.json")])
        assert code == 0
        assert "文件不存在" in capsys.readouterr().out

    def test_query_scalar(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """query 标量结果直接打印。"""
        path = tmp_path / "a.json"
        _write_json_file(path, {"a": {"b": 42}})
        code = run_tool("jsontool", ["query", str(path), "a.b"])
        assert code == 0
        # 标量直接打印（不在 JSON 数组/对象内）
        out = capsys.readouterr().out
        assert "42" in out

    def test_query_container(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """query 容器结果格式化为 JSON。"""
        path = tmp_path / "a.json"
        _write_json_file(path, {"items": [1, 2, 3]})
        code = run_tool("jsontool", ["query", str(path), "items"])
        assert code == 0
        out = capsys.readouterr().out
        start = out.index("[")
        end = out.rindex("]") + 1
        assert json.loads(out[start:end]) == [1, 2, 3]

    def test_query_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """query 路径错误提示。"""
        path = tmp_path / "a.json"
        _write_json_file(path, {"a": 1})
        code = run_tool("jsontool", ["query", str(path), "x"])
        assert code == 0
        assert "键不存在" in capsys.readouterr().out

    def test_query_nonexistent_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """query 文件不存在提示。"""
        code = run_tool("jsontool", ["query", str(tmp_path / "no.json"), "a"])
        assert code == 0
        assert "文件不存在" in capsys.readouterr().out

    def test_sort_print(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """sort 默认打印到标准输出。"""
        path = tmp_path / "a.json"
        _write_json_file(path, {"b": 2, "a": 1})
        code = run_tool("jsontool", ["sort", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        start = out.index("{")
        end = out.rindex("}") + 1
        data = json.loads(out[start:end])
        assert list(data.keys()) == ["a", "b"]

    def test_sort_to_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """sort --output 输出到文件。"""
        path = tmp_path / "a.json"
        _write_json_file(path, {"b": 2, "a": 1})
        out_path = tmp_path / "out.json"
        code = run_tool("jsontool", ["sort", str(path), "--output", str(out_path)])
        assert code == 0
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert list(data.keys()) == ["a", "b"]
        assert "排序完成" in capsys.readouterr().out

    def test_sort_nonexistent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """sort 文件不存在提示。"""
        code = run_tool("jsontool", ["sort", str(tmp_path / "no.json")])
        assert code == 0
        assert "文件不存在" in capsys.readouterr().out
