"""tomltool 工具测试：``fcmd.infras.data`` 公共函数 + CLI 子命令。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.core.toolkit import list_subcommands, list_tools, run_tool
from fcmd.infras.data import (
    format_toml,
    get_toml_value,
    keys_toml,
    read_toml,
    to_toml,
    validate_toml,
)
from tests.helpers import strip_framework_lines

# 模块导入即注册工具（@fcmd.tool 装饰器在导入时执行）


@pytest.fixture()
def toml_file(tmp_path: Path) -> Path:
    """创建临时 TOML 文件，含嵌套结构。"""
    path = tmp_path / "config.toml"
    content = (
        "[project]\n"
        'name = "myapp"\n'
        'version = "1.0.0"\n'
        "\n"
        "[project.dependencies]\n"
        'tomli = "2.0"\n'
        'pytest = "8.0"\n'
        "\n"
        "[tool.ruff]\n"
        "line-length = 100\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture()
def invalid_toml_file(tmp_path: Path) -> Path:
    """创建语法错误的 TOML 文件。"""
    path = tmp_path / "bad.toml"
    path.write_text("[project\nname = \n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------- #
# 工具注册
# ---------------------------------------------------------------------- #
def test_tool_registered() -> None:
    """tomltool 工具已注册。"""
    assert "tomltool" in list_tools()
    subs = list_subcommands("tomltool")
    assert set(subs) == {"get", "keys", "format", "convert", "validate"}


# ---------------------------------------------------------------------- #
# read_toml
# ---------------------------------------------------------------------- #
def test_read_toml_success(toml_file: Path) -> None:
    """正常读取 TOML 文件。"""
    data = read_toml(toml_file)
    assert data["project"]["name"] == "myapp"
    assert data["project"]["version"] == "1.0.0"
    assert data["tool"]["ruff"]["line-length"] == 100


def test_read_toml_file_not_found(tmp_path: Path) -> None:
    """文件不存在时抛 FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        read_toml(tmp_path / "nope.toml")


def test_read_toml_invalid_syntax(invalid_toml_file: Path) -> None:
    """语法错误时抛 TOMLDecodeError（ValueError 子类）。"""
    with pytest.raises(ValueError):
        read_toml(invalid_toml_file)


# ---------------------------------------------------------------------- #
# get_toml_value
# ---------------------------------------------------------------------- #
def test_get_toml_value_simple(toml_file: Path) -> None:
    """获取简单键值。"""
    assert get_toml_value(toml_file, "project.name") == "myapp"
    assert get_toml_value(toml_file, "project.version") == "1.0.0"


def test_get_toml_value_nested(toml_file: Path) -> None:
    """获取嵌套键值。"""
    deps = get_toml_value(toml_file, "project.dependencies")
    assert isinstance(deps, dict)
    assert deps["tomli"] == "2.0"


def test_get_toml_value_key_not_found(toml_file: Path) -> None:
    """键不存在时抛 KeyError。"""
    with pytest.raises(KeyError, match="不存在"):
        get_toml_value(toml_file, "project.nope")


def test_get_toml_value_non_dict_path(toml_file: Path) -> None:
    """路径中间非字典时抛 KeyError。"""
    with pytest.raises(KeyError, match="不是字典"):
        get_toml_value(toml_file, "project.name.sub")


# ---------------------------------------------------------------------- #
# keys_toml
# ---------------------------------------------------------------------- #
def test_keys_toml(toml_file: Path) -> None:
    """列出顶层键。"""
    keys = keys_toml(toml_file)
    assert set(keys) == {"project", "tool"}


# ---------------------------------------------------------------------- #
# format_toml
# ---------------------------------------------------------------------- #
def test_format_toml(toml_file: Path) -> None:
    """格式化为 JSON 字符串。"""
    result = format_toml(toml_file)
    data = json.loads(result)
    assert data["project"]["name"] == "myapp"


def test_format_toml_indent(toml_file: Path) -> None:
    """指定缩进。"""
    result = format_toml(toml_file, indent=4)
    assert "    " in result  # 4 空格缩进


