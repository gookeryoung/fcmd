"""yamtool 工具测试。

验证 ``fcmd.cli.yamtool`` 模块：
- 工具注册与四子命令结构（pretty/get/keys/validate）
- ``read_yaml``/``write_yaml`` 基础读写
- ``pretty_yaml`` 格式化（含 sort_keys/indent）
- ``get_yaml`` 点路径查询（含错误分支）
- ``keys_yaml`` 顶层键列举
- ``validate_yaml`` 语法校验
- CLI 子命令端到端
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-not-found]

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.yamtool import (
    get_yaml,
    keys_yaml,
    pretty_yaml,
    read_yaml,
    validate_yaml,
    write_yaml,
)


# ============================================================================ #
# 辅助函数
# ============================================================================ #
def _write_yaml_file(path: Path, data: Any) -> None:
    """写入 YAML 文件。"""
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False)


_SAMPLE_YAML = """\
name: fcmd
version: 1.0
database:
  host: localhost
  port: 5432
  tags:
    - primary
    - replica
items:
  - {id: 1, label: apple}
  - {id: 2, label: banana}
"""


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """yamtool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "yamtool" in list_tools()

    def test_subcommands(self) -> None:
        """yamtool 有 pretty/get/keys/validate 四个子命令。"""
        subs = list_subcommands("yamtool")
        assert set(subs) == {"pretty", "get", "keys", "validate"}


