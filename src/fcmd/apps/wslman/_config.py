"""wslman 配置：``${FCMD_HOME:-~/.fcmd}/wslman.json``。

容错口径与 filescan 一致：文件损坏 / 非对象才整份回落默认；单字段非法只回落该字段
并给 warning；未知字段忽略。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fcmd.gui import config as _gui_config

__all__ = ["WslmanConfig", "default_path", "load_config", "parse_config"]

_TERMINALS = frozenset({"auto", "wt", "conhost"})


@dataclass(frozen=True, slots=True)
class WslmanConfig:
    path: Path
    download_dir: str = ""
    manifest_url: str | None = None
    sha256: Mapping[str, str] = field(default_factory=dict)
    temp_dir: str = ""
    safety_margin: float = 1.1
    min_free_after_mb: int = 512
    keep_partial: bool = False
    terminal_program: str = "auto"
    terminal_extra_args: tuple[str, ...] = ()
    require_backup_on_replace: bool = True
    backups_dir: str = ""
    warnings: tuple[str, ...] = ()


def default_path() -> Path:
    home = os.environ.get("FCMD_HOME")
    base = Path(home) if home else Path.home() / ".fcmd"
    return base / "wslman.json"


def load_config(path: Path | None = None) -> WslmanConfig:
    target = default_path() if path is None else path
    raw = _gui_config.read_settings(target)
    if not isinstance(raw, dict):
        return WslmanConfig(path=target)
    return parse_config(raw, target)


def parse_config(raw: Mapping[str, Any], path: Path) -> WslmanConfig:
    warnings: list[str] = []
    download = _section(raw, "download", warnings)
    transfer = _section(raw, "transfer", warnings)
    terminal = _section(raw, "terminal", warnings)
    safety = _section(raw, "safety", warnings)
    backups = _section(raw, "backups", warnings)

    margin = _float_value(transfer, "safety_margin", 1.1, warnings, "transfer.safety_margin")
    if not 1.0 <= margin <= 3.0:
        warnings.append(f"transfer.safety_margin={margin} 超出 [1.0, 3.0]，回落到 1.1")
        margin = 1.1

    min_free = _int_value(transfer, "min_free_after_mb", 512, warnings, "transfer.min_free_after_mb")
    if min_free < 0:
        warnings.append("transfer.min_free_after_mb 不能为负，回落到 512")
        min_free = 512

    program = _str_value(terminal, "program", "auto", warnings, "terminal.program").casefold()
    if program not in _TERMINALS:
        warnings.append(f"terminal.program={program!r} 不受支持，回落到 auto")
        program = "auto"

    sha256_raw = download.get("sha256")
    sha256: dict[str, str] = {}
    if isinstance(sha256_raw, Mapping):
        for name, digest in sha256_raw.items():
            if isinstance(name, str) and isinstance(digest, str) and digest:
                sha256[name] = digest.strip().casefold()
            else:
                warnings.append(f"download.sha256[{name!r}] 不是字符串，已忽略")
    elif sha256_raw is not None:
        warnings.append("download.sha256 不是对象，已忽略")

    extra_args = terminal.get("extra_args")
    terminal_args: tuple[str, ...] = ()
    if isinstance(extra_args, list) and all(isinstance(item, str) for item in extra_args):
        terminal_args = tuple(extra_args)
    elif extra_args is not None:
        warnings.append("terminal.extra_args 不是字符串数组，已忽略")

    return WslmanConfig(
        path=path,
        download_dir=_str_value(download, "dir", "", warnings, "download.dir"),
        manifest_url=_optional_str(download.get("manifest_url"), "download.manifest_url", warnings),
        sha256=sha256,
        temp_dir=_str_value(transfer, "temp_dir", "", warnings, "transfer.temp_dir"),
        safety_margin=margin,
        min_free_after_mb=min_free,
        keep_partial=_bool_value(transfer, "keep_partial", False, warnings, "transfer.keep_partial"),
        terminal_program=program,
        terminal_extra_args=terminal_args,
        require_backup_on_replace=_bool_value(
            safety,
            "require_backup_on_replace",
            True,
            warnings,
            "safety.require_backup_on_replace",
        ),
        backups_dir=_str_value(backups, "dir", "", warnings, "backups.dir"),
        warnings=tuple(warnings),
    )


def _section(raw: Mapping[str, Any], name: str, warnings: list[str]) -> Mapping[str, Any]:
    value = raw.get(name)
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    warnings.append(f"{name} 不是对象，已按默认值处理")
    return {}


def _str_value(section: Mapping[str, Any], key: str, default: str, warnings: list[str], label: str) -> str:
    value = section.get(key)
    if value is None:
        return default
    if isinstance(value, str):
        return value
    warnings.append(f"{label} 不是字符串，已回落到默认值")
    return default


def _optional_str(value: Any, label: str, warnings: list[str]) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    warnings.append(f"{label} 不是非空字符串，已忽略")
    return None


def _int_value(section: Mapping[str, Any], key: str, default: int, warnings: list[str], label: str) -> int:
    value = section.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        warnings.append(f"{label} 不是整数，已回落到默认值")
        return default
    return value


def _float_value(section: Mapping[str, Any], key: str, default: float, warnings: list[str], label: str) -> float:
    value = section.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        warnings.append(f"{label} 不是数字，已回落到默认值")
        return default
    return float(value)


def _bool_value(section: Mapping[str, Any], key: str, default: bool, warnings: list[str], label: str) -> bool:
    value = section.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    warnings.append(f"{label} 不是布尔值，已回落到默认值")
    return default
