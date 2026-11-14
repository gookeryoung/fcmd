"""GGUF 精度探测：区分全量 F16/BF16 与量化版本（纯逻辑，可单测）。

``locllmw`` 用这里的结果在模型列表与提示区备注精度。识别优先读取 GGUF
元数据 ``general.file_type``，并用实际张量类型交叉校验；读不到头部时回退到
文件名标记。全量类型还会按张量元素数推算预期文件大小，提示下载中断等
“看起来是 F16、实际不完整”的情况。
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal

from fcmd.apps.locllm.llmbase import human_size

__all__ = ["GGUF_META_LIMIT", "GgufPrecision", "detect_precision", "quantize_input_error"]

#: 元数据最多读取的字节数（防止异常文件导致长时间扫描）
GGUF_META_LIMIT = 64 * 1024 * 1024

PrecisionSource = Literal["metadata", "tensors", "filename", "unknown"]

_TYPE_UINT32 = 4
_TYPE_STRING = 8
_TYPE_ARRAY = 9

#: GGUF 标量值类型 → 字节数（字符串与数组单独处理）
_SCALAR_SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}

#: ``general.file_type`` → 用户可读的量化名
_FILE_TYPE_NAMES = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    4: "Q4_1",
    7: "Q8_0",
    8: "Q5_0",
    9: "Q5_1",
    10: "Q2_K",
    11: "Q3_K_S",
    12: "Q3_K_M",
    13: "Q3_K_L",
    14: "Q4_K_S",
    15: "Q4_K_M",
    16: "Q5_K_S",
    17: "Q5_K_M",
    18: "Q6_K",
    19: "IQ2_XXS",
    20: "IQ2_XS",
    21: "IQ2_S",
    22: "IQ2_M",
    23: "IQ1_S",
    24: "IQ1_M",
    25: "IQ3_XXS",
    26: "IQ3_S",
    27: "IQ3_M",
    28: "IQ4_NL",
    29: "IQ4_XS",
    30: "BF16",
    31: "Q4_0_4_4",
    32: "Q4_0_4_8",
    33: "Q4_0_8_8",
    34: "TQ1_0",
    35: "TQ2_0",
    36: "MXFP4",
}

#: ggml 张量类型 → 名称（与 llama.cpp ``ggml_type`` 顺序一致）
_TENSOR_TYPE_NAMES = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    6: "Q5_0",
    7: "Q5_1",
    8: "Q8_0",
    9: "Q8_1",
    10: "Q2_K",
    11: "Q3_K",
    12: "Q4_K",
    13: "Q5_K",
    14: "Q6_K",
    15: "Q8_K",
    16: "IQ2_XXS",
    17: "IQ2_XS",
    18: "IQ3_XXS",
    19: "IQ1_S",
    20: "IQ4_NL",
    21: "IQ3_S",
    22: "IQ2_S",
    23: "IQ4_XS",
    24: "I8",
    25: "I16",
    26: "I32",
    27: "I64",
    28: "F64",
    29: "IQ1_M",
    30: "BF16",
    31: "Q4_0_4_4",
    32: "Q4_0_4_8",
    33: "Q4_0_8_8",
    34: "TQ1_0",
    35: "TQ2_0",
    36: "IQ4_NL_4_4",
    37: "IQ4_NL_4_8",
    38: "IQ4_NL_8_8",
    39: "MXFP4",
}

#: 固定宽度张量类型 → 每元素字节数（可据此推算文件预期大小）
_ELEMENT_BYTES = {"F32": 4, "F16": 2, "BF16": 2, "F64": 8, "I8": 1, "I16": 2, "I32": 4, "I64": 8}

#: 全量精度（未量化）的 file_type / 张量类型
_FULL_FILE_TYPES = frozenset({0, 1, 30})
_FULL_TENSOR_TYPES = frozenset(_ELEMENT_BYTES)

#: 文件名中的精度标记（取最靠后的一个，避免命中模型名中的版本号）
_FILENAME_MARKER_RE = re.compile(
    r"(?:^|[-_.])(IQ\d+(?:_[A-Za-z0-9]+)*|TQ\d+(?:_[A-Za-z0-9]+)*|Q\d+(?:_[A-Za-z0-9]+)*|BF16|F16|F32|MXFP4)(?=\.|$|[-_])",
    re.IGNORECASE,
)

_SOURCE_TEXT: dict[PrecisionSource, str] = {
    "metadata": "GGUF 元数据",
    "tensors": "GGUF 张量类型",
    "filename": "文件名（未读到元数据）",
    "unknown": "无法识别",
}


@dataclass(frozen=True, slots=True)
class GgufPrecision:
    """GGUF 精度识别结果。

    Attributes
    ----------
    label:
        精度描述，如 ``F16 全量，未量化`` / ``Q4_K_M 量化``。
    quantized:
        ``True`` 表示量化版本，``False`` 表示全量精度，``None`` 表示未知。
    source:
        结论来源（元数据 / 张量类型 / 文件名回退）。
    filename_label:
        文件名中的精度标记（如 ``F16``）；没有则为 ``None``。
    filename_quantized:
        文件名标记对应的量化类别；没有标记时为 ``None``。
    expected_bytes:
        全量精度下按张量元素数推算的预期文件大小；量化版本或无法推算时为 ``None``。
    actual_bytes:
        文件实际大小；文件不可读时为 ``None``。
    """

    label: str
    quantized: bool | None
    source: PrecisionSource
    filename_label: str | None = None
    filename_quantized: bool | None = None
    expected_bytes: int | None = None
    actual_bytes: int | None = None

    @property
    def mismatch(self) -> bool:
        """文件名标注与实测精度类别是否不一致。"""
        return (
            self.quantized is not None
            and self.filename_quantized is not None
            and self.quantized != self.filename_quantized
        )

    @property
    def incomplete(self) -> bool:
        """全量精度下实际文件是否明显小于预期大小。"""
        if self.expected_bytes is None or self.actual_bytes is None:
            return False
        return self.actual_bytes < self.expected_bytes * 0.99

    @property
    def note(self) -> str:
        """用于界面括号备注的短文本。"""
        parts = [self.label]
        if self.mismatch:
            parts.append(f"文件名标注 {self.filename_label}")
        if self.incomplete and self.expected_bytes is not None and self.actual_bytes is not None:
            parts.append(f"文件疑似不完整 {human_size(self.actual_bytes)} / 预期 {human_size(self.expected_bytes)}")
        return "；".join(parts)

    @property
    def detail(self) -> str:
        """提示区使用的完整说明（含结论来源）。"""
        return f"{self.note}（来源: {_SOURCE_TEXT[self.source]}）"


@dataclass(frozen=True, slots=True)
class _GgufMeta:
    """从 GGUF 头部读到的精度相关元数据。"""

    file_type: int | None
    tensor_type: str | None
    expected_bytes: int | None


def _read(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise ValueError("GGUF 文件截断")
    return data


def _read_string(stream: BinaryIO) -> str:
    (length,) = struct.unpack("<Q", _read(stream, 8))
    if length > GGUF_META_LIMIT:
        raise ValueError("GGUF 字符串长度异常")
    return _read(stream, length).decode("utf-8", "replace")


def _skip_value(stream: BinaryIO, value_type: int) -> None:
    """跳过一个元数据值（数组按元素类型逐项跳过）。"""
    if value_type == _TYPE_STRING:
        _read_string(stream)
        return
    if value_type == _TYPE_ARRAY:
        elem_type, count = struct.unpack("<IQ", _read(stream, 12))
        if elem_type == _TYPE_STRING:
            for _ in range(count):
                _read_string(stream)
            return
        size = _SCALAR_SIZES.get(elem_type)
        if size is None or count > GGUF_META_LIMIT:
            raise ValueError("GGUF 数组类型异常")
        stream.seek(size * count, 1)
        return
    size = _SCALAR_SIZES.get(value_type)
    if size is None:
        raise ValueError("GGUF 元数据类型异常")
    stream.seek(size, 1)


def _read_meta(path: Path) -> _GgufMeta:
    """解析 GGUF 头部，返回 file_type、主导张量类型与预期大小。"""
    with path.open("rb") as stream:
        if _read(stream, 4) != b"GGUF":
            raise ValueError("不是 GGUF 文件")
        version = struct.unpack("<I", _read(stream, 4))[0]
        if version < 2:
            raise ValueError("不支持的 GGUF 版本")
        tensor_count = struct.unpack("<Q", _read(stream, 8))[0]
        kv_count = struct.unpack("<Q", _read(stream, 8))[0]

        file_type: int | None = None
        alignment = 32
        for _ in range(kv_count):
            key = _read_string(stream)
            value_type = struct.unpack("<I", _read(stream, 4))[0]
            if value_type == _TYPE_UINT32 and key in ("general.file_type", "general.alignment"):
                (value,) = struct.unpack("<I", _read(stream, 4))
                if key == "general.file_type":
                    file_type = value
                elif value > 0:
                    alignment = value
            else:
                _skip_value(stream, value_type)
            if stream.tell() > GGUF_META_LIMIT:
                raise ValueError("GGUF 元数据过大")

        elems_by_type: dict[str, int] = {}
        max_end = 0
        fixed_width = True
        for _ in range(tensor_count):
            _read_string(stream)
            (n_dims,) = struct.unpack("<I", _read(stream, 4))
            dims = struct.unpack(f"<{n_dims}Q", _read(stream, 8 * n_dims))
            (type_id,) = struct.unpack("<I", _read(stream, 4))
            (offset,) = struct.unpack("<Q", _read(stream, 8))
            name = _TENSOR_TYPE_NAMES.get(type_id, f"type{type_id}")
            elems = math.prod(dims)
            elems_by_type[name] = elems_by_type.get(name, 0) + elems
            elem_bytes = _ELEMENT_BYTES.get(name)
            if elem_bytes is None:
                fixed_width = False
            else:
                max_end = max(max_end, offset + elems * elem_bytes)

        dominant = max(elems_by_type.items(), key=lambda item: item[1])[0] if elems_by_type else None
        expected_bytes: int | None = None
        if fixed_width and tensor_count > 0:
            expected_bytes = (stream.tell() + alignment - 1) // alignment * alignment + max_end
    return _GgufMeta(file_type=file_type, tensor_type=dominant, expected_bytes=expected_bytes)


def _filename_marker(stem: str) -> tuple[bool, str] | None:
    """从文件名提取（量化类别, 精度标记）；没有标记时返回 ``None``。"""
    matches = _FILENAME_MARKER_RE.findall(stem)
    if not matches:
        return None
    marker = matches[-1].upper()
    return marker not in _FULL_TENSOR_TYPES, marker


def _label_for(name: str, quantized: bool) -> str:
    return f"{name} 量化" if quantized else f"{name} 全量，未量化"


def detect_precision(path: Path) -> GgufPrecision:
    """识别 GGUF 是全量精度还是量化版本，并给出括号备注文本。

    读取失败（文件不存在、头部损坏、非 GGUF）时回退到文件名标记；都没有
    则返回“精度未知”，不会抛异常。
    """
    target = Path(path)
    try:
        actual_bytes: int | None = target.stat().st_size
    except OSError:
        actual_bytes = None
    marker = _filename_marker(target.stem)

    try:
        meta = _read_meta(target)
    except (OSError, ValueError, struct.error):
        meta = None

    if meta is not None:
        name = _FILE_TYPE_NAMES.get(meta.file_type) if meta.file_type is not None else None
        if name is not None and meta.file_type is not None:
            quantized = meta.file_type not in _FULL_FILE_TYPES
            label = _label_for(name, quantized)
            source: PrecisionSource = "metadata"
            if meta.tensor_type is not None:
                tensor_quantized = meta.tensor_type not in _FULL_TENSOR_TYPES
                if tensor_quantized != quantized:
                    # 元数据与张量类型矛盾时以实际张量为准
                    label = _label_for(meta.tensor_type, tensor_quantized)
                    quantized = tensor_quantized
                    source = "tensors"
            return GgufPrecision(
                label=label,
                quantized=quantized,
                source=source,
                filename_label=marker[1] if marker is not None else None,
                filename_quantized=marker[0] if marker is not None else None,
                expected_bytes=meta.expected_bytes,
                actual_bytes=actual_bytes,
            )
        if meta.tensor_type is not None:
            quantized = meta.tensor_type not in _FULL_TENSOR_TYPES
            return GgufPrecision(
                label=_label_for(meta.tensor_type, quantized),
                quantized=quantized,
                source="tensors",
                filename_label=marker[1] if marker is not None else None,
                filename_quantized=marker[0] if marker is not None else None,
                expected_bytes=meta.expected_bytes,
                actual_bytes=actual_bytes,
            )

    if marker is not None:
        quantized, name = marker
        return GgufPrecision(
            label=_label_for(name, quantized),
            quantized=quantized,
            source="filename",
            filename_label=name,
            filename_quantized=quantized,
            actual_bytes=actual_bytes,
        )
    return GgufPrecision(label="精度未知", quantized=None, source="unknown", actual_bytes=actual_bytes)


def quantize_input_error(precision: GgufPrecision) -> str | None:
    """判断能否作为量化输入；不能时返回给用户看的原因，可以时返回 ``None``。"""
    if precision.quantized is True:
        return f"已是量化版（{precision.label}），重复量化只会损失质量。请选择 F16 / BF16 等全量精度 GGUF。"
    if precision.incomplete and precision.expected_bytes is not None and precision.actual_bytes is not None:
        return (
            "文件疑似不完整"
            f"（{human_size(precision.actual_bytes)} / 预期 {human_size(precision.expected_bytes)}），"
            "请重新下载后再转换。"
        )
    return None
