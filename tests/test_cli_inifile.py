"""inifile 工具测试：公共函数 + CLI 子命令。"""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

from fcmd.apis.toolkit import list_subcommands, list_tools, run_tool
from fcmd.cli.data.inifile import (
    get_ini_value,
    list_ini_keys,
    list_ini_sections,
    read_ini,
    set_ini_value,
)

# 模块导入即注册工具（@fcmd.tool 装饰器在导入时执行）


@pytest.fixture()
def ini_file(tmp_path: Path) -> Path:
    """创建临时 INI 文件，含 database 与 app 两个 section。"""
    path = tmp_path / "config.ini"
    content = "[database]\nhost = localhost\nport = 5432\n\n[app]\nname = myapp\ndebug = true\n"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture()
def empty_ini_file(tmp_path: Path) -> Path:
    """创建空的 INI 文件（无 section）。"""
    path = tmp_path / "empty.ini"
    path.write_text("", encoding="utf-8")
    return path


# ---------------------------------------------------------------------- #
# 工具注册
# ---------------------------------------------------------------------- #
def test_tool_registered() -> None:
    """inifile 工具已注册。"""
    assert "inifile" in list_tools()
    subs = list_subcommands("inifile")
    assert set(subs) == {"get", "set", "list", "keys"}


# ---------------------------------------------------------------------- #
# read_ini
# ---------------------------------------------------------------------- #
def test_read_ini_success(ini_file: Path) -> None:
    """正常读取 INI 文件。"""
    config = read_ini(ini_file)
    assert config.has_section("database")
    assert config.get("database", "host") == "localhost"
    assert config.get("app", "name") == "myapp"


def test_read_ini_file_not_found(tmp_path: Path) -> None:
    """文件不存在时抛 FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        read_ini(tmp_path / "nope.ini")


# ---------------------------------------------------------------------- #
# get_ini_value
# ---------------------------------------------------------------------- #
def test_get_ini_value_success(ini_file: Path) -> None:
    """正常取值。"""
    assert get_ini_value(ini_file, "database", "host") == "localhost"
    assert get_ini_value(ini_file, "database", "port") == "5432"
    assert get_ini_value(ini_file, "app", "name") == "myapp"


def test_get_ini_value_section_not_found(ini_file: Path) -> None:
    """section 不存在时抛 KeyError。"""
    with pytest.raises(KeyError, match="不存在"):
        get_ini_value(ini_file, "nope", "key")


def test_get_ini_value_key_not_found(ini_file: Path) -> None:
    """key 不存在时抛 NoOptionError。"""
    with pytest.raises(configparser.NoOptionError):
        get_ini_value(ini_file, "database", "nope")


def test_get_ini_value_file_not_found(tmp_path: Path) -> None:
    """文件不存在时抛 FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        get_ini_value(tmp_path / "nope.ini", "db", "host")


# ---------------------------------------------------------------------- #
# set_ini_value
# ---------------------------------------------------------------------- #
def test_set_ini_value_existing_section(ini_file: Path) -> None:
    """修改已有 section 的值并写回。"""
    set_ini_value(ini_file, "database", "port", "3306")
    # 重新读取验证
    assert get_ini_value(ini_file, "database", "port") == "3306"


def test_set_ini_value_new_section(ini_file: Path) -> None:
    """section 不存在时自动创建。"""
    set_ini_value(ini_file, "cache", "ttl", "3600")
    assert get_ini_value(ini_file, "cache", "ttl") == "3600"


def test_set_ini_value_new_key(ini_file: Path) -> None:
    """在已有 section 中新增 key。"""
    set_ini_value(ini_file, "database", "user", "admin")
    assert get_ini_value(ini_file, "database", "user") == "admin"


def test_set_ini_value_file_not_found(tmp_path: Path) -> None:
    """文件不存在时抛 FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        set_ini_value(tmp_path / "nope.ini", "db", "host", "value")


# ---------------------------------------------------------------------- #
# list_ini_sections
# ---------------------------------------------------------------------- #
def test_list_ini_sections(ini_file: Path) -> None:
    """列出所有 section（按文件中的出现顺序）。"""
    sections = list_ini_sections(ini_file)
    assert sections == ["database", "app"]  # configparser 保留插入序


def test_list_ini_sections_empty(empty_ini_file: Path) -> None:
    """空文件返回空列表。"""
    assert list_ini_sections(empty_ini_file) == []


# ---------------------------------------------------------------------- #
# list_ini_keys
# ---------------------------------------------------------------------- #
def test_list_ini_keys(ini_file: Path) -> None:
    """列出 section 的所有 key。"""
    keys = list_ini_keys(ini_file, "database")
    assert set(keys) == {"host", "port"}


def test_list_ini_keys_section_not_found(ini_file: Path) -> None:
    """section 不存在时抛 KeyError。"""
    with pytest.raises(KeyError, match="不存在"):
        list_ini_keys(ini_file, "nope")


# ---------------------------------------------------------------------- #
# CLI 子命令测试
# ---------------------------------------------------------------------- #
def _run(args: list[str]) -> tuple[int, str]:
    """运行 inifile 子命令，返回 (退出码, stdout)。"""
    code = run_tool("inifile", args)
    return code, ""


def test_cli_get(ini_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI get 子命令取值。"""
    code = run_tool("inifile", ["get", str(ini_file), "database", "host"])
    captured = capsys.readouterr()
    assert code == 0
    assert "localhost" in captured.out


def test_cli_get_section_not_found(ini_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI get section 不存在时打印错误（现有工具模式：捕获异常后 return，退出码 0）。"""
    code = run_tool("inifile", ["get", str(ini_file), "nope", "key"])
    captured = capsys.readouterr()
    assert code == 0  # 现有工具模式：捕获异常后 return，framework 返回 0
    assert "不存在" in captured.out


def test_cli_set(ini_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI set 子命令设值并写回。"""
    code = run_tool("inifile", ["set", str(ini_file), "database", "port", "3306"])
    assert code == 0
    # 验证写回成功
    assert get_ini_value(ini_file, "database", "port") == "3306"


def test_cli_set_new_section(ini_file: Path) -> None:
    """CLI set 自动创建新 section。"""
    code = run_tool("inifile", ["set", str(ini_file), "cache", "ttl", "3600"])
    assert code == 0
    assert get_ini_value(ini_file, "cache", "ttl") == "3600"


def test_cli_list(ini_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI list 子命令列出 section。"""
    code = run_tool("inifile", ["list", str(ini_file)])
    captured = capsys.readouterr()
    assert code == 0
    assert "database" in captured.out
    assert "app" in captured.out


def test_cli_list_empty(empty_ini_file: Path) -> None:
    """CLI list 空文件返回成功。"""
    code = run_tool("inifile", ["list", str(empty_ini_file)])
    assert code == 0


def test_cli_keys(ini_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI keys 子命令列出 section 的 key。"""
    code = run_tool("inifile", ["keys", str(ini_file), "database"])
    captured = capsys.readouterr()
    assert code == 0
    assert "host" in captured.out
    assert "port" in captured.out


def test_cli_keys_section_not_found(ini_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI keys section 不存在时打印错误。"""
    code = run_tool("inifile", ["keys", str(ini_file), "nope"])
    captured = capsys.readouterr()
    assert code == 0
    assert "不存在" in captured.out


def test_cli_file_not_found(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI 文件不存在时打印错误。"""
    code = run_tool("inifile", ["get", str(tmp_path / "nope.ini"), "db", "host"])
    captured = capsys.readouterr()
    assert code == 0
    assert "文件不存在" in captured.out
