"""inifile - INI 文件处理工具。

基于标准库 ``configparser`` 提供 INI 文件的读取、查询、修改与列举。

示例
----
    fcmd inifile get config.ini database host         # 取值
    fcmd inifile set config.ini database port 5432     # 设值并写回
    fcmd inifile list config.ini                        # 列出所有 section
    fcmd inifile keys config.ini database               # 列出 section 的 key
"""

from __future__ import annotations

import configparser
from pathlib import Path

import fcmd
from fcmd.console import get_console

__all__ = [
    "get_ini_value",
    "list_ini_keys",
    "list_ini_sections",
    "read_ini",
    "set_ini_value",
]


# ============================================================================
# 公共函数
# ============================================================================


def read_ini(filepath: Path) -> configparser.ConfigParser:
    """读取 INI 文件并解析为 ``ConfigParser`` 对象。

    Parameters
    ----------
    filepath:
        INI 文件路径

    Returns
    -------
    configparser.ConfigParser
        解析后的配置对象

    Raises
    ------
    FileNotFoundError
        文件不存在
    configparser.Error
        INI 语法错误
    """
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    config = configparser.ConfigParser()
    config.read(filepath, encoding="utf-8")
    return config


def get_ini_value(filepath: Path, section: str, key: str) -> str:
    """获取 INI 文件中指定 ``section.key`` 的值。

    Parameters
    ----------
    filepath:
        INI 文件路径
    section:
        section 名
    key:
        key 名

    Returns
    -------
    str
        对应的值

    Raises
    ------
    FileNotFoundError
        文件不存在
    KeyError
        section 不存在
    configparser.NoOptionError
        key 不存在
    """
    config = read_ini(filepath)
    if not config.has_section(section):
        raise KeyError(f"section {section!r} 不存在")
    if not config.has_option(section, key):
        raise configparser.NoOptionError(section, key)
    return config.get(section, key)


def set_ini_value(filepath: Path, section: str, key: str, value: str) -> None:
    """设置 INI 文件中指定 ``section.key`` 的值并写回文件。

    section 不存在时自动创建。

    Parameters
    ----------
    filepath:
        INI 文件路径
    section:
        section 名
    key:
        key 名
    value:
        要设置的值

    Raises
    ------
    FileNotFoundError
        文件不存在
    """
    config = read_ini(filepath)
    if not config.has_section(section):
        config.add_section(section)
    config.set(section, key, value)
    with filepath.open("w", encoding="utf-8") as f:
        config.write(f)


def list_ini_sections(filepath: Path) -> list[str]:
    """列出 INI 文件的所有 section（不含 DEFAULT）。

    Parameters
    ----------
    filepath:
        INI 文件路径

    Returns
    -------
    list[str]
        section 名列表

    Raises
    ------
    FileNotFoundError
        文件不存在
    """
    config = read_ini(filepath)
    return config.sections()


def list_ini_keys(filepath: Path, section: str) -> list[str]:
    """列出 INI 文件指定 section 的所有 key（不含 DEFAULT 继承的键）。

    Parameters
    ----------
    filepath:
        INI 文件路径
    section:
        section 名

    Returns
    -------
    list[str]
        key 名列表

    Raises
    ------
    FileNotFoundError
        文件不存在
    KeyError
        section 不存在
    """
    config = read_ini(filepath)
    if not config.has_section(section):
        raise KeyError(f"section {section!r} 不存在")
    # config.options(section) 包含 DEFAULT 继承的键，用 config[section] 取原始键
    return list(config[section].keys())


# ============================================================================
# CLI 子命令
# ============================================================================


def _print_error(exc: Exception) -> None:
    """统一错误输出格式。"""
    get_console().print(f"[red]错误:[/red] {exc}")


@fcmd.tool("inifile", subcommand="get", help="获取指定 section.key 的值")
def ini_get_cmd(file: Path, section: str, key: str) -> None:
    """获取 INI 文件中 ``section.key`` 的值并打印。

    Parameters
    ----------
    file:
        INI 文件路径
    section:
        section 名
    key:
        key 名
    """
    try:
        value = get_ini_value(file, section, key)
    except (FileNotFoundError, KeyError, configparser.NoOptionError, configparser.Error) as exc:
        _print_error(exc)
        return
    print(value)


@fcmd.tool("inifile", subcommand="set", help="设置 section.key 的值并写回文件")
def ini_set_cmd(file: Path, section: str, key: str, value: str) -> None:
    """设置 INI 文件中 ``section.key`` 的值并写回文件。

    section 不存在时自动创建。

    Parameters
    ----------
    file:
        INI 文件路径
    section:
        section 名
    key:
        key 名
    value:
        要设置的值
    """
    try:
        set_ini_value(file, section, key, value)
    except (FileNotFoundError, configparser.Error) as exc:
        _print_error(exc)
        return
    get_console().print(f"[green]已设置[/green] [{section}] {key} = {value}")


@fcmd.tool("inifile", subcommand="list", help="列出所有 section")
def ini_list_cmd(file: Path) -> None:
    """列出 INI 文件的所有 section。

    Parameters
    ----------
    file:
        INI 文件路径
    """
    try:
        sections = list_ini_sections(file)
    except (FileNotFoundError, configparser.Error) as exc:
        _print_error(exc)
        return
    if not sections:
        get_console().print("[dim]无 section[/dim]")
        return
    for section in sections:
        print(section)


@fcmd.tool("inifile", subcommand="keys", help="列出 section 的所有 key")
def ini_keys_cmd(file: Path, section: str) -> None:
    """列出 INI 文件指定 section 的所有 key。

    Parameters
    ----------
    file:
        INI 文件路径
    section:
        section 名
    """
    try:
        keys = list_ini_keys(file, section)
    except (FileNotFoundError, KeyError, configparser.Error) as exc:
        _print_error(exc)
        return
    if not keys:
        get_console().print(f"[dim]section {section!r} 无 key[/dim]")
        return
    for key in keys:
        print(key)


@fcmd.main("inifile")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