# ============================================================================ #
# read_yaml / write_yaml
# ============================================================================ #
class TestReadYaml:
    """read_yaml 读取测试。"""

    def test_basic(self, tmp_path: Path) -> None:
        """基本读取。"""
        path = tmp_path / "a.yaml"
        _write_yaml_file(path, {"name": "fcmd", "version": 1.0})
        data = read_yaml(path)
        assert data == {"name": "fcmd", "version": 1.0}

    def test_unicode(self, tmp_path: Path) -> None:
        """Unicode 内容。"""
        path = tmp_path / "u.yaml"
        _write_yaml_file(path, {"name": "你好"})
        data = read_yaml(path)
        assert data == {"name": "你好"}

    def test_nonexistent(self, tmp_path: Path) -> None:
        """不存在抛 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError, match="文件不存在"):
            read_yaml(tmp_path / "no.yaml")

    def test_parse_error(self, tmp_path: Path) -> None:
        """YAML 语法错误抛 yaml.YAMLError。"""
        path = tmp_path / "broken.yaml"
        path.write_text("name: fcmd\n  bad: indent\n", encoding="utf-8")
        with pytest.raises(yaml.YAMLError):
            read_yaml(path)

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        """空文件返回 None。"""
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        assert read_yaml(path) is None


class TestWriteYaml:
    """write_yaml 写入测试。"""

    def test_basic(self, tmp_path: Path) -> None:
        """写入后可重新读取。"""
        path = tmp_path / "out.yaml"
        data = {"name": "fcmd", "list": [1, 2, 3]}
        write_yaml(path, data)
        loaded = read_yaml(path)
        assert loaded == data

    def test_unicode_preserved(self, tmp_path: Path) -> None:
        """Unicode 内容不被转义。"""
        path = tmp_path / "u.yaml"
        write_yaml(path, {"name": "你好"})
        text = path.read_text(encoding="utf-8")
        assert "你好" in text

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        """自动创建父目录。"""
        path = tmp_path / "sub" / "deep" / "out.yaml"
        write_yaml(path, {"k": "v"})
        assert path.exists()

    def test_sort_keys(self, tmp_path: Path) -> None:
        """sort_keys=True 时键按字典序排列。"""
        path = tmp_path / "sorted.yaml"
        write_yaml(path, {"b": 1, "a": 2, "c": 3}, sort_keys=True)
        text = path.read_text(encoding="utf-8")
        # a 应出现在 b 之前
        assert text.index("a:") < text.index("b:")


# ============================================================================ #
# pretty_yaml
# ============================================================================ #
class TestPrettyYaml:
    """pretty_yaml 格式化测试。"""

    def test_basic(self) -> None:
        """基本格式化。"""
        out = pretty_yaml({"name": "fcmd", "version": 1})
        assert "name: fcmd" in out
        assert "version: 1" in out

    def test_sort_keys(self) -> None:
        """sort_keys=True 时键按字典序排列。"""
        out = pretty_yaml({"b": 1, "a": 2}, sort_keys=True)
        assert out.index("a:") < out.index("b:")

    def test_preserve_insertion_order_by_default(self) -> None:
        """默认保持插入顺序。"""
        out = pretty_yaml({"b": 1, "a": 2})
        assert out.index("b:") < out.index("a:")

    def test_unicode(self) -> None:
        """Unicode 不转义。"""
        out = pretty_yaml({"name": "你好"})
        assert "你好" in out

    def test_list(self) -> None:
        """列表以 block 风格输出。"""
        out = pretty_yaml({"items": [1, 2, 3]})
        assert "- 1" in out
        assert "- 2" in out
        assert "- 3" in out


# ============================================================================ #
# get_yaml
# ============================================================================ #
class TestGetYaml:
    """get_yaml 点路径查询测试。"""

    def test_dict_access(self) -> None:
        """字典访问。"""
        data = {"a": {"b": {"c": 42}}}
        assert get_yaml(data, "a.b.c") == 42

    def test_list_index(self) -> None:
        """列表索引。"""
        data = {"items": ["x", "y", "z"]}
        assert get_yaml(data, "items.1") == "y"

    def test_nested_list_in_dict(self) -> None:
        """嵌套列表与字典混合。"""
        data = {"a": {"b": [{"c": 1}, {"c": 2}]}}
        assert get_yaml(data, "a.b.0.c") == 1
        assert get_yaml(data, "a.b.1.c") == 2

    def test_empty_path_returns_root(self) -> None:
        """空路径返回原对象。"""
        data = {"a": 1}
        assert get_yaml(data, "") is data

    def test_missing_key_raises(self) -> None:
        """键不存在抛 KeyError。"""
        with pytest.raises(KeyError, match="键不存在"):
            get_yaml({"a": 1}, "b")

    def test_index_out_of_range_raises(self) -> None:
        """列表索引越界抛 IndexError。"""
        with pytest.raises(IndexError, match="列表索引越界"):
            get_yaml({"items": [1]}, "items.5")

    def test_non_int_index_raises_type_error(self) -> None:
        """非整数列表索引抛 TypeError。"""
        with pytest.raises(TypeError, match="列表索引必须是整数"):
            get_yaml({"items": [1]}, "items.abc")

    def test_invalid_path_raises_value_error(self) -> None:
        """空段路径抛 ValueError。"""
        with pytest.raises(ValueError, match="路径格式错误"):
            get_yaml({"a": 1}, "a..b")

    def test_non_container_raises_type_error(self) -> None:
        """对非容器取子项抛 TypeError。"""
        with pytest.raises(TypeError, match="非容器类型"):
            get_yaml(42, "a")


# ============================================================================ #
# keys_yaml
# ============================================================================ #
class TestKeysYaml:
    """keys_yaml 顶层键列举测试。"""

    def test_dict_keys(self) -> None:
        """字典返回键列表。"""
        data = {"b": 1, "a": 2, "c": 3}
        assert keys_yaml(data) == ["b", "a", "c"]

    def test_list_indices(self) -> None:
        """列表返回索引字符串列表。"""
        data = ["x", "y", "z"]
        assert keys_yaml(data) == ["0", "1", "2"]

    def test_empty_dict(self) -> None:
        """空字典返回空列表。"""
        assert keys_yaml({}) == []

    def test_scalar_raises(self) -> None:
        """标量抛 TypeError。"""
        with pytest.raises(TypeError, match="非容器类型"):
            keys_yaml(42)


# ============================================================================ #
# validate_yaml
# ============================================================================ #
class TestValidateYaml:
    """validate_yaml 语法校验测试。"""

    def test_valid_yaml_passes(self, tmp_path: Path) -> None:
        """合法 YAML 通过。"""
        path = tmp_path / "ok.yaml"
        path.write_text(_SAMPLE_YAML, encoding="utf-8")
        validate_yaml(path)  # 无异常即通过

    def test_nonexistent_raises(self, tmp_path: Path) -> None:
        """文件不存在抛 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError, match="文件不存在"):
            validate_yaml(tmp_path / "no.yaml")

    def test_broken_yaml_raises(self, tmp_path: Path) -> None:
        """非良构 YAML 抛 yaml.YAMLError。"""
        path = tmp_path / "broken.yaml"
        path.write_text("name: fcmd\n  bad: indent\n", encoding="utf-8")
        with pytest.raises(yaml.YAMLError):
            validate_yaml(path)

    def test_empty_yaml_passes(self, tmp_path: Path) -> None:
        """空文件视为 None，良构通过。"""
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        validate_yaml(path)


