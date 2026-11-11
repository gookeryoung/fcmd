"""GGUF 精度探测测试。"""

from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

from fcmd.tools.llm import _llmgguf
from fcmd.tools.llm._llmgguf import detect_precision


def _string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack("<Q", len(encoded)) + encoded


def _kv_str(key: str, value: str) -> bytes:
    return _string(key) + struct.pack("<I", 8) + _string(value)


def _kv_u32(key: str, value: int) -> bytes:
    return _string(key) + struct.pack("<I", 4) + struct.pack("<I", value)


def _kv_str_array(key: str, values: list[str]) -> bytes:
    body = struct.pack("<I", 8) + struct.pack("<Q", len(values))
    return _string(key) + struct.pack("<I", 9) + body + b"".join(_string(value) for value in values)


def _kv_u32_array(key: str, values: list[int]) -> bytes:
    body = struct.pack("<I", 4) + struct.pack("<Q", len(values)) + b"".join(struct.pack("<I", v) for v in values)
    return _string(key) + struct.pack("<I", 9) + body


def _kv_f32(key: str, value: float) -> bytes:
    return _string(key) + struct.pack("<I", 6) + struct.pack("<f", value)


_TENSOR_ELEMENT_BYTES = {0: 4, 1: 2, 30: 2}


def _write_gguf(  # noqa: PLR0913
    path: Path,
    *,
    file_type: int | None = 1,
    tensors: list[tuple[str, tuple[int, ...], int, int]] | None = None,
    extra_kv: list[bytes] | None = None,
    version: int = 3,
    alignment: int = 32,
    data_bytes: int | None = None,
    magic: bytes = b"GGUF",
) -> None:
    if tensors is None:
        tensors = [("blk.0.weight", (8,), 1, 0)]
    key_values = [_kv_str("general.architecture", "qwen3moe")]
    if file_type is not None:
        key_values.append(_kv_u32("general.file_type", file_type))
    key_values.extend(extra_kv or [])

    buffer = bytearray(magic)
    buffer += struct.pack("<I", version)
    buffer += struct.pack("<Q", len(tensors))
    buffer += struct.pack("<Q", len(key_values))
    for item in key_values:
        buffer += item
    for name, dims, type_id, offset in tensors:
        buffer += _string(name)
        buffer += struct.pack("<I", len(dims))
        for dim in dims:
            buffer += struct.pack("<Q", dim)
        buffer += struct.pack("<I", type_id)
        buffer += struct.pack("<Q", offset)
    buffer += b"\0" * ((-len(buffer)) % alignment)

    if data_bytes is None:
        end = 0
        for _, dims, type_id, offset in tensors:
            elem_bytes = _TENSOR_ELEMENT_BYTES.get(type_id)
            if elem_bytes is None:
                end = 0
                break
            end = max(end, offset + math.prod(dims) * elem_bytes)
        data_bytes = end
    buffer += b"\0" * data_bytes
    path.write_bytes(bytes(buffer))


def test_f16_full_precision_is_not_flagged_as_quantized(tmp_path: Path) -> None:
    path = tmp_path / "Qwen3-Coder-30B-A3B-Instruct-f16.gguf"
    _write_gguf(path, file_type=1, tensors=[("w", (100,), 1, 0), ("norm", (10,), 0, 200)])

    result = detect_precision(path)

    assert result.label == "F16 全量，未量化"
    assert result.quantized is False
    assert result.source == "metadata"
    assert not result.mismatch
    assert not result.incomplete
    assert result.note == "F16 全量，未量化"
    assert "GGUF 元数据" in result.detail


def test_bf16_full_precision(tmp_path: Path) -> None:
    path = tmp_path / "model-BF16.gguf"
    _write_gguf(path, file_type=30, tensors=[("w", (16,), 30, 0)])

    result = detect_precision(path)

    assert result.label == "BF16 全量，未量化"
    assert result.quantized is False
    assert result.detail.startswith("BF16 全量，未量化")


