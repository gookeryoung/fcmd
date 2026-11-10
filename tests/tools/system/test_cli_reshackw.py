"""reshackw 工具测试。

验证 ``fcmd.tools.system.reshackw``：
- 设置读写（路径、最近打开、几何串）容错
- 文本解码、十六进制预览、资源分组、默认文件名
- 图像预览解码（BMP / ICO 包装）
- 工具注册与 ``run_gui`` 的 ImportError 降级
"""

from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.system import reshackw
from fcmd.tools.system.reshack import ResourceEntry
from fcmd.tools.system.reshackw import (
    FIT_MAX_UPSCALE,
    MAX_ZOOM,
    MIN_ZOOM,
    ZOOM_STEP,
    ReshackSettings,
    add_recent_file,
    clamp_zoom,
    config_path,
    decode_text,
    default_export_name,
    fit_zoom,
    format_hex_preview,
    group_resources,
    image_to_dib,
    is_text_resource,
    load_preview_image,
    load_settings,
    resolve_geometry,
    save_settings,
    valid_geometry,
    zoom_in,
    zoom_out,
)


def _resource(
    type_id: int,
    name_id: int,
    language_id: int = 0,
    data: bytes = b"",
) -> ResourceEntry:
    return ResourceEntry(
        type_id=type_id,
        type_name={3: "Icon", 16: "Version", 24: "Manifest"}.get(type_id, f"T{type_id}"),
        name_id=name_id,
        language_id=language_id,
        offset=0x2000,
        size=len(data),
        data=data,
    )


# ============================================================================ #
# 注册
# ============================================================================ #
def test_tool_registered() -> None:
    """reshackw 应为单命令 GUI 工具。"""
    assert set(_TOOL_REGISTRY["reshackw"]) == {None}


# ============================================================================ #
# 配置持久化
# ============================================================================ #
class TestConfig:
    """设置文件路径与读写。"""

    def test_config_path_uses_fcmd_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert config_path() == tmp_path / "ui" / "reshackw.json"

    def test_config_path_default_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FCMD_HOME", raising=False)
        assert config_path() == Path.home() / ".fcmd" / "ui" / "reshackw.json"

    @pytest.mark.parametrize(
        ("text", "valid"),
        [
            ("1080x720", True),
            ("1080x720+120+80", True),
            ("1080x720-120-80", True),
            ("1080", False),
            ("abc", False),
            ("1080x720+", False),
            (None, False),
        ],
    )
    def test_valid_geometry(self, text: str | None, valid: bool) -> None:
        assert (valid_geometry(text) is not None) is valid

    def test_add_recent_file(self) -> None:
        a, b, c = (str(Path("/a.exe")), str(Path("/b.exe")), str(Path("/c.exe")))
        assert add_recent_file([a, b], Path(c)) == [c, a, b]


