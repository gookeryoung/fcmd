"""csvtool 工具测试。

验证 ``fcmd.cli.csvtool`` 模块：
- 工具注册与五子命令结构（show/to-json/from-json/select/merge）
- ``read_csv``/``write_csv`` 基础读写
- ``csv_to_json``/``json_to_csv`` 双向转换
- ``select_columns`` 列筛选与重排
- ``merge_csvs`` 多文件合并（union/intersection）
- ``format_table`` 表格对齐输出
- CLI 子命令端到端
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.csvtool import (
    csv_to_json,
    format_table,
    json_to_csv,
    merge_csvs,
    read_csv,
    select_columns,
    write_csv,
)


# ============================================================================ #
# 辅助函数
# ============================================================================ #
def _write_csv_bytes(path: Path, content: str) -> None:
    """以 utf-8 写入 CSV 文本。"""
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, data: object) -> None:
    """写入 JSON 数据。"""
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """csvtool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "csvtool" in list_tools()

    def test_subcommands(self) -> None:
        """csvtool 有 show/to-json/from-json/select/merge 五个子命令。"""
        subs = list_subcommands("csvtool")
        assert set(subs) == {"show", "to-json", "from-json", "select", "merge"}


# ============================================================================ #
# read_csv / write_csv
# ============================================================================ #
class TestReadCsv:
    """read_csv 读取测试。"""

    def test_with_header(self, tmp_path: Path) -> None:
        """有表头读取。"""
        path = tmp_path / "a.csv"
        _write_csv_bytes(path, "name,age\nAlice,30\nBob,25\n")
        header, rows = read_csv(path, has_header=True)
        assert header == ["name", "age"]
        assert rows == [["Alice", "30"], ["Bob", "25"]]

    def test_no_header(self, tmp_path: Path) -> None:
        """无表头读取。"""
        path = tmp_path / "a.csv"
        _write_csv_bytes(path, "a,b,c\n1,2,3\n")
        header, rows = read_csv(path, has_header=False)
        assert header is None
        assert rows == [["a", "b", "c"], ["1", "2", "3"]]

    def test_empty_file(self, tmp_path: Path) -> None:
        """空文件返回 (None, [])。"""
        path = tmp_path / "empty.csv"
        _write_csv_bytes(path, "")
        header, rows = read_csv(path)
        assert header is None
        assert rows == []

    def test_nonexistent(self, tmp_path: Path) -> None:
        """不存在抛 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError, match="文件不存在"):
            read_csv(tmp_path / "no.csv")


class TestWriteCsv:
    """write_csv 写入测试。"""

    def test_basic_with_header(self, tmp_path: Path) -> None:
        """带表头写入。"""
        path = tmp_path / "out.csv"
        write_csv(path, [["a", "1"], ["b", "2"]], header=["x", "y"])
        text = path.read_text(encoding="utf-8")
        assert "x,y" in text
        assert "a,1" in text

    def test_no_header(self, tmp_path: Path) -> None:
        """不带表头写入。"""
        path = tmp_path / "out.csv"
        write_csv(path, [["1", "2"]])
        text = path.read_text(encoding="utf-8")
        assert text.startswith("1,2")

    def test_create_parent_dir(self, tmp_path: Path) -> None:
        """自动创建父目录。"""
        path = tmp_path / "sub" / "deep" / "out.csv"
        write_csv(path, [["a"]], header=["h"])
        assert path.exists()


# ============================================================================ #
# csv_to_json / json_to_csv
# ============================================================================ #
class TestCsvToJson:
    """csv_to_json 转换测试。"""

    def test_basic(self, tmp_path: Path) -> None:
        """基本转换。"""
        path = tmp_path / "a.csv"
        _write_csv_bytes(path, "name,age\nAlice,30\nBob,25\n")
        text = csv_to_json(path)
        data = json.loads(text)
        assert data == [{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "25"}]

    def test_custom_indent(self, tmp_path: Path) -> None:
        """自定义缩进。"""
        path = tmp_path / "a.csv"
        _write_csv_bytes(path, "a,b\n1,2\n")
        text = csv_to_json(path, indent=4)
        # 4 空格缩进含 "\n    "
        assert "\n    " in text

    def test_empty_file(self, tmp_path: Path) -> None:
        """空文件返回 "[]"。"""
        path = tmp_path / "empty.csv"
        _write_csv_bytes(path, "")
        assert csv_to_json(path) == "[]"

    def test_short_row_padded(self, tmp_path: Path) -> None:
        """行短于表头时补空字符串。"""
        path = tmp_path / "a.csv"
        _write_csv_bytes(path, "a,b,c\n1,2\n")  # 行只有 2 列
        data = json.loads(csv_to_json(path))
        assert data == [{"a": "1", "b": "2", "c": ""}]

    def test_nonexistent(self, tmp_path: Path) -> None:
        """不存在抛 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError, match="文件不存在"):
            csv_to_json(tmp_path / "no.csv")