def test_q4_k_m_is_reported_as_quantized(tmp_path: Path) -> None:
    path = tmp_path / "model-Q4_K_M.gguf"
    _write_gguf(path, file_type=15, tensors=[("w", (256,), 12, 0)])

    result = detect_precision(path)

    assert result.label == "Q4_K_M 量化"
    assert result.quantized is True
    assert result.expected_bytes is None
    assert result.note == "Q4_K_M 量化"


def test_filename_claiming_f16_but_quantized_is_flagged(tmp_path: Path) -> None:
    path = tmp_path / "model-f16.gguf"
    _write_gguf(path, file_type=15, tensors=[("w", (256,), 12, 0)])

    result = detect_precision(path)

    assert result.label == "Q4_K_M 量化"
    assert result.quantized is True
    assert result.mismatch
    assert result.note == "Q4_K_M 量化；文件名标注 F16"


def test_truncated_full_precision_file_is_flagged(tmp_path: Path) -> None:
    path = tmp_path / "model-f16.gguf"
    _write_gguf(path, file_type=1, tensors=[("w", (1000,), 1, 0)], data_bytes=0)

    result = detect_precision(path)

    assert result.label == "F16 全量，未量化"
    assert result.incomplete
    assert "文件疑似不完整" in result.note
    assert "预期" in result.note


def test_metadata_conflict_prefers_tensor_type(tmp_path: Path) -> None:
    path = tmp_path / "model-f16.gguf"
    _write_gguf(path, file_type=1, tensors=[("w", (256,), 12, 0)])

    result = detect_precision(path)

    assert result.label == "Q4_K 量化"
    assert result.quantized is True
    assert result.source == "tensors"
    assert result.mismatch


def test_missing_file_falls_back_to_filename(tmp_path: Path) -> None:
    result = detect_precision(tmp_path / "model-Q5_K_M.gguf")

    assert result.label == "Q5_K_M 量化"
    assert result.quantized is True
    assert result.source == "filename"
    assert result.actual_bytes is None
    assert result.note == "Q5_K_M 量化"


def test_invalid_header_falls_back_to_filename(tmp_path: Path) -> None:
    path = tmp_path / "model-IQ2_XS.gguf"
    path.write_bytes(b"not a gguf")

    result = detect_precision(path)

    assert result.label == "IQ2_XS 量化"
    assert result.source == "filename"
    assert result.actual_bytes == 10


def test_non_gguf_without_marker_is_unknown(tmp_path: Path) -> None:
    path = tmp_path / "model.gguf"
    path.write_bytes(b"not a gguf")

    result = detect_precision(path)

    assert result.label == "精度未知"
    assert result.quantized is None
    assert result.source == "unknown"
    assert result.note == "精度未知"
    assert "无法识别" in result.detail


def test_unsupported_version_falls_back_to_filename(tmp_path: Path) -> None:
    path = tmp_path / "model-f16.gguf"
    _write_gguf(path, version=1)

    result = detect_precision(path)

    assert result.label == "F16 全量，未量化"
    assert result.source == "filename"


def test_missing_file_type_uses_tensor_type(tmp_path: Path) -> None:
    path = tmp_path / "model.gguf"
    _write_gguf(path, file_type=None, tensors=[("w", (32,), 1, 0)])

    result = detect_precision(path)

    assert result.label == "F16 全量，未量化"
    assert result.source == "tensors"
    assert not result.incomplete


def test_metadata_arrays_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "model-f16.gguf"
    _write_gguf(
        path,
        file_type=1,
        extra_kv=[
            _kv_str_array("tokenizer.ggml.tokens", ["a", "bb", "ccc"]),
            _kv_u32_array("tokenizer.ggml.token_ids", [1, 2, 3]),
        ],
        tensors=[("w", (64,), 1, 0)],
    )

    result = detect_precision(path)

    assert result.label == "F16 全量，未量化"
    assert result.source == "metadata"
    assert not result.incomplete


