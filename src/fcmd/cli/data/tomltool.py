"""tomltool - TOML 文件处理工具。

基于标准库 ``tomllib``（Python 3.11+）或第三方 ``tomli``（3.8-3.10 后备）
提供 TOML 文件的读取、点路径查询、键列举、JSON 格式化与语法校验。

依赖策略
--------
优先使用标准库 ``tomllib``（3.11+）；3.8-3.10 用户需自行安装 ``tomli``
（``pip install tomli``）。用 ``importlib.util.find_spec`` 在模块加载时
探测可用性，避免顶层 import 影响工具发现性能（遵循 pdftool 模式）。

示例
----
    fcmd tomltool get pyproject.toml project.name           # 点路径取值
    fcmd tomltool keys pyproject.toml                       # 列出顶层键
    fcmd tomltool format pyproject.toml                     # 转为 JSON 格式化输出
    fcmd tomltool validate pyproject.toml                   # 语法校验
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import fcmd
from fcmd.console import get_console

__all__ = [
    "format_toml",
    "get_toml_value",
    "keys_toml",
    "read_toml",
    "validate_toml",
]

# 模块加载时探测 TOML 库可用性（find_spec 微秒级，不触发实际导入）
_TOMLLIB_AVAILABLE = importlib.util.find_spec("tomllib") is not None
_TOMLI_AVAILABLE = importlib.util.find_spec("tomli") is not None


def _require_toml_loader() -> Any:
    """获取 TOML 解析器模块（tomllib 3.11+ 或 tomli 3.8-3.10）。

    懒导入：仅在首次调用时执行实际 import，避免工具发现阶段加载开销。

    Raises
    ------
    ImportError
        tomllib 与 tomli 均不可用时
    """
    if _TOMLLIB_AVAILABLE:
        import tomllib

        return tomllib
    if _TOMLI_AVAILABLE:
        import tomli as tomllib  # pyrefly: ignore [missing-import]

        return tomllib
    raise ImportError("TOML 解析需要 Python 3.11+ 或安装 tomli: pip install tomli")


# ============================================================================
# 公共函数
# ============================================================================


def read_toml(filepath: Path) -> dict[str, Any]:
    """读取 TOML 文件并解析为字典。

    Parameters
    ----------
    filepath:
        TOML 文件路径

    Returns
    -------
    dict[str, Any]
        解析后的字典

    Raises
    ------
    FileNotFoundError
        文件不存在
    ImportError
        tomllib 与 tomli 均不可用
    tomllib.TOMLDecodeError
        TOML 语法错误
    """
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    toml_mod = _require_toml_loader()
    with filepath.open("rb") as f:
        return toml_mod.load(f)


def get_toml_value(filepath: Path, key: str) -> Any:
    """按点路径获取 TOML 文件中指定键的值。

    Parameters
    ----------
    filepath:
        TOML 文件路径
    key:
        点分路径（如 ``project.name``、``project.dependencies``）

    Returns
    -------
    Any
        对应的值（str/int/float/bool/list/dict/None）

    Raises
    ------
    FileNotFoundError
        文件不存在
    KeyError
        路径中某级键不存在
    """
    data = read_toml(filepath)
    result: Any = data
    for part in key.split("."):
        if not isinstance(result, dict):
            raise KeyError(f"键 {key!r} 不存在：{part!r} 处不是字典")
        if part not in result:
            raise KeyError(f"键 {key!r} 不存在：{part!r} 未找到")
        result = result[part]
    return result


def keys_toml(filepath: Path) -> list[str]:
    """列出 TOML 文件的顶层键。

    Parameters
    ----------
    filepath:
        TOML 文件路径

    Returns
    -------
    list[str]
        顶层键列表

    Raises
    ------
    FileNotFoundError
        文件不存在
    """
    data = read_toml(filepath)
    return list(data.keys())


def format_toml(filepath: Path, indent: int = 2) -> str:
    """将 TOML 文件转为 JSON 格式化字符串。

    Parameters
    ----------
    filepath:
        TOML 文件路径
    indent:
        JSON 缩进空格数（默认 2）

    Returns
    -------
    str
        JSON 格式化字符串

    Raises
    ------
    FileNotFoundError
        文件不存在
    """
    data = read_toml(filepath)
    return json.dumps(data, indent=indent, ensure_ascii=False, default=str)


def validate_toml(filepath: Path) -> None:
    """校验 TOML 文件语法是否正确。

    Parameters
    ----------
    filepath:
        TOML 文件路径

    Raises
    ------
    FileNotFoundError
        文件不存在
    tomllib.TOMLDecodeError
        TOML 语法错误
    """
    read_toml(filepath)


# ============================================================================
# CLI 子命令
# ============================================================================


def _print_error(exc: Exception) -> None:
    """统一错误输出格式。"""
    get_console().print(f"[red]错误:[/red] {exc}")


@fcmd.tool("tomltool", subcommand="get", help="按点路径取值")
def toml_get_cmd(file: Path, key: str) -> None:
    """按点路径查询 TOML 文件并打印结果。

    Parameters
    ----------
    file:
        TOML 文件路径
    key:
        点分路径（如 ``project.name``）
    """
    try:
        result = get_toml_value(file, key)
    except (FileNotFoundError, KeyError, ImportError) as exc:
        _print_error(exc)
        return
    if isinstance(result, (dict, list)):
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print(result)


@fcmd.tool("tomltool", subcommand="keys", help="列出顶层键")
def toml_keys_cmd(file: Path) -> None:
    """列出 TOML 文件的顶层键。

    Parameters
    ----------
    file:
        TOML 文件路径
    """
    try:
        keys = keys_toml(file)
    except (FileNotFoundError, ImportError) as exc:
        _print_error(exc)
        return
    if not keys:
        get_console().print("[dim]无顶层键[/dim]")
        return
    for key in keys:
        print(key)


@fcmd.tool("tomltool", subcommand="format", help="转为 JSON 格式化输出")
def toml_format_cmd(file: Path) -> None:
    """将 TOML 文件转为 JSON 格式化输出。

    Parameters
    ----------
    file:
        TOML 文件路径
    """
    try:
        result = format_toml(file)
    except (FileNotFoundError, ImportError) as exc:
        _print_error(exc)
        return
    print(result)


@fcmd.tool("tomltool", subcommand="validate", help="语法校验")
def toml_validate_cmd(file: Path) -> None:
    """校验 TOML 文件语法是否正确。

    Parameters
    ----------
    file:
        TOML 文件路径
    """
    try:
        validate_toml(file)
    except (FileNotFoundError, ValueError, ImportError) as exc:
        # ValueError 涵盖 tomllib.TOMLDecodeError（继承 ValueError）
        _print_error(exc)
        return
    get_console().print(f"[green]语法校验通过[/green]: {file}")


@fcmd.main("tomltool")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
