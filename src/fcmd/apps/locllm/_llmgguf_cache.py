"""GGUF 精度识别结果的本地缓存。

``locllmw`` 模型列表要为每个 GGUF 解析一次头部；30B 级模型单文件就要
200–400 ms，串行扫描会让列表迟迟填不满。本模块把识别结果按
「绝对路径 + mtime_ns + size」缓存到 ``${FCMD_HOME:-~/.fcmd}/cache/gguf-precision.json``：

- 文件被重新下载、原地替换或修改后 ``mtime_ns`` / ``size`` 变化，条目自动失效；
- 缓存结构版本变化时整体丢弃并重建（窗口只存 ``tensors=False`` 快路径的结果，
  不记录 ``expected_bytes``，因此不参与“文件疑似不完整”判断）；
- 只保存 :class:`GgufPrecision` 的原始字段，``note`` / ``detail`` 等派生文本
  仍按当前代码计算，避免字段口径调整后读到旧文案；
- 读写失败一律降级为「本次不缓存」，不影响扫描本身。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from fcmd.apps.locllm._llmgguf import GgufPrecision, PrecisionSource

__all__ = ["GGUF_CACHE_SCHEMA_VERSION", "PrecisionCache", "default_cache_path"]

GGUF_CACHE_SCHEMA_VERSION = 2
"""缓存结构版本；与文件中的 ``version`` 不一致时整体丢弃。"""

_MAX_ENTRIES = 2000
"""最多保留的模型条目数；超出后按写入顺序丢弃最旧的条目。"""

_SOURCES = frozenset({"metadata", "tensors", "filename", "unknown"})


def default_cache_path() -> Path:
    """默认缓存文件：``${FCMD_HOME:-~/.fcmd}/cache/gguf-precision.json``。"""
    home = os.environ.get("FCMD_HOME")
    base = Path(home) if home else Path.home() / ".fcmd"
    return base / "cache" / "gguf-precision.json"


def _path_key(path: Path) -> str:
    """缓存键：绝对路径 + 平台大小写口径（与 ``llmenv.path_key`` 一致）。"""
    return os.path.normcase(str(path.absolute()))


@dataclass(frozen=True, slots=True)
class _Entry:
    """一条缓存：识别时刻的文件指纹 + 结果。"""

    mtime_ns: int
    size: int
    precision: GgufPrecision


def _entry_payload(precision: GgufPrecision) -> dict[str, Any]:
    return {
        "label": precision.label,
        "quantized": precision.quantized,
        "source": precision.source,
        "filename_label": precision.filename_label,
        "filename_quantized": precision.filename_quantized,
        "expected_bytes": precision.expected_bytes,
        "actual_bytes": precision.actual_bytes,
    }


def _entry_from_payload(raw: Any) -> _Entry:
    """从 JSON 还原一条缓存；结构异常时抛 ``ValueError`` / ``TypeError``。"""
    if not isinstance(raw, dict):
        raise ValueError("缓存条目不是对象")
    source = raw["source"]
    if source not in _SOURCES:
        raise ValueError("缓存条目的来源未知")
    precision = GgufPrecision(
        label=str(raw["label"]),
        quantized=raw["quantized"],
        source=cast(PrecisionSource, source),
        filename_label=raw.get("filename_label"),
        filename_quantized=raw.get("filename_quantized"),
        expected_bytes=raw.get("expected_bytes"),
        actual_bytes=raw.get("actual_bytes"),
    )
    return _Entry(mtime_ns=int(raw["mtime_ns"]), size=int(raw["size"]), precision=precision)


class PrecisionCache:
    """GGUF 精度缓存；读写失败自动降级，不向调用方抛异常。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_cache_path()
        self._entries: dict[str, _Entry] = {}
        self._dirty = False
        self._load()

    def get(self, model: Path) -> GgufPrecision | None:
        """文件指纹与缓存一致时返回结果，否则 ``None``。"""
        try:
            stat = model.stat()
        except OSError:
            return None
        entry = self._entries.get(_path_key(model))
        if entry is None or entry.mtime_ns != stat.st_mtime_ns or entry.size != stat.st_size:
            return None
        return entry.precision

    def remember(self, model: Path, precision: GgufPrecision) -> None:
        """记录一次识别结果；读取失败的 ``unknown`` 不缓存，避免固化瞬时错误。"""
        if precision.source == "unknown":
            return
        try:
            stat = model.stat()
        except OSError:
            return
        self._entries[_path_key(model)] = _Entry(stat.st_mtime_ns, stat.st_size, precision)
        self._dirty = True

    def save(self) -> None:
        """原子写回缓存文件；目录不可写等失败时静默放弃，下次重新解析。"""
        if not self._dirty:
            return
        for key in list(self._entries)[: max(len(self._entries) - _MAX_ENTRIES, 0)]:
            del self._entries[key]
        payload = {
            "version": GGUF_CACHE_SCHEMA_VERSION,
            "entries": {
                key: {"mtime_ns": entry.mtime_ns, "size": entry.size, **_entry_payload(entry.precision)}
                for key, entry in self._entries.items()
            },
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            return
        self._dirty = False

    def _load(self) -> None:
        """读取缓存文件；结构或版本不符时保持空缓存。"""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict) or raw.get("version") != GGUF_CACHE_SCHEMA_VERSION:
            return
        entries = raw.get("entries")
        if not isinstance(entries, dict):
            return
        for key, item in entries.items():
            try:
                self._entries[key] = _entry_from_payload(item)
            except (KeyError, TypeError, ValueError):
                continue
