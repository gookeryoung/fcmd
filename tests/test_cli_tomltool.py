"""tomltool 工具测试：公共函数 + CLI 子命令。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apis.toolkit import list_subcommands, list_tools, run_tool
from fcmd.cli.tomltool import (
    _TOMLI_AVAILABLE,
    _TOMLLIB_AVAILABLE,
    format_toml,
    get_toml_value,
    keys_toml,
    read_toml,
    validate_toml,
)

# 模块导入即注册工具（@fcmd.tool 装饰器在导入时执行）

# 跳过条件：tomllib 与 tomli 均不可用时跳过所有测试
_TOML_AVAILABLE = _TOMLLIB_AVAILABLE or _TOMLI_AVAILABLE
skip_no_toml = pytest.mark.skipif(not _TOML_AVAILABLE, reason="需要 tomllib(3.11+) 或 tomli")


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
    assert set(subs) == {"get", "keys", "format", "validate"}


# ---------------------------------------------------------------------- #
# read_toml
# ---------------------------------------------------------------------- #
@skip_no_toml
def test_read_toml_success(toml_file: Path) -> None:
    """正常读取 TOML 文件。"""
    data = read_toml(toml_file)
    assert data["project"]["name"] == "myapp"
    assert data["project"]["version"] == "1.0.0"
    assert data["tool"]["ruff"]["line-length"] == 100


@skip_no_toml
def test_read_toml_file_not_found(tmp_path: Path) -> None:
    """文件不存在时抛 FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        read_toml(tmp_path / "nope.toml")


@skip_no_toml
def test_read_toml_invalid_syntax(invalid_toml_file: Path) -> None:
    """语法错误时抛 TOMLDecodeError（ValueError 子类）。"""
    with pytest.raises(ValueError):
        read_toml(invalid_toml_file)


# ---------------------------------------------------------------------- #
# get_toml_value
# ---------------------------------------------------------------------- #
@skip_no_toml
def test_get_toml_value_simple(toml_file: Path) -> None:
    """获取简单键值。"""
    assert get_toml_value(toml_file, "project.name") == "myapp"
    assert get_toml_value(toml_file, "project.version") == "1.0.0"


@skip_no_toml
def test_get_toml_value_nested(toml_file: Path) -> None:
    """获取嵌套键值。"""
    deps = get_toml_value(toml_file, "project.dependencies")
    assert isinstance(deps, dict)
    assert deps["tomli"] == "2.0"


@skip_no_toml
def test_get_toml_value_key_not_found(toml_file: Path) -> None:
    """键不存在时抛 KeyError。"""
    with pytest.raises(KeyError, match="不存在"):
        get_toml_value(toml_file, "project.nope")


@skip_no_toml
def test_get_toml_value_non_dict_path(toml_file: Path) -> None:
    """路径中间非字典时抛 KeyError。"""
    with pytest.raises(KeyError, match="不是字典"):
        get_toml_value(toml_file, "project.name.sub")


# ---------------------------------------------------------------------- #
# keys_toml
# ---------------------------------------------------------------------- #
@skip_no_toml
def test_keys_toml(toml_file: Path) -> None:
    """列出顶层键。"""
    keys = keys_toml(toml_file)
    assert set(keys) == {"project", "tool"}


# ---------------------------------------------------------------------- #
# format_toml
# ---------------------------------------------------------------------- #
@skip_no_toml
def test_format_toml(toml_file: Path) -> None:
    """格式化为 JSON 字符串。"""
    result = format_toml(toml_file)
    data = json.loads(result)
    assert data["project"]["name"] == "myapp"


@skip_no_toml
def test_format_toml_indent(toml_file: Path) -> None:
    """指定缩进。"""
    result = format_toml(toml_file, indent=4)
    assert "    " in result  # 4 空格缩进


# ---------------------------------------------------------------------- #
# validate_toml
# ---------------------------------------------------------------------- #
@skip_no_toml
def test_validate_toml_success(toml_file: Path) -> None:
    """语法正确的文件不抛异常。"""
    validate_toml(toml_file)  # 不抛异常即通过


@skip_no_toml
def test_validate_toml_invalid(invalid_toml_file: Path) -> None:
    """语法错误时抛 ValueError。"""
    with pytest.raises(ValueError):
        validate_toml(invalid_toml_file)


# ---------------------------------------------------------------------- #
# CLI 子命令测试
# ---------------------------------------------------------------------- #
@skip_no_toml
def test_cli_get(toml_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI get 子命令取值。"""
    code = run_tool("tomltool", ["get", str(toml_file), "project.name"])
    captured = capsys.readouterr()
    assert code == 0
    assert "myapp" in captured.out


@skip_no_toml
def test_cli_get_nested(toml_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI get 嵌套路径返回 JSON。"""
    code = run_tool("tomltool", ["get", str(toml_file), "project.dependencies"])
    captured = capsys.readouterr()
    assert code == 0
    assert "tomli" in captured.out


@skip_no_toml
def test_cli_get_key_not_found(toml_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI get 键不存在时打印错误。"""
    code = run_tool("tomltool", ["get", str(toml_file), "project.nope"])
    captured = capsys.readouterr()
    assert code == 0
    assert "不存在" in captured.out


@skip_no_toml
def test_cli_keys(toml_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI keys 子命令列出顶层键。"""
    code = run_tool("tomltool", ["keys", str(toml_file)])
    captured = capsys.readouterr()
    assert code == 0
    assert "project" in captured.out
    assert "tool" in captured.out


@skip_no_toml
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


@skip_no_toml
def test_cli_validate(toml_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI validate 子命令校验通过。"""
    code = run_tool("tomltool", ["validate", str(toml_file)])
    captured = capsys.readouterr()
    assert code == 0
    assert "通过" in captured.out


@skip_no_toml
def test_cli_validate_invalid(invalid_toml_file: Path) -> None:
    """CLI validate 语法错误时打印错误。"""
    code = run_tool("tomltool", ["validate", str(invalid_toml_file)])
    assert code == 0


@skip_no_toml
def test_cli_file_not_found(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI 文件不存在时打印错误。"""
    code = run_tool("tomltool", ["get", str(tmp_path / "nope.toml"), "key"])
    captured = capsys.readouterr()
    assert code == 0
    assert "文件不存在" in captured.out