# ---------------------------------------------------------------------- #
# validate_toml
# ---------------------------------------------------------------------- #
def test_validate_toml_success(toml_file: Path) -> None:
    """语法正确的文件不抛异常。"""
    validate_toml(toml_file)  # 不抛异常即通过


def test_validate_toml_invalid(invalid_toml_file: Path) -> None:
    """语法错误时抛 ValueError。"""
    with pytest.raises(ValueError):
        validate_toml(invalid_toml_file)


# ---------------------------------------------------------------------- #
# CLI 子命令测试
# ---------------------------------------------------------------------- #
def test_cli_get(toml_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI get 子命令取值。"""
    code = run_tool("tomltool", ["get", str(toml_file), "project.name"])
    captured = capsys.readouterr()
    assert code == 0
    assert "myapp" in captured.out


def test_cli_get_nested(toml_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI get 嵌套路径返回 JSON。"""
    code = run_tool("tomltool", ["get", str(toml_file), "project.dependencies"])
    captured = capsys.readouterr()
    assert code == 0
    assert "tomli" in captured.out


def test_cli_get_key_not_found(toml_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI get 键不存在时打印错误。"""
    code = run_tool("tomltool", ["get", str(toml_file), "project.nope"])
    captured = capsys.readouterr()
    assert code == 0
    assert "不存在" in captured.out


def test_cli_keys(toml_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI keys 子命令列出顶层键。"""
    code = run_tool("tomltool", ["keys", str(toml_file)])
    captured = capsys.readouterr()
    assert code == 0
    assert "project" in captured.out
    assert "tool" in captured.out


def test_cli_format(toml_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI format 子命令输出 JSON。"""
    code = run_tool("tomltool", ["format", str(toml_file)])
    captured = capsys.readouterr()
    assert code == 0
    # 从输出中提取 JSON（过滤 framework 日志行）
    json_start = captured.out.index("{")
    decoder = json.JSONDecoder()
    data, _ = decoder.raw_decode(captured.out[json_start:])
    assert data["project"]["name"] == "myapp"


def test_cli_validate(toml_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI validate 子命令校验通过。"""
    code = run_tool("tomltool", ["validate", str(toml_file)])
    captured = capsys.readouterr()
    assert code == 0
    assert "通过" in captured.out


def test_cli_validate_invalid(invalid_toml_file: Path) -> None:
    """CLI validate 语法错误时打印错误。"""
    code = run_tool("tomltool", ["validate", str(invalid_toml_file)])
    assert code == 0


def test_cli_file_not_found(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI 文件不存在时打印错误。"""
    code = run_tool("tomltool", ["get", str(tmp_path / "nope.toml"), "key"])
    captured = capsys.readouterr()
    assert code == 0
    assert "文件不存在" in captured.out


# ---------------------------------------------------------------------- #
# to_toml（写方向序列化）
# ---------------------------------------------------------------------- #
def test_to_toml_scalars_and_nested() -> None:
    """标量、数组、子表与表数组按 TOML 结构输出。"""
    text = to_toml(
        {
            "title": "demo",
            "enabled": True,
            "count": 3,
            "ratio": 0.5,
            "tags": ["a", "b"],
            "empty": [],
            "owner": {"name": "me", "ids": [1, 2]},
            "items": [{"id": 1}, {"id": 2}],
        }
    )
    assert 'title = "demo"' in text
    assert "enabled = true" in text
    assert "count = 3" in text
    assert "ratio = 0.5" in text
    assert 'tags = ["a", "b"]' in text
    assert "empty = []" in text
    assert "[owner]" in text
    assert 'name = "me"' in text
    assert "[[items]]" in text


def test_to_toml_roundtrip(toml_file: Path) -> None:
    """读入再写出后能被 tomllib 重新解析出等价数据。"""
    from fcmd.infras._toml import loads

    original = read_toml(toml_file)
    assert loads(to_toml(original)) == original


def test_to_toml_quotes_and_escapes() -> None:
    """键名含特殊字符时加引号，字符串转义控制字符。"""
    text = to_toml({"a b": 'quote " backslash \\ newline \n tab \t ctrl \x01', "普通": 1})
    assert '"a b" = ' in text
    assert 'quote \\" backslash \\\\ newline \\n tab \\t ctrl \\u0001' in text
    assert '"\u666e\u901a" = 1' in text


def test_to_toml_special_floats_and_datetime() -> None:
    """nan/inf 与日期时间按 TOML 字面量输出。"""
    from datetime import datetime

    text = to_toml({"nan": float("nan"), "inf": float("inf"), "ninf": float("-inf"), "at": datetime(2024, 1, 2, 3)})
    assert "nan = nan" in text
    assert "inf = inf" in text
    assert "ninf = -inf" in text
    assert "at = 2024-01-02T03:00:00" in text


def test_to_toml_empty_mapping() -> None:
    """空映射输出空串。"""
    assert to_toml({}) == ""


def test_to_toml_rejects_unsupported() -> None:
    """非映射顶层、None 值、非字符串键、未知类型都抛 ValueError。"""
    with pytest.raises(ValueError, match="顶层必须是映射"):
        to_toml([1, 2])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="None"):
        to_toml({"a": None})
    with pytest.raises(ValueError, match="键必须是字符串"):
        to_toml({1: "a"})  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="无法表示的类型"):
        to_toml({"a": object()})


# ---------------------------------------------------------------------- #
# CLI convert
# ---------------------------------------------------------------------- #
def test_cli_convert_json(toml_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """convert --to json 在标准输出给出 JSON。"""
    code = run_tool("tomltool", ["convert", str(toml_file), "--to", "json"])
    out = strip_framework_lines(capsys.readouterr().out)
    assert code == 0
    decoder = json.JSONDecoder()
    data, _ = decoder.raw_decode(out[out.index("{") :])
    assert data["project"]["name"] == "myapp"


def test_cli_convert_yaml(toml_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """convert --to yaml 在标准输出给出 YAML。"""
    code = run_tool("tomltool", ["convert", str(toml_file), "--to", "yaml"])
    out = strip_framework_lines(capsys.readouterr().out)
    assert code == 0
    assert "project:" in out
    assert "name: myapp" in out


def test_cli_convert_to_file(toml_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--output 写到目标文件（父目录自动创建）。"""
    target = tmp_path / "nested" / "out.json"
    code = run_tool("tomltool", ["convert", str(toml_file), "--to", "json", "--output", str(target)])
    captured = capsys.readouterr()
    assert code == 0
    assert "已写入" in captured.out
    assert json.loads(target.read_text(encoding="utf-8"))["tool"]["ruff"]["line-length"] == 100


def test_cli_convert_invalid_format(toml_file: Path) -> None:
    """--to 取值不在枚举内按用法错误退出（2）。"""
    assert run_tool("tomltool", ["convert", str(toml_file), "--to", "xml"]) == 2


def test_cli_convert_file_not_found(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """源文件不存在退出 4。"""
    code = run_tool("tomltool", ["convert", str(tmp_path / "nope.toml"), "--to", "json"])
    captured = capsys.readouterr()
    assert code == 4
    assert "错误" in captured.out + captured.err or "错误" in captured.err


def test_cli_convert_invalid_toml(invalid_toml_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """源文件语法错误退出 3。"""
    code = run_tool("tomltool", ["convert", str(invalid_toml_file), "--to", "json"])
    assert code == 3
    assert "解析失败" in capsys.readouterr().out + ""


def test_cli_convert_missing_pyyaml(
    toml_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """PyYAML 缺失时退出 1 并提示安装。"""
    from fcmd.infras.data import yaml as yaml_infra

    def _boom() -> object:
        raise ImportError("YAML 处理需要 PyYAML: pip install pyyaml")

    monkeypatch.setattr(yaml_infra, "_yaml_module", _boom)
    code = run_tool("tomltool", ["convert", str(toml_file), "--to", "yaml"])
    captured = capsys.readouterr()
    assert code == 1
    assert "PyYAML" in captured.out + captured.err


def test_cli_convert_write_failure(toml_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """目标路径不可写时退出 1。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    code = run_tool("tomltool", ["convert", str(toml_file), "--to", "json", "--output", str(blocker / "out.json")])
    captured = capsys.readouterr()
    assert code == 1
    assert "写入目标文件失败" in captured.out + captured.err
