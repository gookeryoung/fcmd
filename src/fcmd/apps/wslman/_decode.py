"""控制台字节解码与 ``wsl.exe`` 文本解析。

设计前提：``wsl.exe`` 被重定向时输出 UTF-16LE（带 BOM，偶数字节为 NUL），
但新版 WSL 在设置 ``WSL_UTF8=1`` 后可能输出 UTF-8，因此必须逐级判定而不是写死编码。
所有函数都是纯函数，便于在没有 WSL 的机器上跑单测。
"""

from __future__ import annotations

import codecs
import locale
import re
from collections.abc import Iterable
from dataclasses import dataclass

__all__ = [
    "DistroRow",
    "OnlineRow",
    "decode_console",
    "find_pair",
    "is_no_distribution_message",
    "is_not_enabled_message",
    "parse_distro_list",
    "parse_online_list",
    "parse_status",
    "parse_wsl_version",
]

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
_VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+){0,2}")

# ``wsl -l -v`` 的 STATE 取值（英文），其余取值保留原文并记为 unknown。
_KNOWN_STATES = {
    "running",
    "stopped",
    "installing",
    "uninstalling",
    "converting",
    "starting",
    "stopping",
    "installed",
}

_ONLINE_HEADERS = frozenset({"name", "名称", "发行版名称", "分发版名称"})

_NO_DISTRO_PATTERNS = (
    "no installed distributions",
    "没有已安装的分发版",
    "没有已安装的发行版",
    "尚未安装任何分发",
    "未安装任何发行版",
)

_NOT_ENABLED_PATTERNS = (
    "hyperv",
    "virtual machine platform",
    "虚拟机平台",
    "可选组件",
    "optional component",
    "wsl is not installed",
    "未安装适用于 linux",
    "内核组件",
    "wslregisterdistribution failed",
)


@dataclass(frozen=True, slots=True)
class DistroRow:
    """``wsl --list --verbose`` 的一行。"""

    name: str
    state: str
    state_raw: str
    version: int | None
    default: bool


@dataclass(frozen=True, slots=True)
class OnlineRow:
    """``wsl --list --online`` 的一行。"""

    name: str
    friendly_name: str


def decode_console(raw: bytes) -> str:
    """把 ``wsl.exe`` 的输出字节解码成文本。

    判定顺序：UTF-16 BOM → 偶数位 NUL 占比 → UTF-8 → 本地编码。
    最后统一去掉 NUL、BOM、ANSI 转义与行尾空白。
    """
    if not raw:
        return ""

    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = raw.decode("utf-16", errors="replace")
    elif _utf16le_score(raw) > 0.3:
        text = raw.decode("utf-16-le", errors="replace")
    else:
        text = _decode_utf8_or_locale(raw)
    return _clean(text)


def _utf16le_score(raw: bytes) -> float:
    sample = raw[:4096]
    units = len(sample) // 2
    if units == 0:
        return 0.0
    # UTF-16LE 的 ASCII 字符形如 41 00，NUL 落在奇数位。
    zeros = sample[1::2].count(0)
    return zeros / units


def _decode_utf8_or_locale(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        encoding = locale.getpreferredencoding(False) or "utf-8"
        return raw.decode(encoding, errors="replace")


def _clean(text: str) -> str:
    text = _ANSI_RE.sub("", text)
    text = text.replace("\x00", "").replace("\ufeff", "")
    lines = (line.rstrip() for line in text.splitlines())
    return "\n".join(lines).strip()


def parse_distro_list(text: str) -> tuple[DistroRow, ...]:
    """解析 ``wsl --list --verbose``。

    不匹配表头文字（本地化会变）：表头最后一列不是 1/2，会被版本判定自然跳过。
    名称从右往左切：最后一列是版本，倒数第二列是状态，其余都是名称（允许空格）。
    """
    rows: list[DistroRow] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        default = stripped.startswith("*")
        body = stripped[1:].strip() if default else stripped
        parts = body.split()
        if len(parts) < 3:
            continue
        if parts[0].upper() == "NAME" and parts[-1].upper() == "VERSION":
            continue
        if parts[-1] not in {"1", "2"}:
            continue
        version = int(parts[-1])
        state_raw = parts[-2]
        name = " ".join(parts[:-2]).strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        state = state_raw.casefold() if state_raw.casefold() in _KNOWN_STATES else "unknown"
        rows.append(DistroRow(name=name, state=state, state_raw=state_raw, version=version, default=default))
    return tuple(rows)


def parse_online_list(text: str) -> tuple[OnlineRow, ...]:
    """解析 ``wsl --list --online``：两列以制表符或两个以上空格分隔。"""
    rows: list[OnlineRow] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "\t" in stripped:
            name, _, friendly = stripped.partition("\t")
        else:
            match = re.match(r"^(\S+)\s{2,}(.+)$", stripped)
            if match:
                name, friendly = match.group(1), match.group(2)
            elif " " not in stripped:
                # 单列名称（无友好名）；含空格的整句介绍与本地化表头在此被跳过。
                name, friendly = stripped, ""
            else:
                continue
        name = name.strip()
        friendly = friendly.strip()
        if not name or name.casefold() in _ONLINE_HEADERS:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append(OnlineRow(name=name, friendly_name=friendly))
    return tuple(rows)


def parse_wsl_version(text: str) -> str | None:
    """从 ``wsl --version`` 的输出里取 WSL 自身版本号。"""
    for line in text.splitlines():
        if "wsl" in line.casefold():
            match = _VERSION_RE.search(line)
            if match:
                return match.group(0)
    match = _VERSION_RE.search(text)
    return match.group(0) if match else None


def parse_status(text: str) -> tuple[tuple[str, str], ...]:
    """把 ``wsl --status`` 拆成（标签, 值）对，仅用于展示。

    输出是本地化自由文本，这些键值对**不作为**默认发行版或版本的判定依据。
    """
    pairs: list[tuple[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        match = re.match(r"^([^:：]+)[:：]\s*(.+)$", stripped)
        if match and match.group(1).strip() and match.group(2).strip():
            pairs.append((match.group(1).strip(), match.group(2).strip()))
    return tuple(pairs)


def find_pair(pairs: Iterable[tuple[str, str]], labels: Iterable[str]) -> str | None:
    """在（标签, 值）对里按标签候选查找第一个值。"""
    wanted = {label.casefold() for label in labels}
    for label, value in pairs:
        if label.casefold() in wanted:
            return value
    return None


def is_no_distribution_message(text: str) -> bool:
    """是否是「WSL 正常但没有任何发行版」的提示。"""
    lowered = text.casefold()
    return any(pattern in lowered for pattern in _NO_DISTRO_PATTERNS)


def is_not_enabled_message(text: str) -> bool:
    """是否是「WSL 未启用 / 系统组件缺失」的提示。"""
    lowered = text.casefold()
    return any(pattern in lowered for pattern in _NOT_ENABLED_PATTERNS)
