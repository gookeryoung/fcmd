"""llmenv - 大模型工具的路径、配置、状态文件与客户端环境变量。

独立命令：``llmenv <show|add|remove|reset|list|env>``。

目录约定（``FCMD_HOME`` 默认 ``~/.fcmd``；``FCMD_SERVE_DIR`` / ``FCMD_CONFIG`` /
``FCMD_ENGINES`` 可分别覆盖服务目录、配置文件与引擎根目录）::

    ${FCMD_HOME}/serve/<name>.json        服务状态（pid / 端口 / 模型 / 引擎）
    ${FCMD_HOME}/serve/logs/<name>.log    引擎日志（追加写）
    ${FCMD_HOME}/serve/config.json        持久化配置（模型目录列表）
    ${FCMD_HOME}/engines/llama.cpp/<tag>/ 引擎目录（含 llama-server）

模型目录解析优先级：显式 ``--models-root`` → ``FCMD_MODELS`` → ``config.json`` 的
``models_roots``（可多个）→ Windows 下存在的 ``D:/models`` → ``${FCMD_HOME}/models``。
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import fcmd
from fcmd.console import Table, get_console
from fcmd.tools.llm.llmbase import DEFAULT_NAME, ServeError, ServeState, validate_name

__all__ = [
    "add_models_root",
    "client_env",
    "config_path",
    "configured_roots",
    "default_models_root",
    "delete_state",
    "engines_root",
    "fcmd_home",
    "home_dir",
    "list_states",
    "load_config",
    "load_state",
    "log_path",
    "models_roots",
    "path_key",
    "remove_models_root",
    "require_state",
    "reset_models_roots",
    "save_config",
    "save_state",
    "serve_dir",
    "state_path",
]


def fcmd_home() -> Path:
    """``FCMD_HOME``（默认 ``~/.fcmd``）。"""
    raw = os.environ.get("FCMD_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".fcmd"


def home_dir(home: Path | None = None) -> Path:
    """显式 ``home`` 优先，否则回落到 :func:`fcmd_home`。"""
    return home if home is not None else fcmd_home()


def _env_override(name: str) -> Path | None:
    """读取目录覆盖环境变量（空值视为未设置）。"""
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else None


def serve_dir(home: Path | None = None) -> Path:
    """服务状态目录（``FCMD_SERVE_DIR`` 可覆盖）。"""
    if home is None:
        override = _env_override("FCMD_SERVE_DIR")
        if override is not None:
            return override
    return home_dir(home) / "serve"


def state_path(name: str, home: Path | None = None) -> Path:
    """状态文件路径。"""
    return serve_dir(home) / f"{validate_name(name)}.json"


def log_path(name: str, home: Path | None = None) -> Path:
    """日志文件路径。"""
    return serve_dir(home) / "logs" / f"{validate_name(name)}.log"


def engines_root(home: Path | None = None) -> Path:
    """llama.cpp 引擎根目录（``FCMD_ENGINES`` 可覆盖）。"""
    if home is None:
        override = _env_override("FCMD_ENGINES")
        if override is not None:
            return override
    return home_dir(home) / "engines" / "llama.cpp"


def default_models_root() -> Path:
    """兜底模型根目录：Windows 的 ``D:/models``，否则 ``${FCMD_HOME}/models``。"""
    if os.name == "nt" and Path("D:/").exists():
        return Path("D:/models")
    return fcmd_home() / "models"


def config_path(home: Path | None = None) -> Path:
    """持久化配置文件路径（``FCMD_CONFIG`` 可覆盖，默认 ``${FCMD_HOME}/serve/config.json``）。"""
    if home is None:
        override = _env_override("FCMD_CONFIG")
        if override is not None:
            return override
    return serve_dir(home) / "config.json"


def load_config(home: Path | None = None) -> dict[str, Any]:
    """读取配置；缺失或损坏时返回空字典。"""
    path = config_path(home)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: dict[str, Any], home: Path | None = None) -> Path:
    """写入配置文件（自动创建目录）。"""
    path = config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def path_key(path: Path) -> str:
    """用于去重的路径键（Windows 大小写不敏感）。"""
    return os.path.normcase(str(Path(path).resolve()))


def configured_roots(home: Path | None = None) -> list[Path]:
    """配置文件中登记的模型目录（保持登记顺序并去重）。"""
    raw = load_config(home).get("models_roots")
    if not isinstance(raw, list):
        return []
    roots: list[Path] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        path = Path(item.strip()).expanduser()
        key = path_key(path)
        if key in seen:
            continue
        seen.add(key)
        roots.append(path)
    return roots


def models_roots(explicit: Path | None = None, home: Path | None = None) -> list[Path]:
    """解析实际使用的模型目录列表。

    优先级：``explicit``（``--models-root``）→ ``FCMD_MODELS`` → 配置文件 → 默认目录。
    """
    if explicit is not None:
        return [explicit.expanduser()]
    raw = os.environ.get("FCMD_MODELS")
    if raw:
        return [Path(raw).expanduser()]
    roots = configured_roots(home)
    return roots or [default_models_root()]


def _save_roots(roots: Sequence[Path], home: Path | None) -> list[Path]:
    save_config({"models_roots": [str(p) for p in roots]}, home)
    return list(roots)


def add_models_root(path: Path, home: Path | None = None) -> list[Path]:
    """新增一个模型目录并落盘（重复登记忽略）。"""
    roots = configured_roots(home)
    key = path_key(path)
    if any(path_key(item) == key for item in roots):
        return roots
    return _save_roots([*roots, path.expanduser()], home)


def remove_models_root(path: Path, home: Path | None = None) -> list[Path]:
    """移除一个已登记的模型目录。"""
    roots = configured_roots(home)
    key = path_key(path)
    remaining = [item for item in roots if path_key(item) != key]
    if len(remaining) == len(roots):
        raise ServeError(f"配置中没有模型目录: {path}")
    return _save_roots(remaining, home)


def reset_models_roots(home: Path | None = None) -> list[Path]:
    """清空配置中的模型目录，回到默认解析。"""
    path = config_path(home)
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    return [default_models_root()]


def save_state(state: ServeState, home: Path | None = None) -> Path:
    """写入状态文件（自动创建目录）。"""
    path = state_path(state.name, home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_state(name: str, home: Path | None = None, *, missing_ok: bool = False) -> ServeState | None:
    """读取状态文件；缺失时按 ``missing_ok`` 返回 ``None`` 或抛错。"""
    path = state_path(name, home)
    if not path.is_file():
        if missing_ok:
            return None
        raise ServeError(f"服务 {name!r} 未启动（状态文件不存在: {path}）")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ServeState(
            name=str(data["name"]),
            pid=int(data["pid"]),
            create_time=float(data["create_time"]),
            host=str(data["host"]),
            port=int(data["port"]),
            model=str(data["model"]),
            profile=str(data["profile"]),
            context=int(data["context"]),
            engine=str(data["engine"]),
            alias=str(data["alias"]),
            api_key=str(data["api_key"]),
            log=str(data["log"]),
            started_at=str(data["started_at"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ServeError(f"状态文件损坏: {path}（{exc}）") from exc


def require_state(name: str, home: Path | None = None) -> ServeState:
    """读取状态文件；不存在时抛 :class:`ServeError`。"""
    state = load_state(name, home)
    if state is None:  # pragma: no cover - missing_ok=False 时不会返回 None
        raise ServeError(f"服务 {name!r} 未启动")
    return state


def delete_state(name: str, home: Path | None = None) -> bool:
    """删除状态文件，返回是否实际删除。"""
    path = state_path(name, home)
    if not path.is_file():
        return False
    path.unlink()
    return True


def list_states(home: Path | None = None) -> list[ServeState]:
    """列出全部可解析的服务状态（损坏文件跳过）。"""
    directory = serve_dir(home)
    if not directory.is_dir():
        return []
    states: list[ServeState] = []
    for path in sorted(directory.glob("*.json")):
        try:
            state = load_state(path.stem, home)
        except ServeError:
            continue
        if state is not None:
            states.append(state)
    return states


def client_env(state: ServeState) -> dict[str, str]:
    """dsh / pi 等 OpenAI 兼容客户端需要的环境变量。"""
    return {
        "OPENAI_BASE_URL": state.base_url + "/v1",
        "OPENAI_API_KEY": state.api_key,
        "OPENAI_MODEL": state.alias,
    }


_ENV_SHELL = Literal["plain", "powershell", "bash"]


@fcmd.tool("llmenv", subcommand="show", help="显示目录布局与当前生效的模型目录")
def show_cmd() -> None:
    """打印 ``FCMD_HOME`` 目录布局、模型目录与已登记服务数量。"""
    table = Table(title="llmenv 目录布局", box="ascii")
    table.add_column("项目", style="cyan", no_wrap=True)
    table.add_column("路径")
    table.add_row("FCMD_HOME", str(fcmd_home()))
    table.add_row("服务状态", str(serve_dir()))
    table.add_row("配置文件", str(config_path()))
    table.add_row("引擎根目录", str(engines_root()))
    table.add_row("模型目录", ", ".join(str(path) for path in models_roots()))
    table.add_row("已登记服务", str(len(list_states())))
    get_console().print(table)


@fcmd.tool("llmenv", subcommand="add", help="登记一个模型目录")
def add_cmd(path: Path) -> None:
    """新增模型目录并落盘（重复登记忽略）。

    Parameters
    ----------
    path:
        要登记的模型目录。
    """
    add_models_root(path)
    get_console().print(f"已登记模型目录: {path.expanduser()}")
    if not path.expanduser().is_dir():
        get_console().print(f"[yellow]提示: 目录当前不存在 {path.expanduser()}[/yellow]")
    show_cmd()


@fcmd.tool("llmenv", subcommand="remove", help="移除已登记的模型目录")
def remove_cmd(path: Path) -> None:
    """移除模型目录并落盘。

    Parameters
    ----------
    path:
        要移除的模型目录。
    """
    remove_models_root(path)
    get_console().print(f"已移除模型目录: {path.expanduser()}")
    show_cmd()


@fcmd.tool("llmenv", subcommand="reset", help="清空模型目录配置，回到默认目录")
def reset_cmd() -> None:
    """清空配置文件中的模型目录列表。"""
    reset_models_roots()
    get_console().print("已清空模型目录配置，回到默认目录")
    show_cmd()


@fcmd.tool("llmenv", subcommand="list", help="列出已登记的服务状态")
def list_cmd() -> None:
    """列出全部可解析的服务状态文件。"""
    states = list_states()
    if not states:
        get_console().print("没有已登记的服务：fcmd llmserve start")
        return
    table = Table(title="已登记服务", box="ascii")
    table.add_column("服务", style="cyan", no_wrap=True)
    table.add_column("地址", no_wrap=True)
    table.add_column("档位", no_wrap=True)
    table.add_column("PID", justify="right", no_wrap=True)
    table.add_column("启动时间", no_wrap=True)
    for state in states:
        table.add_row(
            state.name, state.base_url, f"{state.profile}/ctx{state.context}", str(state.pid), state.started_at
        )
    get_console().print(table)


@fcmd.tool("llmenv", subcommand="env", help="输出服务的 OPENAI_* 环境变量")
def env_cmd(name: str = DEFAULT_NAME, shell: _ENV_SHELL = "plain") -> None:
    """按指定 shell 语法输出客户端环境变量。

    Parameters
    ----------
    name:
        服务名，默认 ``qwen``。
    shell:
        输出格式：``plain``（``KEY=VALUE``）/ ``powershell`` / ``bash``。
    """
    state = require_state(name)
    for key, value in client_env(state).items():
        if shell == "powershell":
            get_console().print(f'$env:{key}="{value}"', markup=False)
        elif shell == "bash":
            get_console().print(f'export {key}="{value}"', markup=False)
        else:
            get_console().print(f"{key}={value}", markup=False)


@fcmd.main("llmenv")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行
