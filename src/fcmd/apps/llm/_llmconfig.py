"""_llmconfig - llm 工具族配置的唯一权威来源。

所有 llm 应用（llmenv / llmdown / llmmodel / llmengine / llmconvert /
llmqnt / llmserve / llmdoctor / llmw 等）都从这里解析目录与配置，模块内
不再保留私有默认值。

配置文件为 ``${FCMD_HOME:-~/.fcmd}/serve/config.json``（``FCMD_CONFIG`` 可
覆盖），权威键::

    models_dirs      list[str] 模型目录列表（首个为下载 / 产物落点，即主目录）
    models_dir       str     旧版单目录键，仍可读入；写入时与 models_dirs 保持同步
    engines_root     str     llama.cpp 引擎根目录
    serve_dir        str     服务状态与日志目录
    paint_dir        str     画图工作目录（状态 / 日志 / 出图）
    sd_engines_root  str     stable-diffusion.cpp 引擎根目录

画图 / 视频 / 音频共用同一个 sd.cpp 引擎目录与同一个默认引擎（``current.json``），
不再有按用途区分的引擎 tag。
模型目录支持多个（Steam 式存储管理）：下载与转换产物固定写入列表首个目录，
扫描时遍历全部目录并按真实路径去重；转换等产物写入主目录下的固定子目录
:data:`PRODUCTS_DIRNAME`（``converted``）。设置页与所有 CLI 读取点都走
:func:`models_dirs`（列表）/ :func:`models_dir`（主目录）/ :func:`products_dir`。

优先级（配置文件路径本身除外，避免自引用）::

    显式参数 > 环境变量 > config.json > 内置默认

    模型目录：--models-root → FCMD_MODELS → models_dirs / models_dir → 默认目录
    引擎目录：               FCMD_ENGINES → engines_root → ${home}/engines/llama.cpp
    服务目录：               FCMD_SERVE_DIR → serve_dir  → ${home}/serve
    画图目录：               FCMD_PAINT_DIR → paint_dir  → ${home}/paint
    SD 引擎目录：            FCMD_SD_ENGINES → sd_engines_root → ${home}/engines/sd.cpp

工作目录概念已移除：``work_dir`` 键、``FCMD_WORK`` 环境变量与 ``work_dir``
API 都不再存在。

旧配置一次性迁移后写回；旧键仍可读入，但此后只写 ``models_dirs``：

* config.json 旧键：``models_roots``（此前的列表）/ ``models_root`` /
  ``model_roots`` / ``models`` → ``models_dir``（= ``models_dirs`` 首项）。
  列表其余路径写入 ``models_dir_extra`` 迁移记录，供设置页提示用户手动加回，
  迁移时不再自动加入扫描列表。
* config.json 其他旧键：``engines`` / ``engine_dir`` / ``llama_cpp_dir`` →
  ``engines_root``；``serve`` → ``serve_dir``。
* 旧工作目录键（``work_dir`` / ``download_dir`` / ``workdir`` /
  ``work_root``）与窗口设置（``ui/llmw.json``，改名前的 ``ui/locllmw.json``
  首次访问时搬迁到新名字）的 ``download.dest``：读取时忽略并删除，
  值写入 ``ignored_work_dir`` 迁移记录，供设置页与 ``llmenv show`` 提示一次。
* ``ui/llmenvw.json`` 的 ``serve_dir`` / ``engines_root`` 并入 config.json，
  随后把该文件改名为 ``.migrated``（保留数据，不再读取）。
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fcmd.infras.jsonio import read_json_file, write_json_atomic
from fcmd.infras.system import fcmd_home

__all__ = [
    "LEGACY_UI_SETTINGS_NAME",
    "PRODUCTS_DIRNAME",
    "UI_SETTINGS_NAME",
    "add_models_dir",
    "config_path",
    "configured_models_dir",
    "configured_models_dirs",
    "default_models_dir",
    "engines_root",
    "fcmd_home",
    "get_option",
    "home_dir",
    "ignored_work_dir",
    "load_config",
    "models_dir",
    "models_dir_extra",
    "models_dirs",
    "paint_dir",
    "paint_engines_root",
    "path_key",
    "products_dir",
    "remove_models_dir",
    "save_config",
    "serve_dir",
    "set_models_dir",
    "set_models_dirs",
    "set_option",
    "ui_settings_path",
]

#: 转换等产物的固定子目录名（位于模型目录下）。
PRODUCTS_DIRNAME = "converted"

#: 窗口设置文件名（``${home}/ui/llmw.json``）。
UI_SETTINGS_NAME = "llmw"

#: 改名前的窗口设置文件名；首次访问时复制到新名字，旧文件保留作回退。
LEGACY_UI_SETTINGS_NAME = "locllmw"

_MODELS_KEYS = ("models_roots", "models_root", "model_roots", "models")
_WORK_KEYS = ("work_dir", "download_dir", "workdir", "work_root")
_ENGINE_KEYS = ("engines", "engine_dir", "llama_cpp_dir")
_SERVE_KEYS = ("serve",)


# ---------------------------------------------------------------------- #
# 基础路径
# ---------------------------------------------------------------------- #
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


def ui_settings_path(home: Path | None = None) -> Path:
    """窗口设置路径 ``${home}/ui/llmw.json``，首次访问时搬迁旧文件。

    改名前的 ``ui/locllmw.json`` 若存在且新文件缺失，把主文件与 ``.bak``
    备份移动到新名字并删除旧文件；新文件已存在时不触碰旧文件。
    """
    target = home_dir(home) / "ui" / f"{UI_SETTINGS_NAME}.json"
    legacy = home_dir(home) / "ui" / f"{LEGACY_UI_SETTINGS_NAME}.json"
    if target.is_file() or not legacy.is_file():
        return target
    with contextlib.suppress(OSError):
        target.parent.mkdir(parents=True, exist_ok=True)
        legacy.replace(target)
        legacy_bak = legacy.with_name(legacy.name + ".bak")
        target_bak = target.with_name(target.name + ".bak")
        if legacy_bak.is_file():
            target_bak.unlink(missing_ok=True)
            legacy_bak.replace(target_bak)
    return target


def _read_json(path: Path) -> dict[str, Any] | None:
    """读取 JSON 对象；缺失、损坏或非对象时返回 ``None``。"""
    try:
        data = read_json_file(path)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """写入 JSON 对象（自动创建父目录）；任一写操作都会让配置缓存失效。"""
    _LOAD_CACHE.clear()
    write_json_atomic(path, data)


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
    """移除窗口设置里的旧 ``download.dest``，绝对路径记入迁移提示。"""
    settings = ui_settings_path(home)
    data = _read_json(settings)
    if data is None:
        return False
    download = data.get("download")
    if not isinstance(download, dict) or "dest" not in download:
        return False
    dest = download.pop("dest", None)
    changed = True
    if isinstance(dest, str) and dest.strip():
        path = Path(dest.strip()).expanduser()
        if path.is_absolute() and not raw.get("ignored_work_dir"):
            raw["ignored_work_dir"] = str(path)
    with contextlib.suppress(OSError):
        _write_json(settings, data)
    return changed


def _record_ignored_work(raw: dict[str, Any]) -> bool:
    """把旧工作目录值记入迁移记录后删除；返回值是否发生变化。"""
    value = _first_text(raw, _WORK_KEYS)
    changed = False
    if value and not raw.get("ignored_work_dir"):
        raw["ignored_work_dir"] = value
        changed = True
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
    for keys, target in ((_ENGINE_KEYS, "engines_root"), (_SERVE_KEYS, "serve_dir")):
        if raw.get(target):
            continue
        value = _first_text(raw, keys)
        if value:
            raw[target] = value
            changed = True
    changed = _record_ignored_work(raw) or changed
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
_LOAD_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
"""``load_config`` 的进程内缓存：键为（路径, mtime_ns, size），任何写操作后整体失效。

