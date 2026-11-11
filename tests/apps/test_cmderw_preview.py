"""预览增强纯逻辑测试：编码探测、十六进制与缩放倍率。"""

from __future__ import annotations

import codecs

import pytest

from fcmd.apps.cmderw import _cmderw_preview as preview


class TestDetectEncoding:
    def test_utf8(self) -> None:
        assert preview.detect_encoding("中文".encode()) == "utf-8"

    def test_utf8_bom(self) -> None:
        assert preview.detect_encoding(codecs.BOM_UTF8 + b"x") == "utf-8-sig"

    def test_utf16_bom(self) -> None:
        assert preview.detect_encoding(codecs.BOM_UTF16_LE + "x".encode("utf-16-le")) == "utf-16"

    def test_gb18030_when_not_utf8(self) -> None:
        data = "中文".encode("gb18030")
        assert preview.detect_encoding(data) == "gb18030"

    def test_fallback_latin1(self) -> None:
        assert preview.detect_encoding(b"\xfa\xff") == "latin-1"

    def test_has_text_bom(self) -> None:
        assert preview.has_text_bom(codecs.BOM_UTF8 + b"x")
        assert preview.has_text_bom(codecs.BOM_UTF16_LE + b"x")
        assert not preview.has_text_bom(b"plain")


class TestDecodeText:
    def test_auto_and_explicit(self) -> None:
        data = "中文".encode("gb18030")
        assert preview.decode_text(data) == "中文"
        assert preview.decode_text(data, "gb18030") == "中文"

    def test_replaces_invalid_bytes(self) -> None:
        assert preview.decode_text(b"\xff", "utf-8") == "\ufffd"

    def test_unknown_encoding_raises(self) -> None:
        with pytest.raises(ValueError, match="未知编码"):
            preview.decode_text(b"x", "nope")


class TestHexDump:
    def test_layout(self) -> None:
        row = preview.hex_dump(b"AB")
        assert row == f"{0:08x}  {'41 42':<47}  AB"

    def test_limit(self) -> None:
        text = preview.hex_dump(b"A" * 40, limit=16)
        assert text.count("\n") == 0
        assert text.startswith("00000000  41")

    def test_empty(self) -> None:
        assert preview.hex_dump(b"") == ""


class TestZoom:
    def test_fit_factor(self) -> None:
        assert preview.fit_factor(100, 100, 640, 480) == 1
        assert preview.fit_factor(1280, 100, 640, 480) == 2
        assert preview.fit_factor(100, 960, 640, 480) == 2
        assert preview.fit_factor(1280, 960, 640, 480) == 2
        assert preview.fit_factor(0, 10, 640, 480) == 1
        assert preview.fit_factor(10, 10, 0, 0) == 1

    def test_zoom_steps(self) -> None:
        assert preview.zoom_in(4) == 2
        assert preview.zoom_in(1) == 1
        assert preview.zoom_out(1) == 2
        assert preview.zoom_out(8) == 16
        assert preview.zoom_out(16) == preview.ZOOM_MAX

    def test_labels(self) -> None:
        assert preview.zoom_label(1) == "1:1"
        assert preview.zoom_label(2) == "1:2"
        assert preview.zoom_label(0) == "1:1"