# ============================================================================ #
# CLI 子命令测试
# ============================================================================ #
class TestYamtoolCLI:
    """``yamtool`` 通过 ``run_tool`` 调用测试。"""

    def test_pretty_via_run_tool(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd yamtool pretty <file> 打印格式化 YAML。"""
        path = tmp_path / "in.yaml"
        _write_yaml_file(path, {"name": "fcmd"})
        code = run_tool("yamtool", ["pretty", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "name: fcmd" in out

    def test_pretty_sort_keys(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--sort-keys 排序键。"""
        path = tmp_path / "in.yaml"
        _write_yaml_file(path, {"b": 1, "a": 2})
        code = run_tool("yamtool", ["pretty", str(path), "--sort-keys"])
        assert code == 0
        out = capsys.readouterr().out
        assert out.index("a:") < out.index("b:")

    def test_pretty_nonexistent_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """文件不存在打印错误。"""
        code = run_tool("yamtool", ["pretty", str(tmp_path / "no.yaml")])
        assert code == 0
        out = capsys.readouterr().out
        assert "文件不存在" in out

    def test_pretty_broken_yaml(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """非良构 YAML 打印解析错误。"""
        path = tmp_path / "broken.yaml"
        path.write_text("name: fcmd\n  bad: indent\n", encoding="utf-8")
        code = run_tool("yamtool", ["pretty", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "YAML 解析失败" in out

    def test_get_dict_value(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd yamtool get <file> a.b 打印字典值。"""
        path = tmp_path / "in.yaml"
        _write_yaml_file(path, {"a": {"b": 42}})
        code = run_tool("yamtool", ["get", str(path), "a.b"])
        assert code == 0
        out = capsys.readouterr().out
        assert "42" in out

    def test_get_list_index(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd yamtool get <file> items.0 打印列表元素。"""
        path = tmp_path / "in.yaml"
        _write_yaml_file(path, {"items": ["apple", "banana"]})
        code = run_tool("yamtool", ["get", str(path), "items.0"])
        assert code == 0
        out = capsys.readouterr().out
        assert "apple" in out

    def test_get_dict_value_prints_yaml(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """get 返回容器时打印 YAML 格式。"""
        path = tmp_path / "in.yaml"
        _write_yaml_file(path, {"a": {"b": {"c": 1}}})
        code = run_tool("yamtool", ["get", str(path), "a.b"])
        assert code == 0
        out = capsys.readouterr().out
        assert "c: 1" in out

    def test_get_missing_key(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """键不存在打印错误。"""
        path = tmp_path / "in.yaml"
        _write_yaml_file(path, {"a": 1})
        code = run_tool("yamtool", ["get", str(path), "b"])
        assert code == 0
        out = capsys.readouterr().out
        assert "键不存在" in out

    def test_get_nonexistent_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """文件不存在打印错误。"""
        code = run_tool("yamtool", ["get", str(tmp_path / "no.yaml"), "a"])
        assert code == 0
        out = capsys.readouterr().out
        assert "文件不存在" in out

    def test_keys_dict(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd yamtool keys <file> 列出字典键。"""
        path = tmp_path / "in.yaml"
        _write_yaml_file(path, {"b": 1, "a": 2})
        code = run_tool("yamtool", ["keys", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line and not line.startswith(">") and not line.startswith("OK")]
        assert "a" in lines
        assert "b" in lines

    def test_keys_list(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd yamtool keys <file> 对列表返回索引字符串。"""
        path = tmp_path / "in.yaml"
        _write_yaml_file(path, ["x", "y"])
        code = run_tool("yamtool", ["keys", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line and not line.startswith(">") and not line.startswith("OK")]
        assert "0" in lines
        assert "1" in lines

    def test_keys_nonexistent_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """文件不存在打印错误。"""
        code = run_tool("yamtool", ["keys", str(tmp_path / "no.yaml")])
        assert code == 0
        out = capsys.readouterr().out
        assert "文件不存在" in out

    def test_keys_scalar_value(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """YAML 顶层为标量时打印错误。"""
        path = tmp_path / "scalar.yaml"
        path.write_text("just a string\n", encoding="utf-8")
        code = run_tool("yamtool", ["keys", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "非容器类型" in out

    def test_validate_valid_yaml(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """合法 YAML 校验通过。"""
        path = tmp_path / "ok.yaml"
        path.write_text(_SAMPLE_YAML, encoding="utf-8")
        code = run_tool("yamtool", ["validate", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "语法校验通过" in out

    def test_validate_nonexistent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """文件不存在打印错误。"""
        code = run_tool("yamtool", ["validate", str(tmp_path / "no.yaml")])
        assert code == 0
        out = capsys.readouterr().out
        assert "文件不存在" in out

    def test_validate_broken_yaml(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """非良构 YAML 打印失败。"""
        path = tmp_path / "broken.yaml"
        path.write_text("name: fcmd\n  bad: indent\n", encoding="utf-8")
        code = run_tool("yamtool", ["validate", str(path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "语法校验失败" in out
