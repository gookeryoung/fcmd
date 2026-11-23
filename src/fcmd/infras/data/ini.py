"""``fcmd.infras.data.ini``（由 ``infras/data.py`` 拆分）。"""

from __future__ import annotations

import configparser
from pathlib import Path

from .csv import _require_file

__all__ = ["get_ini_value", "list_ini_keys", "list_ini_sections", "read_ini", "set_ini_value"]


def read_ini(filepath: Path) -> configparser.ConfigParser:
    """读取 INI 文件并解析为 ConfigParser 对象。"""
    _require_file(filepath)
    config = configparser.ConfigParser()
    config.read(filepath, encoding="utf-8")
    return config


def get_ini_value(filepath: Path, section: str, key: str) -> str:
    """获取 INI 文件中指定 section.key 的值。"""
    config = read_ini(filepath)
    if not config.has_section(section):
        raise KeyError(f"section {section!r} 不存在")
    if not config.has_option(section, key):
        raise configparser.NoOptionError(section, key)
    return config.get(section, key)


def set_ini_value(filepath: Path, section: str, key: str, value: str) -> None:
    """设置 INI 文件中指定 section.key 的值并写回文件。"""
    config = read_ini(filepath)
    if not config.has_section(section):
        config.add_section(section)
    config.set(section, key, value)
    with filepath.open("w", encoding="utf-8") as f:
        config.write(f)


def list_ini_sections(filepath: Path) -> list[str]:
    """列出 INI 文件的所有 section（不含 DEFAULT）。"""
    return read_ini(filepath).sections()


def list_ini_keys(filepath: Path, section: str) -> list[str]:
    """列出 INI 文件指定 section 的所有 key（不含 DEFAULT 继承的键）。"""
    config = read_ini(filepath)
    if not config.has_section(section):
        raise KeyError(f"section {section!r} 不存在")
    return list(config[section].keys())
