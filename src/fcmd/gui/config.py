"""GUI 工具共享的设置持久化骨架（不注册工具）。

全部 GUI 窗口工具（llm 的 5 个窗口、img2icow / videoconvw / ncmconvertw、
img2pdfw / pdfcryptw / pdfsplitw、reshackw / checksum / alarmclock、
regexvalidatew、docdiffw）的设置文件统一位于
``${FCMD_HOME:-~/.fcmd}/ui/<name>.json``，读写流程与 geometry 校验完全一致。
本模块收敛这部分骨架，各工具只保留设置 dataclass 与字段级解析。

本模块位于 :mod:`fcmd.gui`，不参与 :mod:`fcmd.tools` 的工具发现。
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fcmd.gui.geometry import clamp_geometry, valid_geometry
from fcmd.infras.system import fcmd_home

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["clamp_geometry", "config_path", "read_settings", "valid_geometry", "write_settings"]


def config_path(name: str) -> Path:
    """返回 ``${FCMD_HOME:-~/.fcmd}/ui/<name>.json``。"""
    return fcmd_home() / "ui" / f"{name}.json"


def read_settings(path: Path) -> dict[str, Any] | None:
    """读取设置 JSON；主文件损坏时回退到 ``.bak``，均不可用时返回 ``None``。"""
    for candidate in (path, _backup_path(path)):
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(raw, dict):
            return raw
    return None


def write_settings(payload: Mapping[str, Any], path: Path) -> None:
    """写入设置 JSON；父目录自动创建，原子替换并保留一份 ``.bak`` 备份。

    先写同目录临时文件再 ``os.replace``，避免写到一半中断留下半截 JSON。
    替换前只备份可解析的旧文件，防止损坏内容覆盖最近一次有效备份。
    写入失败静默忽略：GUI 关闭路径不应因设置落盘失败而报错。
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _backup_previous(path)
        tmp.replace(path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _backup_path(path: Path) -> Path:
    """返回设置文件对应的备份路径（``<name>.json.bak``）。"""
    return path.with_suffix(path.suffix + ".bak")


def _backup_previous(path: Path) -> None:
    """把当前设置文件内容备份到 ``.bak``；文件缺失或内容损坏时跳过。"""
    try:
        previous = path.read_text(encoding="utf-8")
        json.loads(previous)
    except (OSError, ValueError):
        return
    try:
        _backup_path(path).write_text(previous, encoding="utf-8")
    except OSError:
        return