def test_alignment_padding_is_accounted_for(tmp_path: Path) -> None:
    path = tmp_path / "model-f16.gguf"
    _write_gguf(path, file_type=1, tensors=[("w", (16,), 1, 0)], alignment=64)

    result = detect_precision(path)

    assert result.label == "F16 全量，未量化"
    assert not result.incomplete


def test_file_type_without_tensors_uses_metadata(tmp_path: Path) -> None:
    path = tmp_path / "model-Q6_K.gguf"
    _write_gguf(path, file_type=18, tensors=[])

    result = detect_precision(path)

    assert result.label == "Q6_K 量化"
    assert result.quantized is True
    assert result.source == "metadata"
    assert result.expected_bytes is None


def test_invalid_metadata_value_type_falls_back_to_filename(tmp_path: Path) -> None:
    path = tmp_path / "model-Q3_K_M.gguf"
    _write_gguf(path, file_type=None, extra_kv=[_string("bad.key") + struct.pack("<I", 99)])

    result = detect_precision(path)

    assert result.source == "filename"
    assert result.label == "Q3_K_M 量化"


def test_unsupported_array_element_type_falls_back_to_filename(tmp_path: Path) -> None:
    path = tmp_path / "model-IQ4_XS.gguf"
    array_value = struct.pack("<I", 99) + struct.pack("<Q", 1)
    _write_gguf(path, file_type=None, extra_kv=[_string("bad.array") + struct.pack("<I", 9) + array_value])

    result = detect_precision(path)

    assert result.source == "filename"
    assert result.label == "IQ4_XS 量化"


def test_truncated_header_falls_back_to_filename(tmp_path: Path) -> None:
    path = tmp_path / "model-Q2_K.gguf"
    path.write_bytes(b"GGUF\x03\x00")

    result = detect_precision(path)

    assert result.source == "filename"
    assert result.label == "Q2_K 量化"


def test_oversized_string_length_falls_back_to_filename(tmp_path: Path) -> None:
    path = tmp_path / "model-BF16.gguf"
    payload = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", 1)
    payload += struct.pack("<Q", _llmgguf.GGUF_META_LIMIT + 1)
    path.write_bytes(payload)

    result = detect_precision(path)

    assert result.source == "filename"
    assert result.label == "BF16 全量，未量化"


def test_scalar_metadata_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "model-f16.gguf"
    _write_gguf(path, file_type=1, extra_kv=[_kv_f32("general.some.float", 1.5)])

    result = detect_precision(path)

    assert result.label == "F16 全量，未量化"
    assert result.source == "metadata"


def test_custom_alignment_is_honored(tmp_path: Path) -> None:
    path = tmp_path / "model-f16.gguf"
    _write_gguf(
        path,
        file_type=1,
        extra_kv=[_kv_u32("general.alignment", 64)],
        alignment=64,
        tensors=[("w", (16,), 1, 0)],
    )

    result = detect_precision(path)

    assert not result.incomplete
    assert result.expected_bytes == path.stat().st_size


def test_unknown_file_type_without_tensors_falls_back_to_filename(tmp_path: Path) -> None:
    path = tmp_path / "model-Q8_0.gguf"
    _write_gguf(path, file_type=999, tensors=[])

    result = detect_precision(path)

    assert result.source == "filename"
    assert result.label == "Q8_0 量化"


def test_oversized_metadata_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_llmgguf, "GGUF_META_LIMIT", 80)
    path = tmp_path / "model-Q4_0.gguf"
    _write_gguf(
        path,
        file_type=1,
        extra_kv=[_kv_str_array("tokenizer.ggml.tokens", ["a", "bb", "ccc"])],
    )

    result = detect_precision(path)

    assert result.source == "filename"
    assert result.label == "Q4_0 量化"
