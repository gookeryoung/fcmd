"""_llmconfig - localllm 工具族配置的唯一权威来源。

所有 localllm 应用（llmenv / llmdown / llmmodel / llmengine / llmconvert /
llmqnt / llmserve / llmdoctor / locllmw 等）都从这里解析目录与配置，模块内
不再保留私有默认值。

配置文件为 ``${FCMD_HOME:-~/.fcmd}/serve/config.json``（``FCMD_CONFIG`` 可
覆盖），权威键::

    models_dir    str        唯一的模型目录
    work_dir      str        下载 / 转换等工作目录（默认模型目录）
    engines_root  str        llama.cpp 引擎根目录
    serve_dir     str        服务状态与日志目录

模型目录只支持单一目录；设置页与所有 CLI 读取点都走 :func:`models_dir`。

优先级（配置文件路径本身除外，避免自引用）::

    显式参数 > 环境变量 > config.json > 内置默认

    模型目录：--models-root → FCMD_MODELS → models_dir → 默认目录
    工作目录：显式 dest    → FCMD_WORK   → work_dir   → 模型目录
    引擎目录：               FCMD_ENGINES → engines_root → ${home}/engines/llama.cpp
    服务目录：               FCMD_SERVE_DIR → serve_dir  → ${home}/serve

旧配置一次性迁移后写回；旧键仍可读入，但此后只写 ``models_dir``：

* config.json 旧键：``models_roots``（此前的列表）/ ``models_root`` /
  ``model_roots`` / ``models`` → ``models_dir``。列表只保留第一个作为
  模型目录，其余路径写入 ``models_dir_extra`` 迁移记录（不会被扫描），
  不再静默丢弃。
* config.json 其他旧键：``download_dir`` / ``workdir`` / ``work_root`` →
  ``work_dir``；``engines`` / ``engine_dir`` / ``llama_cpp_dir`` →
  ``engines_root``；``serve`` → ``serve_dir``。
* ``ui/llmenvw.json`` 的 ``serve_dir`` / ``engines_root`` 并入 config.json，
  随后把该文件改名为 ``.migrated``（保留数据，不再读取）。
* ``ui/locllmw.json`` 的 ``download.dest`` 若是绝对路径则迁移为 ``work_dir``
  并移除该键；相对路径（旧默认 ``models``）不迁移，回落到统一默认。
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

__all__ = [
    "config_path",
    "configured_models_dir",
    "default_models_dir",
    "engines_root",
    "fcmd_home",
    "get_option",
    "home_dir",
    "load_config",
    "models_dir",
    "models_dir_extra",
    "path_key",
    "save_config",
    "serve_dir",
    "set_models_dir",
    "set_option",
    "work_dir",
]

_MODELS_KEYS = ("models_roots", "models_root", "model_roots", "models")
_WORK_KEYS = ("download_dir", "workdir", "work_root")
_ENGINE_KEYS = ("engines", "engine_dir", "llama_cpp_dir")
_SERVE_KEYS = ("serve",)


# ---------------------------------------------------------------------- #
# 基础路径
# ---------------------------------------------------------------------- #
def fcmd_home() -> Path:
    """``FCMD_HOME``（默认 ``~/.fcmd``）。"""
    raw = os.environ.get("FCMD_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".fcmd"


def home_dir(home: Path | None = None) -> Path:
    """显式 ``home`` 优先，否则回落到 :func:`fcmd_home`。"""
    return home if home is not None else fcmd_home()


def _env_path(name: str) -> Path | None:
    """读取目录覆盖环境变量（空值视为未设置）。"""
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else None


def config_path(home: Path | None = None) -> Path:
    """配置文件路径（``FCMD_CONFIG`` 可覆盖，默认 ``${home}/serve/config.json``）。"""
    if home is None:
        override = _env_path("FCMD_CONFIG")
        if override is not None:
            return override
    return home_dir(home) / "serve" / "config.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    """读取 JSON 对象；缺失、损坏或非对象时返回 ``None``。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """写入 JSON 对象（自动创建父目录）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------- #
# 旧配置迁移
# ---------------------------------------------------------------------- #
def _first_text(raw: dict[str, Any], keys: Sequence[str]) -> str | None:
    """取第一个非空字符串旧键。"""
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _legacy_models(raw: dict[str, Any]) -> tuple[str | None, list[str]]:
    """把旧模型键规整为 ``(主目录, 多余目录)``；字符串或字符串列表均可。"""
    for key in _MODELS_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), []
        if isinstance(value, list):
            items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
            if items:
                return items[0], items[1:]
    return None, []


def _migrate_legacy_file(raw: dict[str, Any], home: Path | None) -> bool:
    """把 ``ui/llmenvw.json`` 的目录覆盖并入配置，并把旧文件改名。"""
    legacy = home_dir(home) / "ui" / "llmenvw.json"
    if not legacy.is_file():
        return False
    data = _read_json(legacy) or {}
    changed = False
    for source, target in (("serve_dir", "serve_dir"), ("engines_root", "engines_root")):
        value = data.get(source)
        if isinstance(value, str) and value.strip() and not raw.get(target):
            raw[target] = value.strip()
            changed = True
    with contextlib.suppress(OSError):
        legacy.replace(legacy.with_name(legacy.name + ".migrated"))
    return changed


def _migrate_download_dest(raw: dict[str, Any], home: Path | None) -> bool:
    """把 locllmw.json 的旧 ``download.dest``（仅绝对路径）迁为 ``work_dir``。"""
    legacy = home_dir(home) / "ui" / "locllmw.json"
    data = _read_json(legacy)
    if data is None:
        return False
    download = data.get("download")
    if not isinstance(download, dict) or "dest" not in download:
        return False
    dest = download.pop("dest", None)
    changed = True
    if isinstance(dest, str) and dest.strip():
        path = Path(dest.strip()).expanduser()
        if path.is_absolute() and not raw.get("work_dir"):
            raw["work_dir"] = str(path)
    with contextlib.suppress(OSError):
        _write_json(legacy, data)
    return changed


def _migrate(raw: dict[str, Any], home: Path | None) -> bool:
    """迁移旧键；返回是否发生了需要写回的变化。"""
    changed = False
    if not (isinstance(raw.get("models_dir"), str) and raw["models_dir"].strip()):
        primary, extras = _legacy_models(raw)
        if primary:
            raw["models_dir"] = primary
            changed = True
            if extras:
                raw["models_dir_extra"] = extras
    for keys, target in (
        (_WORK_KEYS, "work_dir"),
        (_ENGINE_KEYS, "engines_root"),
        (_SERVE_KEYS, "serve_dir"),
    ):
        if raw.get(target):
            continue
        value = _first_text(raw, keys)
        if value:
            raw[target] = value
            changed = True
    for key in (*_MODELS_KEYS, *_WORK_KEYS, *_ENGINE_KEYS, *_SERVE_KEYS):
        if key in raw:
            del raw[key]
            changed = True
    changed = _migrate_legacy_file(raw, home) or changed
    changed = _migrate_download_dest(raw, home) or changed
    return changed


# ---------------------------------------------------------------------- #
# 配置读写
# ---------------------------------------------------------------------- #
def load_config(home: Path | None = None) -> dict[str, Any]:
    """读取配置并完成旧键迁移；缺失或损坏时返回空字典。"""
    raw = _read_json(config_path(home)) or {}
    if _migrate(raw, home):
        with contextlib.suppress(OSError):
            _write_json(config_path(home), raw)
    return raw


def save_config(data: dict[str, Any], home: Path | None = None) -> Path:
    """整体写入配置文件（调用方需自行保留其他键）。"""
    path = config_path(home)
    _write_json(path, data)
    return path


def get_option(key: str, home: Path | None = None) -> Any:
    """读取单个权威键；不存在时返回 ``None``。"""
    return load_config(home).get(key)


def set_option(key: str, value: Any, home: Path | None = None) -> dict[str, Any]:
    """写入或删除（``value is None``）单个键，保留其他配置。"""
    data = load_config(home)
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    save_config(data, home)
    return data


# ---------------------------------------------------------------------- #
# 模型目录 / 工作目录
# ---------------------------------------------------------------------- #
def default_models_dir() -> Path:
    """兜底模型目录：Windows 的 ``D:/models``，否则 ``${FCMD_HOME}/models``。"""
    if os.name == "nt" and Path("D:/").exists():
        return Path("D:/models")
    return fcmd_home() / "models"


def path_key(path: Path) -> str:
    """用于比较的路径键（Windows 大小写不敏感）。"""
    return os.path.normcase(str(Path(path).resolve()))


def configured_models_dir(home: Path | None = None) -> Path | None:
    """配置文件中登记的模型目录；未登记时返回 ``None``。"""
    raw = load_config(home).get("models_dir")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return Path(raw.strip()).expanduser()


def models_dir_extra(home: Path | None = None) -> list[str]:
    """旧多值配置合并时记录的其余模型目录（仅提示，不参与扫描）。"""
    raw = load_config(home).get("models_dir_extra")
    if not isinstance(raw, list):
        return []
    return [item.strip() for item in raw if isinstance(item, str) and item.strip()]


def models_dir(explicit: Path | None = None, home: Path | None = None) -> Path:
    """解析唯一模型目录（显式 → FCMD_MODELS → 配置 → 默认）。"""
    if explicit is not None:
        return explicit.expanduser()
    raw = os.environ.get("FCMD_MODELS")
    if raw:
        return Path(raw).expanduser()
    return configured_models_dir(home) or default_models_dir()


def work_dir(explicit: Path | None = None, home: Path | None = None) -> Path:
    """解析工作目录（显式 → FCMD_WORK → 配置 → 模型目录）。"""
    if explicit is not None:
        return explicit.expanduser()
    raw = os.environ.get("FCMD_WORK")
    if raw:
        return Path(raw).expanduser()
    configured = load_config(home).get("work_dir")
    if isinstance(configured, str) and configured.strip():
        return Path(configured.strip()).expanduser()
    return models_dir(home=home)


def set_models_dir(path: Path | None, home: Path | None = None) -> Path:
    """设置唯一模型目录；``None`` 清除配置回到默认，并保留其他配置键。"""
    data = load_config(home)
    data.pop("models_dir_extra", None)
    if path is None:
        data.pop("models_dir", None)
    else:
        data["models_dir"] = str(path.expanduser())
    save_config(data, home)
    return models_dir(home=home)


# ---------------------------------------------------------------------- #
# 引擎 / 服务目录
# ---------------------------------------------------------------------- #
def engines_root(home: Path | None = None) -> Path:
    """llama.cpp 引擎根目录（FCMD_ENGINES → 配置 → 默认）。"""
    if home is None:
        override = _env_path("FCMD_ENGINES")
        if override is not None:
            return override
    configured = load_config(home).get("engines_root")
    if isinstance(configured, str) and configured.strip():
        return Path(configured.strip()).expanduser()
    return home_dir(home) / "engines" / "llama.cpp"


def serve_dir(home: Path | None = None) -> Path:
    """服务状态目录（FCMD_SERVE_DIR → 配置 → 默认）。"""
    if home is None:
        override = _env_path("FCMD_SERVE_DIR")
        if override is not None:
            return override
    configured = load_config(home).get("serve_dir")
    if isinstance(configured, str) and configured.strip():
        return Path(configured.strip()).expanduser()
    return home_dir(home) / "serve"