class TestResolveGeometry:
    """启动时 geometry 恢复：位置夹回屏幕、尺寸不小于下限。"""

    def test_keeps_visible_position(self) -> None:
        assert resolve_geometry("1000x680+100+80", 1920, 1080) == "1000x680+100+80"

    def test_pulls_back_offscreen(self) -> None:
        assert resolve_geometry("1080x720+5000+5000", 1920, 1080) == "1080x720+840+360"

    def test_enforces_minimum_size(self) -> None:
        assert resolve_geometry("200x100+0+0", 1920, 1080) == "900x600+0+0"

    def test_falls_back_to_default_geometry(self) -> None:
        assert resolve_geometry(None, 1920, 1080) == "1080x720+180+120"
        assert resolve_geometry("bad", 1920, 1080) == "1080x720+180+120"

    def test_clamps_default_into_small_screen(self) -> None:
        assert resolve_geometry(None, 800, 600) == "800x600+0+0"

    def test_add_recent_file_dedupes(self) -> None:
        a, b = str(Path("/a.exe")), str(Path("/b.exe"))
        assert add_recent_file([a, b], Path(b)) == [b, a]

    def test_add_recent_file_limit(self) -> None:
        files = [str(Path(f"/{index}.exe")) for index in range(3)]
        expected = str(Path("/new.exe"))
        result = add_recent_file(files, Path("/new.exe"), limit=2)
        assert result == [expected, files[0]]

    def test_load_settings_missing(self, tmp_path: Path) -> None:
        assert load_settings(tmp_path / "none.json") == ReshackSettings()

    def test_load_settings_bad_json(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{bad", encoding="utf-8")
        assert load_settings(path) == ReshackSettings()

    def test_load_settings_non_object(self, tmp_path: Path) -> None:
        path = tmp_path / "list.json"
        path.write_text("[1, 2]", encoding="utf-8")
        assert load_settings(path) == ReshackSettings()

    def test_load_settings_valid(self, tmp_path: Path) -> None:
        path = tmp_path / "ok.json"
        path.write_text(
            json.dumps(
                {
                    "file_path": "/app.exe",
                    "recent_files": ["/a.exe", 42],
                    "geometry": "900x600+1+2",
                }
            ),
            encoding="utf-8",
        )
        settings = load_settings(path)
        assert settings.file_path == "/app.exe"
        assert settings.recent_files == ["/a.exe"]
        assert settings.geometry == "900x600+1+2"

    def test_load_settings_invalid_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "invalid.json"
        path.write_text(
            json.dumps(
                {
                    "file_path": 42,
                    "recent_files": "not-a-list",
                    "geometry": "bad-geometry",
                }
            ),
            encoding="utf-8",
        )
        settings = load_settings(path)
        assert settings == ReshackSettings()

    def test_load_settings_caps_recent(self, tmp_path: Path) -> None:
        path = tmp_path / "many.json"
        recent = [f"/{index}.exe" for index in range(20)]
        path.write_text(json.dumps({"recent_files": recent}), encoding="utf-8")
        assert len(load_settings(path).recent_files) == reshackw.RECENT_LIMIT

    def test_save_settings_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "reshackw.json"
        settings = ReshackSettings(
            file_path="/app.exe",
            recent_files=["/app.exe"],
            geometry="900x600+1+2",
        )
        save_settings(settings, path)
        assert load_settings(path) == settings

    def test_save_settings_silent_on_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_args: Any, **_kwargs: Any) -> None:
            raise OSError("readonly")

        monkeypatch.setattr(Path, "write_text", boom)
        save_settings(ReshackSettings(), tmp_path / "x.json")


# ============================================================================ #
# 预览逻辑
# ============================================================================ #
class TestDecodeText:
    """文本资源解码。"""

    def test_empty(self) -> None:
        assert decode_text(b"") == "(空资源)"

    def test_utf8(self) -> None:
        assert decode_text("你好".encode()) == "你好"

    def test_utf16_bom(self) -> None:
        assert decode_text("hello".encode("utf-16")) == "hello"

    def test_utf16_without_bom(self) -> None:
        assert decode_text("hello".encode("utf-16-le")) == "hello"

    def test_gbk(self) -> None:
        assert decode_text("你好".encode("gbk")) == "你好"

    def test_latin1_fallback(self) -> None:
        text = decode_text(b"\x81\x20\x80")
        assert isinstance(text, str)

    def test_invalid_utf16_bom_falls_back(self) -> None:
        text = decode_text(b"\xff\xfe\x00")
        assert isinstance(text, str)


class TestHexPreview:
    """十六进制预览。"""

    def test_empty(self) -> None:
        assert format_hex_preview(b"") == "(空资源)"

    def test_ascii_column(self) -> None:
        text = format_hex_preview(b"AB")
        assert "41 42" in text
        assert text.endswith("AB")

    def test_truncation(self) -> None:
        text = format_hex_preview(b"A" * 32, limit=16)
        assert "仅显示前 16 字节，共 32 字节" in text
        assert text.count("\n") == 1

    def test_non_printable(self) -> None:
        assert "." in format_hex_preview(b"\x00\x01")


class TestGroupAndNames:
    """资源分组与默认文件名。"""

    def test_is_text_resource(self) -> None:
        assert is_text_resource(24) is True
        assert is_text_resource(3) is False

    def test_group_resources(self) -> None:
        resources = [
            _resource(24, 1),
            _resource(3, 2),
            _resource(3, 1, language_id=1033),
            _resource(3, 1),
        ]
        grouped = group_resources(resources)
        assert [item[0] for item in grouped] == [3, 24]
        assert grouped[0][1] == "Icon"
        assert [(item.name_id, item.language_id) for item in grouped[0][2]] == [
            (1, 0),
            (1, 1033),
            (2, 0),
        ]

    def test_default_export_name(self) -> None:
        assert default_export_name(_resource(3, 1)) == "Icon_1_0.ico"
        assert default_export_name(_resource(24, 2, 1033)) == "Manifest_2_1033.xml"


# ============================================================================ #
# 预览缩放
# ============================================================================ #
class TestZoom:
    """缩放倍率计算。"""

    def test_clamp_zoom(self) -> None:
        assert clamp_zoom(1.0) == 1.0
        assert clamp_zoom(0.001) == MIN_ZOOM
        assert clamp_zoom(999.0) == MAX_ZOOM

    def test_zoom_in_and_out(self) -> None:
        assert zoom_in(1.0) == pytest.approx(ZOOM_STEP)
        assert zoom_out(1.0) == pytest.approx(1 / ZOOM_STEP)
        assert zoom_in(MAX_ZOOM) == MAX_ZOOM
        assert zoom_out(MIN_ZOOM) == MIN_ZOOM

    def test_fit_zoom_downscales(self) -> None:
        assert fit_zoom(800, 600, 400, 300) == pytest.approx(0.5)

    def test_fit_zoom_caps_upscale(self) -> None:
        assert fit_zoom(16, 16, 1000, 1000) == pytest.approx(FIT_MAX_UPSCALE)

    def test_fit_zoom_respects_max_upscale(self) -> None:
        assert fit_zoom(10, 10, 1000, 1000, max_upscale=3.0) == pytest.approx(3.0)

    def test_fit_zoom_floor(self) -> None:
        assert fit_zoom(10000, 10000, 10, 10) == MIN_ZOOM

    @pytest.mark.parametrize(
        ("image_width", "image_height", "view_width", "view_height"),
        [
            (0, 10, 100, 100),
            (10, 0, 100, 100),
            (10, 10, 0, 100),
            (10, 10, 100, 0),
        ],
    )
    def test_fit_zoom_invalid_dimensions(
        self, image_width: int, image_height: int, view_width: int, view_height: int
    ) -> None:
        assert fit_zoom(image_width, image_height, view_width, view_height) == 1.0


# ============================================================================ #
# 图像预览
# ============================================================================ #
class TestImagePreview:
    """BMP / ICO 资源图像解码。"""

    def test_empty(self) -> None:
        assert load_preview_image(b"", 3) is None

    def test_unsupported_bytes(self) -> None:
        assert load_preview_image(b"not an image", 3) is None

    def test_short_bitmap_header(self) -> None:
        assert load_preview_image(b"BM", 2) is None

    def test_invalid_bitmap_header_size(self) -> None:
        data = (12).to_bytes(4, "little") + b"\x00" * 40
        assert load_preview_image(data, 2) is None

    def test_invalid_icon_dimensions(self) -> None:
        data = bytearray(40)
        data[4:8] = (0).to_bytes(4, "little", signed=True)
        assert load_preview_image(bytes(data), 3) is None

    def test_short_icon_header(self) -> None:
        assert load_preview_image(b"x" * 20, 3) is None

    def test_no_pillow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "PIL", None)
        assert load_preview_image(b"data", 24) is None

    def test_bitmap_preview(self) -> None:
        image_module = pytest.importorskip("PIL.Image")
        buffer = BytesIO()
        image_module.new("RGB", (4, 4), "red").save(buffer, format="BMP")
        dib = buffer.getvalue()[14:]
        image = load_preview_image(dib, 2)
        assert image is not None
        assert image.size == (4, 4)

    @pytest.mark.filterwarnings("ignore:Image was not the expected size")
    def test_icon_preview(self) -> None:
        image_module = pytest.importorskip("PIL.Image")
        buffer = BytesIO()
        image_module.new("RGBA", (32, 32), (255, 0, 0, 255)).save(buffer, format="ICO", sizes=[(32, 32)])
        ico = buffer.getvalue()
        offset = int.from_bytes(ico[18:22], "little")
        size = int.from_bytes(ico[14:18], "little")
        image = load_preview_image(ico[offset : offset + size], 3)
        assert image is not None

    def test_png_in_raw_data(self) -> None:
        image_module = pytest.importorskip("PIL.Image")
        buffer = BytesIO()
        image_module.new("RGB", (2, 2), "blue").save(buffer, format="PNG")
        image = load_preview_image(buffer.getvalue(), 24)
        assert image is not None