class TestJsonToCsv:
    """json_to_csv 转换测试。"""

    def test_basic(self, tmp_path: Path) -> None:
        """基本转换。"""
        path = tmp_path / "a.json"
        _write_json(path, [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}])
        header, rows = json_to_csv(path)
        assert header == ["name", "age"]
        assert rows == [["Alice", "30"], ["Bob", "25"]]

    def test_missing_keys(self, tmp_path: Path) -> None:
        """对象缺失键补空字符串。"""
        path = tmp_path / "a.json"
        _write_json(path, [{"a": "1", "b": "2"}, {"a": "1", "c": "3"}])
        header, rows = json_to_csv(path)
        assert header == ["a", "b", "c"]
        assert rows == [["1", "2", ""], ["1", "", "3"]]

    def test_key_order_preserved(self, tmp_path: Path) -> None:
        """键首次出现顺序保持。"""
        path = tmp_path / "a.json"
        _write_json(path, [{"x": "1", "y": "2"}, {"y": "3", "z": "4"}])
        header, _ = json_to_csv(path)
        assert header == ["x", "y", "z"]

    def test_nonexistent(self, tmp_path: Path) -> None:
        """不存在抛 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError, match="文件不存在"):
            json_to_csv(tmp_path / "no.json")

    def test_not_array(self, tmp_path: Path) -> None:
        """顶层非数组抛 ValueError。"""
        path = tmp_path / "a.json"
        _write_json(path, {"key": "value"})
        with pytest.raises(ValueError, match="顶层必须是数组"):
            json_to_csv(path)

    def test_element_not_object(self, tmp_path: Path) -> None:
        """数组元素非对象抛 ValueError。"""
        path = tmp_path / "a.json"
        _write_json(path, [1, 2, 3])
        with pytest.raises(ValueError, match="数组元素必须是对象"):
            json_to_csv(path)


# ============================================================================ #
# select_columns
# ============================================================================ #
class TestSelectColumns:
    """select_columns 列筛选测试。"""

    def test_basic_select(self) -> None:
        """基本筛选。"""
        header = ["name", "age", "city"]
        rows = [["Alice", "30", "NYC"], ["Bob", "25", "LA"]]
        new_header, new_rows = select_columns(rows, header, ["name"])
        assert new_header == ["name"]
        assert new_rows == [["Alice"], ["Bob"]]

    def test_reorder(self) -> None:
        """列重排。"""
        header = ["a", "b", "c"]
        rows = [["1", "2", "3"]]
        new_header, new_rows = select_columns(rows, header, ["c", "a"])
        assert new_header == ["c", "a"]
        assert new_rows == [["3", "1"]]

    def test_missing_column(self) -> None:
        """列名不存在抛 ValueError。"""
        header = ["a", "b"]
        rows = [["1", "2"]]
        with pytest.raises(ValueError, match="列不存在: c"):
            select_columns(rows, header, ["c"])

    def test_ragged_row(self) -> None:
        """行长不足时补空字符串。"""
        header = ["a", "b", "c"]
        rows = [["1"]]  # 行只有 1 列
        _new_header, new_rows = select_columns(rows, header, ["a", "c"])
        assert new_rows == [["1", ""]]


# ============================================================================ #
# merge_csvs
# ============================================================================ #
class TestMergeCsvs:
    """merge_csvs 多文件合并测试。"""

    def test_union_basic(self, tmp_path: Path) -> None:
        """union 模式：列并集，缺失填空。"""
        a = tmp_path / "a.csv"
        _write_csv_bytes(a, "name,age\nAlice,30\n")
        b = tmp_path / "b.csv"
        _write_csv_bytes(b, "name,city\nBob,LA\n")
        header, rows = merge_csvs([a, b], mode="union")
        assert header == ["name", "age", "city"]
        assert rows == [["Alice", "30", ""], ["Bob", "", "LA"]]

    def test_union_default_mode(self, tmp_path: Path) -> None:
        """默认模式为 union。"""
        a = tmp_path / "a.csv"
        _write_csv_bytes(a, "x,y\n1,2\n")
        b = tmp_path / "b.csv"
        _write_csv_bytes(b, "y,z\n3,4\n")
        header, rows = merge_csvs([a, b])
        assert header == ["x", "y", "z"]
        assert rows == [["1", "2", ""], ["", "3", "4"]]

    def test_union_preserves_first_occurrence_order(self, tmp_path: Path) -> None:
        """union 保持列首次出现顺序。"""
        a = tmp_path / "a.csv"
        _write_csv_bytes(a, "b,a\n2,1\n")
        b = tmp_path / "b.csv"
        _write_csv_bytes(b, "c,b\n3,2\n")
        header, _ = merge_csvs([a, b], mode="union")
        # 第一个 CSV 中 b 在 a 前，c 仅第二个 CSV 有
        assert header == ["b", "a", "c"]

    def test_intersection_basic(self, tmp_path: Path) -> None:
        """intersection 模式：列交集，按第一个 CSV 顺序。"""
        a = tmp_path / "a.csv"
        _write_csv_bytes(a, "name,age,city\nAlice,30,NYC\n")
        b = tmp_path / "b.csv"
        _write_csv_bytes(b, "city,name,phone\nLA,Bob,123\n")
        header, rows = merge_csvs([a, b], mode="intersection")
        # 交集为 {name, city}，按第一个 CSV 顺序：name, city
        assert header == ["name", "city"]
        assert rows == [["Alice", "NYC"], ["Bob", "LA"]]

    def test_intersection_no_common(self, tmp_path: Path) -> None:
        """intersection 无公共列时表头为空。"""
        a = tmp_path / "a.csv"
        _write_csv_bytes(a, "x\n1\n")
        b = tmp_path / "b.csv"
        _write_csv_bytes(b, "y\n2\n")
        header, rows = merge_csvs([a, b], mode="intersection")
        assert header == []
        assert rows == [[], []]

    def test_intersection_first_csv_empty(self, tmp_path: Path) -> None:
        """intersection 第一个 CSV 为空文件时表头为空。"""
        a = tmp_path / "a.csv"
        _write_csv_bytes(a, "")  # 空文件
        b = tmp_path / "b.csv"
        _write_csv_bytes(b, "x\n1\n")
        header, rows = merge_csvs([a, b], mode="intersection")
        assert header == []
        # 第二个 CSV 的 1 行数据保留，但列被清空
        assert rows == [[]]

    def test_intersection_preserves_first_order(self, tmp_path: Path) -> None:
        """intersection 顺序以第一个 CSV 为准。"""
        a = tmp_path / "a.csv"
        _write_csv_bytes(a, "c,b,a\n3,2,1\n")
        b = tmp_path / "b.csv"
        _write_csv_bytes(b, "a,c\n1,3\n")
        header, _ = merge_csvs([a, b], mode="intersection")
        # 交集为 {c, a}（b 不在第二个 CSV 中），按第一个 CSV 顺序 c, a
        assert header == ["c", "a"]

    def test_too_few_files(self, tmp_path: Path) -> None:
        """少于 2 个文件抛 ValueError。"""
        a = tmp_path / "a.csv"
        _write_csv_bytes(a, "x\n1\n")
        with pytest.raises(ValueError, match="合并至少需要 2 个 CSV 文件"):
            merge_csvs([a])
        with pytest.raises(ValueError, match="合并至少需要 2 个 CSV 文件"):
            merge_csvs([])

    def test_invalid_mode(self, tmp_path: Path) -> None:
        """无效 mode 抛 ValueError。"""
        a = tmp_path / "a.csv"
        _write_csv_bytes(a, "x\n1\n")
        b = tmp_path / "b.csv"
        _write_csv_bytes(b, "y\n2\n")
        with pytest.raises(ValueError, match="不支持的合并模式"):
            merge_csvs([a, b], mode="concat")

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """任一文件不存在抛 FileNotFoundError。"""
        a = tmp_path / "a.csv"
        _write_csv_bytes(a, "x\n1\n")
        with pytest.raises(FileNotFoundError, match="文件不存在"):
            merge_csvs([a, tmp_path / "no.csv"])

    def test_empty_csv(self, tmp_path: Path) -> None:
        """空 CSV 处理：表头为空列表，行不参与。"""
        a = tmp_path / "a.csv"
        _write_csv_bytes(a, "x\n1\n")
        b = tmp_path / "b.csv"
        _write_csv_bytes(b, "")  # 空文件
        header, rows = merge_csvs([a, b], mode="union")
        assert header == ["x"]
        assert rows == [["1"]]

    def test_ragged_row_padded(self, tmp_path: Path) -> None:
        """行长不足补空字符串。"""
        a = tmp_path / "a.csv"
        _write_csv_bytes(a, "a,b,c\n1,2,3\n1,2\n")  # 第二行只有 2 列
        b = tmp_path / "b.csv"
        _write_csv_bytes(b, "a,b,c\n4,5,6\n")
        header, rows = merge_csvs([a, b], mode="union")
        assert header == ["a", "b", "c"]
        assert rows == [["1", "2", "3"], ["1", "2", ""], ["4", "5", "6"]]

    def test_three_files_union(self, tmp_path: Path) -> None:
        """三个文件 union 合并。"""
        a = tmp_path / "a.csv"
        _write_csv_bytes(a, "a\n1\n")
        b = tmp_path / "b.csv"
        _write_csv_bytes(b, "b\n2\n")
        c = tmp_path / "c.csv"
        _write_csv_bytes(c, "c\n3\n")
        header, rows = merge_csvs([a, b, c], mode="union")
        assert header == ["a", "b", "c"]
        assert rows == [["1", "", ""], ["", "2", ""], ["", "", "3"]]


# ============================================================================ #
# format_table
# ============================================================================ #
class TestFormatTable:
    """format_table 表格格式化测试。"""

    def test_with_header(self) -> None:
        """带表头输出。"""
        result = format_table(["name", "age"], [["Alice", "30"], ["Bob", "25"]])
        lines = result.splitlines()
        assert "name" in lines[0]
        assert "Alice" in lines[2]
        assert "Bob" in lines[3]
        # 第二行是分隔线
        assert set(lines[1]) <= {"-", " "}

    def test_no_header(self) -> None:
        """无表头输出。"""
        result = format_table(None, [["1", "2"], ["3", "4"]])
        lines = result.splitlines()
        assert "1" in lines[0]
        assert "3" in lines[1]
        assert len(lines) == 2

    def test_empty(self) -> None:
        """空数据返回（空）。"""
        assert format_table(None, []) == "（空）"

    def test_column_truncation(self) -> None:
        """列宽超过 max_width 截断。"""
        long_text = "x" * 50
        result = format_table(["h"], [[long_text]], max_width=10)
        # 截断后含省略号
        assert "..." in result

    def test_ragged_rows(self) -> None:
        """不齐行补齐。"""
        result = format_table(["a", "b", "c"], [["1", "2"]])
        # 不抛异常即可
        assert "1" in result


# ============================================================================ #
# CLI 子命令端到端
# ============================================================================ #
class TestCLISubcommands:
    """CLI 子命令端到端测试。"""

    def test_show_default_rows(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """show 默认 5 行。"""
        path = tmp_path / "a.csv"
        _write_csv_bytes(path, "name,age\n" + "\n".join(f"u{i},{i}" for i in range(10)) + "\n")
        code = run_tool("csvtool", ["show", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "u0" in out
        assert "共 10 行" in out

    def test_show_custom_rows(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """show --rows N。"""
        path = tmp_path / "a.csv"
        _write_csv_bytes(path, "name,age\n" + "\n".join(f"u{i},{i}" for i in range(10)) + "\n")
        code = run_tool("csvtool", ["show", str(path), "--rows", "3"])
        assert code == 0
        out = capsys.readouterr().out
        assert "显示前 3 行" in out

    def test_show_no_header(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """show --no-header。"""
        path = tmp_path / "a.csv"
        _write_csv_bytes(path, "a,b\n1,2\n3,4\n")
        code = run_tool("csvtool", ["show", str(path), "--no-header"])
        assert code == 0
        out = capsys.readouterr().out
        # 无表头时不输出分隔线
        assert "a" in out  # 作为数据行

    def test_show_nonexistent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """show 文件不存在提示。"""
        code = run_tool("csvtool", ["show", str(tmp_path / "no.csv")])
        assert code == 0
        assert "文件不存在" in capsys.readouterr().out

    def test_to_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """to-json 转换并打印。"""
        path = tmp_path / "a.csv"
        _write_csv_bytes(path, "name,age\nAlice,30\n")
        code = run_tool("csvtool", ["to-json", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        # 框架打印前缀/后缀，提取 JSON 数组部分验证
        assert '"name": "Alice"' in out
        assert '"age": "30"' in out
        # 提取 [ ... ] 部分并解析
        start = out.index("[")
        end = out.rindex("]") + 1
        data = json.loads(out[start:end])
        assert data == [{"name": "Alice", "age": "30"}]

    def test_to_json_nonexistent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """to-json 文件不存在提示。"""
        code = run_tool("csvtool", ["to-json", str(tmp_path / "no.csv")])
        assert code == 0
        assert "文件不存在" in capsys.readouterr().out

    def test_from_json_default_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """from-json 默认输出同名 csv。"""
        path = tmp_path / "a.json"
        _write_json(path, [{"name": "Alice", "age": 30}])
        code = run_tool("csvtool", ["from-json", str(path)])
        assert code == 0
        out_csv = tmp_path / "a.csv"
        assert out_csv.exists()
        text = out_csv.read_text(encoding="utf-8")
        assert "name,age" in text
        assert "Alice,30" in text
        assert "转换完成" in capsys.readouterr().out

    def test_from_json_with_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """from-json --output 指定输出。"""
        path = tmp_path / "a.json"
        _write_json(path, [{"x": "1"}])
        out_csv = tmp_path / "out.csv"
        code = run_tool("csvtool", ["from-json", str(path), "--output", str(out_csv)])
        assert code == 0
        assert "1" in out_csv.read_text(encoding="utf-8")

    def test_from_json_invalid(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """from-json 顶层非数组提示。"""
        path = tmp_path / "a.json"
        _write_json(path, {"k": "v"})
        code = run_tool("csvtool", ["from-json", str(path)])
        assert code == 0
        assert "顶层必须是数组" in capsys.readouterr().out

    def test_select_print(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """select 默认打印到标准输出。"""
        path = tmp_path / "a.csv"
        _write_csv_bytes(path, "name,age,city\nAlice,30,NYC\nBob,25,LA\n")
        code = run_tool("csvtool", ["select", str(path), "name", "city"])
        assert code == 0
        out = capsys.readouterr().out
        assert "name" in out
        assert "Alice" in out
        assert "NYC" in out
        # age 列被排除
        assert "30" not in out

    def test_select_to_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """select --output 输出到文件。"""
        path = tmp_path / "a.csv"
        _write_csv_bytes(path, "a,b,c\n1,2,3\n4,5,6\n")
        out_csv = tmp_path / "out.csv"
        code = run_tool("csvtool", ["select", str(path), "c", "a", "--output", str(out_csv)])
        assert code == 0
        text = out_csv.read_text(encoding="utf-8")
        assert "c,a" in text
        assert "3,1" in text
        assert "6,4" in text
        assert "筛选完成" in capsys.readouterr().out

    def test_select_missing_column(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """select 列名不存在提示。"""
        path = tmp_path / "a.csv"
        _write_csv_bytes(path, "a,b\n1,2\n")
        code = run_tool("csvtool", ["select", str(path), "x"])
        assert code == 0
        assert "列不存在" in capsys.readouterr().out

    def test_select_empty_csv(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """select 空文件提示。"""
        path = tmp_path / "empty.csv"
        _write_csv_bytes(path, "")
        code = run_tool("csvtool", ["select", str(path), "x"])
        assert code == 0
        assert "CSV 文件为空" in capsys.readouterr().out

    def test_select_nonexistent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """select 文件不存在提示。"""
        code = run_tool("csvtool", ["select", str(tmp_path / "no.csv"), "x"])
        assert code == 0
        assert "文件不存在" in capsys.readouterr().out

    def test_merge_union_print(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """merge union 默认打印到标准输出。"""
        a = tmp_path / "a.csv"
        _write_csv_bytes(a, "name,age\nAlice,30\n")
        b = tmp_path / "b.csv"
        _write_csv_bytes(b, "name,city\nBob,LA\n")
        code = run_tool("csvtool", ["merge", str(a), str(b)])
        assert code == 0
        out = capsys.readouterr().out
        assert "name" in out
        assert "Alice" in out
        assert "Bob" in out
        assert "LA" in out

    def test_merge_intersection_print(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """merge --mode intersection 打印交集。"""
        a = tmp_path / "a.csv"
        _write_csv_bytes(a, "name,age,city\nAlice,30,NYC\n")
        b = tmp_path / "b.csv"
        _write_csv_bytes(b, "city,name\nLA,Bob\n")
        code = run_tool("csvtool", ["merge", str(a), str(b), "--mode", "intersection"])
        assert code == 0
        out = capsys.readouterr().out
        assert "name" in out
        assert "city" in out
        # age 列被排除
        assert "age" not in out
        assert "30" not in out

    def test_merge_to_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """merge --output 输出到文件。"""
        a = tmp_path / "a.csv"
        _write_csv_bytes(a, "x\n1\n")
        b = tmp_path / "b.csv"
        _write_csv_bytes(b, "y\n2\n")
        out_csv = tmp_path / "out.csv"
        code = run_tool("csvtool", ["merge", str(a), str(b), "--output", str(out_csv)])
        assert code == 0
        text = out_csv.read_text(encoding="utf-8")
        assert "x,y" in text
        assert "1," in text
        assert ",2" in text
        assert "合并完成" in capsys.readouterr().out

    def test_merge_too_few_files(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """merge 仅 1 个文件提示错误。"""
        a = tmp_path / "a.csv"
        _write_csv_bytes(a, "x\n1\n")
        code = run_tool("csvtool", ["merge", str(a)])
        assert code == 0
        assert "合并至少需要 2" in capsys.readouterr().out

    def test_merge_nonexistent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """merge 文件不存在提示。"""
        a = tmp_path / "a.csv"
        _write_csv_bytes(a, "x\n1\n")
        code = run_tool("csvtool", ["merge", str(a), str(tmp_path / "no.csv")])
        assert code == 0
        assert "文件不存在" in capsys.readouterr().out
