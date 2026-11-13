"""``%UserProfile%\\.wslconfig`` 解析与校验（只读）。

v1 不写该文件：写入需要处理引号、用户注释与 ``wsl --shutdown`` 生效时机，
风险边界与本工具的只读定位不同。
"""

from __future__ import annotations

import configparser
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = ["WslConfig", "default_path", "load_wslconfig", "parse_wslconfig_text", "summary_lines"]

_KNOWN_KEYS: dict[str, frozenset[str]] = {
    "wsl2": frozenset(
        {
            "memory",
            "processors",
            "swap",
            "swapfile",
            "localhostforwarding",
            "kernel",
            "kernelcommandline",
            "nestedvirtualization",
            "vmidletimeout",
            "safemode",
            "guiapplications",
            "debugconsole",
            "defaultvhdsize",
        }
    ),
    "experimental": frozenset(
        {
            "automemoryreclaim",
            "sparsevhd",
            "usewindowsdnscache",
            "besteffortdnsparsing",
            "networkingmode",
            "dnstunneling",
            "firewall",
            "ignoredports",
            "hostaddressloopback",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class WslConfig:
    """解析结果；``error`` 非空表示整份文件不可用。"""

    path: Path
    exists: bool
    sections: Mapping[str, Mapping[str, str]]
    warnings: tuple[str, ...]
    error: str | None

    @property
    def ok(self) -> bool:
        return self.error is None

    def get(self, section: str, key: str) -> str | None:
        values = self.sections.get(section)
        if values is None:
            return None
        return values.get(key)


def default_path() -> Path:
    """``%UserProfile%\\.wslconfig``；非 Windows 环境回落到用户主目录。"""
    home = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(home) / ".wslconfig"


def load_wslconfig(path: Path | None = None) -> WslConfig:
    target = default_path() if path is None else path
    if not target.exists():
        return WslConfig(path=target, exists=False, sections={}, warnings=(), error=None)
    try:
        text = target.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        return WslConfig(path=target, exists=True, sections={}, warnings=(), error=f"读取 .wslconfig 失败：{exc}")
    return parse_wslconfig_text(text, target)


def parse_wslconfig_text(text: str, path: Path) -> WslConfig:
    warnings: list[str] = []
    _check_duplicates(text, warnings)

    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # type: ignore[assignment] - 保留键的原始大小写
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        return WslConfig(
            path=path, exists=True, sections={}, warnings=tuple(warnings), error=f".wslconfig 解析失败：{exc}"
        )

    sections: dict[str, dict[str, str]] = {}
    known_sections = set(_KNOWN_KEYS)
    for section in parser.sections():
        values = dict(parser.items(section))
        sections[section] = values
        lowered = section.casefold()
        if lowered not in known_sections:
            warnings.append(f"未知小节 [{section}]，WSL 可能忽略")
            continue
        known = _KNOWN_KEYS[lowered]
        for key in values:
            if key.casefold() not in known:
                warnings.append(f"[{section}] 含未知键 {key}，WSL 可能忽略")

    return WslConfig(path=path, exists=True, sections=sections, warnings=tuple(warnings), error=None)


def _check_duplicates(text: str, warnings: list[str]) -> None:
    """configparser 的 strict=False 会静默取最后一个值，这里单独提示重复键。"""
    section: str | None = None
    seen: dict[str, set[str]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            seen.setdefault(section.casefold(), set())
            continue
        if "=" not in line:
            continue
        if section is None:
            warnings.append("存在位于任何小节之外的键，WSL 可能忽略")
            continue
        key = line.split("=", 1)[0].strip().casefold()
        bucket = seen.setdefault(section.casefold(), set())
        if key in bucket:
            warnings.append(f"[{section}] 键 {key} 重复，后者生效")
        bucket.add(key)


def summary_lines(config: WslConfig) -> list[str]:
    """把配置压成人类可读摘要（按小节与键排序，保证输出稳定）。"""
    if config.error:
        return [f"[错误] {config.error}"]
    if not config.exists:
        return [f"未找到 {config.path}（可选文件，WSL 会使用默认值）"]
    lines: list[str] = []
    for section in sorted(config.sections):
        lines.append(f"[{section}]")
        values = config.sections[section]
        for key in sorted(values, key=str.casefold):
            lines.append(f"  {key} = {values[key]}")
    if not lines:
        lines.append(f"{config.path} 为空")
    for warning in config.warnings:
        lines.append(f"[警告] {warning}")
    return lines