# ============================================================================ #
# 剪贴板数据
# ============================================================================ #
class TestImageToDib:
    """预览图像转 Windows CF_DIB。"""

    def test_rgb_image(self) -> None:
        image_module = pytest.importorskip("PIL.Image")
        image = image_module.new("RGB", (3, 2), "red")
        dib = image_to_dib(image)
        assert dib is not None
        assert dib[:4] == (40).to_bytes(4, "little")
        assert int.from_bytes(dib[4:8], "little") == 3
        assert int.from_bytes(dib[8:12], "little") == 2

    def test_rgba_image_is_flattened_to_rgb(self) -> None:
        image_module = pytest.importorskip("PIL.Image")
        image = image_module.new("RGBA", (2, 2), (255, 0, 0, 128))
        dib = image_to_dib(image)
        assert dib is not None
        assert int.from_bytes(dib[14:16], "little") == 24

    def test_invalid_object(self) -> None:
        assert image_to_dib(object()) is None

    def test_non_bmp_output(self) -> None:
        class FakeImage:
            def convert(self, _mode: str) -> FakeImage:
                return self

            def save(self, buffer: Any, **_kwargs: Any) -> None:
                buffer.write(b"not a bitmap")

        assert image_to_dib(FakeImage()) is None


# ============================================================================ #
# GUI 入口
# ============================================================================ #
def test_run_gui_import_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """缺少 tkinter 时输出中文提示且不抛堆栈。"""

    def boom(_settings_path: Path | None = None) -> None:
        raise ImportError("no tkinter")

    monkeypatch.setattr(reshackw, "ReshackWindow", boom)
    reshackw.run_gui()
    assert "tkinter 不可用" in capsys.readouterr().out


def test_run_gui_starts_mainloop(monkeypatch: pytest.MonkeyPatch) -> None:
    """窗口创建成功后进入 mainloop。"""
    calls: list[str] = []

    class FakeWindow:
        def __init__(self, _settings_path: Path | None = None) -> None:
            self.root = self

        def mainloop(self) -> None:
            calls.append("mainloop")

    monkeypatch.setattr(reshackw, "ReshackWindow", FakeWindow)
    reshackw.run_gui()
    assert calls == ["mainloop"]


def test_launch_cmd_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(reshackw, "run_gui", lambda: calls.append("run"))
    reshackw.launch_cmd()
    assert calls == ["run"]
