"""locllmw 设置页：存储 / 引擎两页配置 + 关于与诊断。

所有目录解析都委托给 :mod:`fcmd.apps.locllm._llmconfig`，页面不保存私有默认值；
修改直接写入权威 ``config.json``。「引擎」页等价承载原独立引擎窗口的能力：
引擎根目录、已安装引擎列表与默认切换、在线下载、离线包检查 / 安装、运行体检，
全部调用 :mod:`fcmd.apps.locllm.llmengine`（与 ``fcmd llmengine`` 同一套逻辑）。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import fcmd
from fcmd.apps.locllm import _llmconfig, llmengine
from fcmd.apps.locllm.llmbase import Check, EngineInfo, EngineVariant, ServeError, human_size
from fcmd.gui import config as _guiconfig

__all__ = [
    "EngineStatus",
    "apply_path",
    "check_engine_tag",
    "directory_rows",
    "engine_rows",
    "engine_status",
    "inspect_offline",
    "install_offline",
    "install_online",
    "installed_engines",
    "migration_note",
    "select_engine",
    "ui_rows",
]

_ENV = "环境变量"
_CONFIG = "配置"
_DEFAULT = "默认"
_AUTO = "自动保存"

_VARIANTS = ("cuda", "cpu")
_OFFLINE_PLACEHOLDER = "尚未选择离线包（zip 文件或包含 zip 的目录）"
_KIND_LABELS = {
    "engine": "引擎",
    "cudart": "CUDA 运行库",
    "source": "源码包",
    "unknown": "未知",
    "invalid": "无效",
}

#: 设置页可编辑的目录项：配置键 → (行标题, 选择对话框标题)
_EDITABLE: dict[str, tuple[str, str]] = {
    "models_dir": ("模型目录", "选择模型目录（仅支持单一目录）"),
    "engines_root": ("引擎根目录", "选择 llama.cpp 引擎根目录"),
}

TaskFunc = Callable[[Callable[[str, int, int | None], None]], tuple[str, str | None]]


# ---------------------------------------------------------------------- #
# 纯逻辑：存储 / 引擎 / 关于
# ---------------------------------------------------------------------- #
def _source(env_name: str, config_key: str, home: Path | None = None) -> str:
    """来源标注：环境变量 > 配置 > 默认（显式 home 时忽略环境变量）。"""
    if home is None and os.environ.get(env_name, "").strip():
        return _ENV
    value = _llmconfig.get_option(config_key, home)
    if isinstance(value, str) and value.strip():
        return _CONFIG
    return _DEFAULT


def directory_rows(home: Path | None = None) -> list[tuple[str, str, str]]:
    """返回「存储」页 ``(项目, 路径, 来源)``：只有模型目录。"""
    return [
        ("模型目录", str(_llmconfig.models_dir(home=home)), _source("FCMD_MODELS", "models_dir", home)),
    ]


def engine_rows(home: Path | None = None) -> list[tuple[str, str, str]]:
    """返回「引擎」页根目录 ``(项目, 路径, 来源)``。"""
    return [("引擎根目录", str(_llmconfig.engines_root(home)), _source("FCMD_ENGINES", "engines_root", home))]


def ui_rows() -> list[tuple[str, str, str]]:
    """返回「关于与诊断」折叠区：程序版本、配置文件与窗口记忆（只读）。"""
    path = _guiconfig.config_path("locllmw")
    settings = _guiconfig.read_settings(path) or {}
    geometry = settings.get("geometry")
    geometry_text = geometry if isinstance(geometry, str) and geometry.strip() else "未保存（使用默认尺寸）"
    return [
        ("程序版本", fcmd.__version__, _AUTO),
        ("配置文件", str(_llmconfig.config_path()), _AUTO),
        ("窗口配置文件", str(path), _AUTO),
        ("窗口位置与大小", geometry_text, _AUTO),
    ]


def migration_note(home: Path | None = None) -> str:
    """旧配置迁移提示：多余模型目录 + 已忽略的旧工作目录；无记录时返回空串。"""
    notes: list[str] = []
    extras = _llmconfig.models_dir_extra(home)
    if extras:
        notes.append("旧配置中的其他模型目录已记录但不再扫描：" + "、".join(extras))
    ignored = _llmconfig.ignored_work_dir(home)
    if ignored:
        notes.append(f"旧工作目录已忽略（下载与转换产物统一使用模型目录）：{ignored}")
    return "\n".join(notes)


def apply_path(key: str, value: str | None, home: Path | None = None) -> Path:
    """写入设置页目录项并返回当前生效路径；``value`` 为 ``None`` 表示恢复默认。"""
    if key == "models_dir":
        return _llmconfig.set_models_dir(Path(value) if value else None, home)
    if key == "engines_root":
        _llmconfig.set_option("engines_root", value or None, home)
        return _llmconfig.engines_root(home)
    raise ValueError(f"未知设置项: {key}")


@dataclass(frozen=True)
class EngineStatus:
    """引擎页摘要：根目录、当前引擎描述、已安装数量。"""

    root: Path
    current: str
    installed: int


def installed_engines(home: Path | None = None) -> list[EngineInfo]:
    """已安装引擎（按 tag 降序，当前引擎 ``source`` 为 ``current``）。"""
    return llmengine.list_engines(home)


def engine_status(home: Path | None = None) -> EngineStatus:
    """读取引擎页摘要；当前引擎无法解析时给出原因文案。"""
    infos = installed_engines(home)
    try:
        current = llmengine.resolve_engine(home=home)
    except ServeError as exc:
        text = f"未找到（{exc}）"
    else:
        text = f"{current.tag} · {current.exe}"
    return EngineStatus(_llmconfig.engines_root(home), text, len(infos))


def select_engine(tag: str, home: Path | None = None) -> EngineInfo:
    """把 ``tag`` 设为默认引擎（复用 ``fcmd llmengine use`` 的实现）。"""
    return llmengine.use_engine(tag, home)


def check_engine_tag(tag: str | None, home: Path | None = None) -> Check:
    """解析并体检引擎，返回 ``llama-server --version`` 的结果。"""
    return llmengine.check_engine(llmengine.resolve_engine(tag, home))


def install_online(
    tag: str,
    variant: EngineVariant = "cuda",
    home: Path | None = None,
    on_progress: llmengine.ProgressCallback | None = None,
) -> EngineInfo:
    """在线安装引擎；没有默认引擎时自动把新装的设为默认。"""
    info = llmengine.install_engine(tag, variant=variant, home=home, on_progress=on_progress)
    _ensure_current(info.tag, home)
    return info


def inspect_offline(source: Path, *, deep: bool = False) -> list[llmengine.ZipInspection]:
    """检查离线包（zip 或包含 zip 的目录）。"""
    return llmengine.inspect_zips([source], deep=deep)


def install_offline(
    tag: str,
    source: Path,
    home: Path | None = None,
    *,
    infer_tag: bool = False,
) -> tuple[EngineInfo, str]:
    """安装离线包或登记已编译引擎目录，返回 ``(引擎信息, 实际使用的 tag)``。

    ``source`` 为目录时优先安装目录内全部 zip，没有 zip 则按已编译引擎目录登记；
    为 zip 时直接解压安装。``infer_tag`` 为真且包内能推断出 tag 时用它替换入参。
    """
    target = tag
    if infer_tag:
        inferred = next((item.tag for item in inspect_offline(source) if item.tag), None)
        if inferred:
            target = inferred
    if source.is_dir():
        zips = sorted(item for item in source.glob("*.zip") if item.is_file())
        info = (
            llmengine.install_engine(target, archives=zips, home=home)
            if zips
            else llmengine.install_engine(target, path=source, home=home)
        )
    else:
        info = llmengine.install_engine(target, archives=[source], home=home)
    _ensure_current(target, home)
    return info, target


def _ensure_current(tag: str, home: Path | None = None) -> None:
    """没有默认引擎时把刚安装的引擎设为默认。"""
    if not any(item.source == "current" for item in installed_engines(home)):
        llmengine.use_engine(tag, home)


def format_inspection(item: llmengine.ZipInspection) -> str:
    """把单个离线包检查结果格式化为一行正文 + 附注。"""
    state = "可用" if item.usable else "不可安装"
    lines = [
        f"{item.path.name}: {_KIND_LABELS[item.kind]} / {state} / TAG={item.tag or '-'} / "
        f"变体={item.variant or '-'} / llama-server={item.server or '-'} / {human_size(item.total_size)}"
    ]
    lines.extend(f"  · {note}" for note in item.notes)
    lines.extend(f"  ! {problem}" for problem in item.problems)
    return "\n".join(lines)


# ---------------------------------------------------------------------- #
# 设置页
# ---------------------------------------------------------------------- #