窗口启动时（llmw / 设置页 / 转换页）会在同一轮里读取配置十余次，缓存把
「重新读盘 + 旧键迁移」压到每次文件指纹变化后的一次，冷启动少掉数十次 I/O。
"""


def _stat_key(path: Path) -> tuple[str, int, int]:
    """文件指纹键；文件不存在时用 ``(-1, -1)`` 表示，仍可命中“空配置”缓存。"""
    try:
        stat = path.stat()
    except OSError:
        return str(path), -1, -1
    return str(path), stat.st_mtime_ns, stat.st_size


def load_config(home: Path | None = None) -> dict[str, Any]:
    """读取配置并完成旧键迁移；缺失或损坏时返回空字典。

    结果按「路径 + mtime_ns + size」缓存在进程内，命中时直接返回副本；写配置
    （:func:`save_config` / :func:`set_option` / 迁移写回）会使缓存失效。
    """
    path = config_path(home)
    cached = _LOAD_CACHE.get(_stat_key(path))
    if cached is not None:
        return dict(cached)
    raw = _read_json(path) or {}
    if _migrate(raw, home):
        with contextlib.suppress(OSError):
            _write_json(path, raw)
    _LOAD_CACHE.clear()
    _LOAD_CACHE[_stat_key(path)] = dict(raw)
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
# 模型目录 / 转换产物目录
# ---------------------------------------------------------------------- #
def default_models_dir() -> Path:
    """兜底模型目录：Windows 的 ``D:/models``，否则 ``${FCMD_HOME}/models``。"""
    if os.name == "nt" and Path("D:/").exists():
        return Path("D:/models")
    return fcmd_home() / "models"


def path_key(path: Path) -> str:
    """用于比较的路径键（Windows 大小写不敏感）。"""
    return os.path.normcase(str(Path(path).resolve()))


def configured_models_dirs(home: Path | None = None) -> list[Path]:
    """配置文件中登记的模型目录列表（按登记顺序，首项为主目录）。

    新键 ``models_dirs`` 优先；旧版单值 ``models_dir`` 回退为单元素列表。
    空串 / 非法项被跳过；未登记时返回空列表。
    """
    raw = load_config(home).get("models_dirs")
    paths: list[Path] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                paths.append(Path(item.strip()).expanduser())
    if paths:
        return _dedupe_paths(paths)
    single = load_config(home).get("models_dir")
    if isinstance(single, str) and single.strip():
        return [Path(single.strip()).expanduser()]
    return []


def configured_models_dir(home: Path | None = None) -> Path | None:
    """配置文件中登记的主模型目录（首个目录）；未登记时返回 ``None``。"""
    dirs = configured_models_dirs(home)
    return dirs[0] if dirs else None


def _dedupe_paths(paths: Sequence[Path]) -> list[Path]:
    """按真实路径去重并保持顺序（Windows 大小写不敏感）。"""
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = path_key(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def models_dirs(explicit: Path | None = None, home: Path | None = None) -> list[Path]:
    """解析模型目录列表（显式 → FCMD_MODELS → 配置 → 默认）。

    ``explicit`` / ``FCMD_MODELS`` 是单目录覆盖，返回单元素列表；
    配置文件可登记多个目录，首个为主目录（下载 / 产物落点）。
    """
    if explicit is not None:
        return [explicit.expanduser()]
    raw = os.environ.get("FCMD_MODELS")
    if raw:
        return [Path(raw).expanduser()]
    configured = configured_models_dirs(home)
    return configured or [default_models_dir()]


def models_dir_extra(home: Path | None = None) -> list[str]:
    """旧多值配置合并时记录的其余模型目录（仅提示，不参与扫描）。"""
    raw = load_config(home).get("models_dir_extra")
    if not isinstance(raw, list):
        return []
    return [item.strip() for item in raw if isinstance(item, str) and item.strip()]


def ignored_work_dir(home: Path | None = None) -> str | None:
    """迁移时忽略的旧工作目录（仅提示一次，不参与任何路径解析）。"""
    raw = load_config(home).get("ignored_work_dir")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw.strip()


def models_dir(explicit: Path | None = None, home: Path | None = None) -> Path:
    """解析主模型目录（列表首项：下载与转换产物落点）。"""
    return models_dirs(explicit, home)[0]


def products_dir(explicit: Path | None = None, home: Path | None = None) -> Path:
    """转换等产物的固定目录：主模型目录下的 ``converted`` 子目录。"""
    return models_dir(explicit, home) / PRODUCTS_DIRNAME


def set_models_dirs(paths: Sequence[Path | str], home: Path | None = None) -> list[Path]:
    """整体写入模型目录列表；首项为主目录，空列表清除配置回到默认。

    同时同步旧键 ``models_dir``（首项），保证仍读取单值键的调用方拿到主目录；
    写入列表时清除旧配置迁移提示。
    """
    resolved = _dedupe_paths([Path(str(item)).expanduser() for item in paths if str(item).strip()])
    data = load_config(home)
    data.pop("models_dir_extra", None)
    data.pop("ignored_work_dir", None)
    if not resolved:
        data.pop("models_dirs", None)
        data.pop("models_dir", None)
    else:
        data["models_dirs"] = [str(path) for path in resolved]
        data["models_dir"] = str(resolved[0])
    save_config(data, home)
    return models_dirs(home=home)


def set_models_dir(path: Path | None, home: Path | None = None) -> Path:
    """设置唯一模型目录（替换整个列表）；``None`` 清除配置回到默认。"""
    return set_models_dirs([path] if path is not None else [], home)[0]


def add_models_dir(path: Path | str, home: Path | None = None) -> list[Path]:
    """追加一个模型目录（已存在则保持原位置）；返回最新列表。"""
    resolved = Path(str(path)).expanduser()
    current = configured_models_dirs(home)
    if not current:
        # 尚未登记时，第一项就是主目录；显式添加的目录直接成为主目录。
        return set_models_dirs([resolved], home)
    return set_models_dirs([*current, resolved], home)


def remove_models_dir(path: Path | str, home: Path | None = None) -> list[Path]:
    """移除一个模型目录（按真实路径比较，大小写不敏感）；返回最新列表。"""
    key = path_key(Path(str(path)).expanduser())
    current = configured_models_dirs(home)
    return set_models_dirs([item for item in current if path_key(item) != key], home)


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


def paint_dir(home: Path | None = None) -> Path:
    """画图工作目录：状态、日志与默认出图目录。"""
    if home is None:
        override = _env_path("FCMD_PAINT_DIR")
        if override is not None:
            return override
    configured = load_config(home).get("paint_dir")
    if isinstance(configured, str) and configured.strip():
        return Path(configured.strip()).expanduser()
    return home_dir(home) / "paint"


def paint_engines_root(home: Path | None = None) -> Path:
    """stable-diffusion.cpp 引擎根目录（FCMD_SD_ENGINES → 配置 → 默认）。"""
    if home is None:
        override = _env_path("FCMD_SD_ENGINES")
        if override is not None:
            return override
    configured = load_config(home).get("sd_engines_root")
    if isinstance(configured, str) and configured.strip():
        return Path(configured.strip()).expanduser()
    return home_dir(home) / "engines" / "sd.cpp"
