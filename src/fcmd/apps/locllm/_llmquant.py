"""GGUF 量化标签解析：基础量化与 Unsloth Dynamic（UD）变体。

下载仓库、扫描本地模型与按档位选模型都依赖同一套量化口径：

- 基础：``Q4_K_M`` / ``Q5_K_M`` / ``Q8_0`` / ``IQ4_XS`` …
- 动态：``UD-Q4_K_XL``（Unsloth Dynamic，按层混合量化）
- 分片：``…-00001-of-00003.gguf`` 先去掉分片后缀再解析

``family`` 用于档位匹配（``Q4_K_M`` 与 ``UD-Q4_K_XL`` 同属 ``Q4_K``），
``variant`` 用于同族区分（``S`` / ``M`` / ``L`` / ``XL``）；``raw`` 保留
给界面显示，避免被 GGUF 元数据里的基础 ``file_type`` 覆盖。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "QuantTag",
    "exact_quant_match",
    "matches_profile",
    "parse_quant_tag",
]

_SHARD_RE = re.compile(r"-\d{5}-of-\d{5}$", re.IGNORECASE)
_QUANT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<ud>UD[-_])?(?P<quant>I?Q\d+(?:_[A-Za-z0-9]+)*|BF16|F16|F32|MXFP4)(?=$|[-_.])",
    re.IGNORECASE,
)
#: 动态量化常见的变体：XL 表示按层混合，质量通常高于同族 M
_DYNAMIC_VARIANTS = frozenset({"XL"})


@dataclass(frozen=True, slots=True)
class QuantTag:
    """一个文件名或量化名中的量化描述。"""

    raw: str
    """界面显示名（保留 ``UD-`` 前缀），如 ``UD-Q4_K_XL``。"""
    family: str
    """量化族，如 ``Q4_K`` / ``Q4_0`` / ``IQ4_XS``。"""
    variant: str
    """族内变体，如 ``S`` / ``M`` / ``L`` / ``XL``；无变体时为空串。"""
    dynamic: bool
    """是否动态量化（``UD-`` 前缀或 ``XL`` 变体）。"""


def _strip_shard(name: str) -> str:
    """去掉 ``-00001-of-00003`` 分片后缀（保留 .gguf 前的其余内容）。"""
    stem = Path(name).stem
    return _SHARD_RE.sub("", stem)


def parse_quant_tag(text: str | Path) -> QuantTag | None:
    """从文件名 / 路径 / 量化名解析量化标签；无法识别时返回 ``None``。"""
    stem = _strip_shard(Path(text).name)
    matches = list(_QUANT_RE.finditer(stem))
    if not matches:
        return None
    match = matches[-1]
    quant = match.group("quant").upper()
    ud = bool(match.group("ud"))
    parts = quant.split("_")
    if len(parts) >= 3 and parts[0].startswith("Q") and parts[1] == "K":
        family = "_".join(parts[:2])
        variant = parts[2]
    else:
        family = quant
        variant = ""
    dynamic = ud or variant in _DYNAMIC_VARIANTS
    raw = f"UD-{quant}" if ud else quant
    return QuantTag(raw=raw, family=family, variant=variant, dynamic=dynamic)


def exact_quant_match(filename: str | Path, requested: str) -> bool:
    """文件名与显式量化名是否完全一致（忽略大小写与 ``UD-`` 前缀差异）。"""
    tag = parse_quant_tag(filename)
    wanted = parse_quant_tag(requested)
    if tag is None or wanted is None:
        return str(requested).casefold() in Path(filename).name.casefold()
    return tag.raw.casefold() == wanted.raw.casefold()


def matches_profile(filename: str | Path, profile_quant: str) -> bool:
    """文件名是否满足档位量化。

    ``Q4_K_M`` 兼容同族动态 ``XL``（``UD-Q4_K_XL`` / ``Q4_K_XL``），
    但不把 ``Q4_0`` 当作 ``Q4_K``，也不接受更小的 ``S`` 变体。
    """
    tag = parse_quant_tag(filename)
    target = parse_quant_tag(profile_quant)
    if tag is None or target is None:
        return str(profile_quant).casefold() in Path(filename).name.casefold()
    if tag.family != target.family:
        return False
    if not target.variant:
        return True
    if tag.variant == target.variant:
        return True
    return tag.variant in _DYNAMIC_VARIANTS and target.variant in {"S", "M", "L"}
