"""ncmconvertw 工具测试。

验证 ``fcmd.tools.media.ncmconvertw`` 模块：
- 工具注册
- 设置文件读写与异常回退
- 窗口 geometry 校验
- NCM 头解析、密钥盒 / 元数据 / 音频解密
- 合成 NCM 文件的端到端解锁
- GUI 启动入口的降级行为
"""

from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.media import ncmconvertw
from fcmd.tools.media.ncmconvertw import (
    CORE_KEY,
    MAGIC,
    META_KEY,
    META_PREFIX,
    NcmConvertSettings,
    NcmError,
    build_key_box,
    build_output_path,
    collect_ncm_files,
    config_path,
    decrypt_audio,
    decrypt_metadata,
    decrypt_ncm,
    load_settings,
    read_header,
    save_settings,
    valid_geometry,
)

KEY_STREAM = bytes(range(16))
KEY_BOX_HEX = (
    "91955d8d5897253c0755185f8f37b1e370870115e5086d8cac3eeb1929d60c34"
    "c1a2c3217c6017ba4b39038632ee5068aab3fe43401c4e8bf32a92bf6c59931a8"
    "3d5c6c0cda54a9bbe7ed2ea80da6a7d2c4485f90428b946c996315cc20575bcb"
    "40988a878351d724942ccc4733633a06482f69fe4169e74147f6eb20d9a3fa70e"
    "67273b1e20841b9ca9a1f44d0b24c838a6cbd0b81f66bb54fd8a81cec74c22d8"
    "ae0fe85e260061d9416fcfad6b94e071a362e7e91265d42d8eedf75a767af2ec9"
    "83057d3ab45fa2f1347f1b67bf8df9d521069dc3a63fbdbf0020677f5e1e689a4"
    "ff4f2e9053cab5efde2399d1bdddd7792b3dafe2b711fc0ac55156b0485b"
)
AUDIO_XOR_HEX = (
    "513fa9e831ca50d94f0f8c7b54e5bf30167ca6cef5ccf2b0063e8ce309fef02a"
    "2ad89f272a4153f9b050134a10e50f8294e8964f8046c1482887249ecead4f3d"
)


# ============================================================================ #
# 合成 NCM 文件
# ============================================================================ #
def _aes_ecb_encrypt(data: bytes, key: bytes) -> bytes:
    """测试辅助：AES-128-ECB 加密（依赖 cryptography）。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def _pkcs7_pad(data: bytes) -> bytes:
    """测试辅助：PKCS#7 补齐到 16 字节整数倍。"""
    pad = 16 - len(data) % 16
    return data + bytes([pad]) * pad


def _xor(data: bytes, key: int) -> bytes:
    """测试辅助：逐字节异或。"""
    return bytes(byte ^ key for byte in data)


def _encrypt_key(stream: bytes = KEY_STREAM) -> bytes:
    """测试辅助：构造文件头里的加密密钥字段。"""
    plain = _pkcs7_pad(ncmconvertw.KEY_PREFIX + stream)
    return _xor(_aes_ecb_encrypt(plain, CORE_KEY), 0x64)


def _encrypt_meta(metadata: dict[str, Any] | bytes) -> bytes:
    """测试辅助：构造文件头里的加密元数据字段。"""
    payload = metadata if isinstance(metadata, bytes) else json.dumps(metadata).encode("utf-8")
    plain = _pkcs7_pad(ncmconvertw.META_JSON_PREFIX + payload)
    encrypted = _aes_ecb_encrypt(plain, META_KEY)
    return _xor(META_PREFIX + base64.b64encode(encrypted), 0x63)


def _build_ncm(  # noqa: PLR0913 - 测试辅助需要显式覆盖各字段
    path: Path,
    *,
    metadata: dict[str, Any] | bytes | None = None,
    audio: bytes = b"",
    cover: bytes = b"",
    key_blob: bytes | None = None,
    meta_blob: bytes | None = None,
) -> bytes:
    """测试辅助：按 ncmdump.cpp 的布局写出一个 NCM 文件并返回字节。"""
    key_data = _encrypt_key() if key_blob is None else key_blob
    meta = {"format": "mp3", "musicName": "demo"} if metadata is None else metadata
    meta_data = _encrypt_meta(meta) if meta_blob is None else meta_blob
    payload = b"".join(
        (
            MAGIC,
            b"\x01\x70",
            len(key_data).to_bytes(4, "little"),
            key_data,
            len(meta_data).to_bytes(4, "little"),
            meta_data,
            b"\x00" * 4,
            b"\x00" * 5,
            len(cover).to_bytes(4, "little"),
            cover,
            audio,
        )
    )
    path.write_bytes(payload)
    return payload


def _expected_audio(audio: bytes) -> bytes:
    """测试辅助：用解密函数本身计算期望音频（分块与流式等价）。"""
    out = io.BytesIO()
    decrypt_audio(io.BytesIO(audio), out, build_key_box(_encrypt_key()))
    return out.getvalue()


# ============================================================================ #
# 注册验证
# ============================================================================ #
def test_tool_registered() -> None:
    """ncmconvertw 应以单命令形态注册。"""
    assert set(_TOOL_REGISTRY["ncmconvertw"]) == {None}


# ============================================================================ #
# 配置路径与设置文件
# ============================================================================ #
class TestSettings:
    """设置文件读写。"""

    def test_config_path_uses_fcmd_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """FCMD_HOME 优先。"""
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert config_path() == tmp_path / "ui" / "ncmconvertw.json"

    def test_config_path_falls_back_to_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """未设置 FCMD_HOME 时使用 ``~/.fcmd``。"""
        monkeypatch.delenv("FCMD_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert config_path() == tmp_path / ".fcmd" / "ui" / "ncmconvertw.json"

    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        assert load_settings(tmp_path / "missing.json") == NcmConvertSettings()

    def test_load_corrupt_json_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_settings(path) == NcmConvertSettings()

    def test_load_non_dict_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "list.json"
        path.write_text("[]", encoding="utf-8")
        assert load_settings(path) == NcmConvertSettings()

    def test_load_restores_values(self, tmp_path: Path) -> None:
        path = tmp_path / "ok.json"
        path.write_text(
            '{"output_dir": "out", "last_input_dir": "music", "geometry": "800x600+10+20"}',
            encoding="utf-8",
        )
        assert load_settings(path) == NcmConvertSettings(
            output_dir="out",
            last_input_dir="music",
            geometry="800x600+10+20",
        )

    def test_load_invalid_fields_fall_back(self, tmp_path: Path) -> None:
        """非法字段逐项回退默认值。"""
        path = tmp_path / "invalid.json"
        path.write_text('{"output_dir": 1, "last_input_dir": null, "geometry": "bad"}', encoding="utf-8")
        assert load_settings(path) == NcmConvertSettings()

    def test_save_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "settings.json"
        settings = NcmConvertSettings(output_dir="out", last_input_dir="music", geometry="800x600")
        save_settings(settings, path)
        assert load_settings(path) == settings

    def test_save_ignores_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """写入失败时静默忽略，不影响窗口关闭。"""

        def boom(*args: Any, **kwargs: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", boom)
        save_settings(NcmConvertSettings(), tmp_path / "x.json")  # 不应抛出


class TestValidGeometry:
    """窗口 geometry 校验。"""

    @pytest.mark.parametrize("text", ["720x480", "720x480+300+200", "800x600-10-20"])
    def test_valid(self, text: str) -> None:
        assert valid_geometry(text) == text

    @pytest.mark.parametrize("text", [None, "", "720", "720x480abc", "720 x 480"])
    def test_invalid(self, text: str | None) -> None:
        assert valid_geometry(text) is None


class TestResolveGeometry:
    """启动时 geometry 恢复：位置夹回屏幕、尺寸不小于下限。"""

    def test_keeps_visible_position(self) -> None:
        assert ncmconvertw.resolve_geometry("800x600+100+80", 1920, 1080) == "800x600+100+80"

    def test_pulls_back_offscreen(self) -> None:
        assert ncmconvertw.resolve_geometry("860x640+5000+5000", 1920, 1080) == "860x640+1060+440"

    def test_enforces_minimum_size(self) -> None:
        assert ncmconvertw.resolve_geometry("200x100+0+0", 1920, 1080) == "720x560+0+0"

    def test_falls_back_to_default_geometry(self) -> None:
        assert ncmconvertw.resolve_geometry(None, 1920, 1080) == "860x640+240+140"
        assert ncmconvertw.resolve_geometry("bad", 1920, 1080) == "860x640+240+140"

    def test_clamps_default_into_small_screen(self) -> None:
        assert ncmconvertw.resolve_geometry(None, 800, 600) == "800x600+0+0"


# ============================================================================ #
# 密钥盒与音频流
# ============================================================================ #
class TestKeyBox:
    """密钥盒构建。"""

    def test_known_vector(self) -> None:
        """固定密钥流应生成固定密钥盒（防止算法漂移）。"""
        assert build_key_box(_encrypt_key()).hex() == KEY_BOX_HEX

    def test_short_key_stream_rejected(self) -> None:
        """密钥流为空（只剩 17 字节前缀）时报错。"""
        with pytest.raises(NcmError, match="密钥数据过短"):
            build_key_box(_encrypt_key(b""))

    def test_bad_padding_rejected(self) -> None:
        """填充字节非法时报错。"""
        bogus = _xor(_aes_ecb_encrypt(b"\x00" * 15 + b"\x20", CORE_KEY), 0x64)
        with pytest.raises(NcmError, match="PKCS#7"):
            build_key_box(bogus)

    def test_padding_content_mismatch(self) -> None:
        """末字节声明的填充长度与内容不一致时报错。"""
        bogus = _xor(_aes_ecb_encrypt(b"\x00" * 14 + b"\x00\x02", CORE_KEY), 0x64)
        with pytest.raises(NcmError, match="内容不一致"):
            build_key_box(bogus)

    def test_empty_decrypted_data(self) -> None:
        """解密结果为空时报错。"""
        with pytest.raises(NcmError, match="为空"):
            ncmconvertw._pkcs7_unpad(b"")

    def test_crypto_dependency_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """缺少 cryptography 时给出安装提示。"""
        monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives.ciphers", None)
        with pytest.raises(NcmError, match="cryptography"):
            build_key_box(b"\x00" * 16)


class TestDecryptAudio:
    """音频流解密。"""

    def test_known_vector(self) -> None:
        key_box = build_key_box(_encrypt_key())
        out = io.BytesIO()
        assert decrypt_audio(io.BytesIO(bytes(range(64))), out, key_box) == 64
        assert out.getvalue().hex() == AUDIO_XOR_HEX

    def test_chunk_size_does_not_change_result(self) -> None:
        key_box = build_key_box(_encrypt_key())
        audio = bytes(range(250)) * 3
        whole = io.BytesIO()
        parts = io.BytesIO()
        decrypt_audio(io.BytesIO(audio), whole, key_box)
        decrypt_audio(io.BytesIO(audio), parts, key_box, chunk_size=7)
        assert whole.getvalue() == parts.getvalue()

    def test_empty_input(self) -> None:
        out = io.BytesIO()
        assert decrypt_audio(io.BytesIO(b""), out, bytes(256)) == 0
        assert out.getvalue() == b""

    def test_invalid_key_box_length(self) -> None:
        with pytest.raises(ValueError, match="256"):
            decrypt_audio(io.BytesIO(b"x"), io.BytesIO(), bytes(32))


# ============================================================================ #
# 元数据与文件头
# ============================================================================ #
class TestMetadata:
    """元数据解密。"""

    def test_roundtrip(self) -> None:
        assert decrypt_metadata(_encrypt_meta({"format": "flac", "musicName": "歌"})) == {
            "format": "flac",
            "musicName": "歌",
        }

    def test_wrong_prefix(self) -> None:
        with pytest.raises(NcmError, match="前缀"):
            decrypt_metadata(b"xxxx")

    def test_invalid_base64_payload(self) -> None:
        """Base64 解出的长度不是 16 的整数倍时报错。"""
        blob = _xor(META_PREFIX + b"AAAA", 0x63)
        with pytest.raises(NcmError, match="16"):
            decrypt_metadata(blob)

    def test_broken_base64_text(self) -> None:
        """Base64 文本本身无法解码时报错。"""
        blob = _xor(META_PREFIX + b"A", 0x63)
        with pytest.raises(NcmError, match="Base64"):
            decrypt_metadata(blob)

    def test_missing_music_prefix(self) -> None:
        """解密结果缺少 music: 前缀时报错。"""
        plain = _pkcs7_pad(b"not-music-json")
        blob = _xor(META_PREFIX + base64.b64encode(_aes_ecb_encrypt(plain, META_KEY)), 0x63)
        with pytest.raises(NcmError, match="music:"):
            decrypt_metadata(blob)

    def test_invalid_json(self) -> None:
        with pytest.raises(NcmError, match="JSON"):
            decrypt_metadata(_encrypt_meta(b"{not json"))

    def test_non_object_json(self) -> None:
        with pytest.raises(NcmError, match="JSON 对象"):
            decrypt_metadata(_encrypt_meta(b"[1, 2]"))


class TestReadHeader:
    """NCM 文件头解析。"""

    def test_reads_fields_and_stops_at_audio(self, tmp_path: Path) -> None:
        path = tmp_path / "song.ncm"
        audio = bytes(range(32))
        _build_ncm(path, audio=audio, cover=b"COVER")
        with path.open("rb") as fp:
            header = read_header(fp)
            assert fp.read() == audio
        assert header.audio_format == "mp3"
        assert header.metadata["musicName"] == "demo"
        assert header.key_box.hex() == KEY_BOX_HEX

    def test_normalizes_uppercase_format(self, tmp_path: Path) -> None:
        path = tmp_path / "song.ncm"
        _build_ncm(path, metadata={"format": "MP3"})
        with path.open("rb") as fp:
            assert read_header(fp).audio_format == "mp3"

    def test_bad_magic(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.ncm"
        path.write_bytes(b"NOTNCM!!" + b"\x00" * 32)
        with path.open("rb") as fp, pytest.raises(NcmError, match="文件头"):
            read_header(fp)

    def test_truncated_file(self, tmp_path: Path) -> None:
        path = tmp_path / "short.ncm"
        path.write_bytes(MAGIC)
        with path.open("rb") as fp, pytest.raises(NcmError, match="长度不足"):
            read_header(fp)

    def test_zero_key_length(self, tmp_path: Path) -> None:
        path = tmp_path / "zero.ncm"
        path.write_bytes(MAGIC + b"\x00" * 2 + b"\x00\x00\x00\x00")
        with path.open("rb") as fp, pytest.raises(NcmError, match="密钥长度"):
            read_header(fp)

    def test_oversized_key_length(self, tmp_path: Path) -> None:
        path = tmp_path / "big.ncm"
        path.write_bytes(MAGIC + b"\x00" * 2 + (2 * 1024 * 1024).to_bytes(4, "little"))
        with path.open("rb") as fp, pytest.raises(NcmError, match="密钥长度"):
            read_header(fp)

    def test_missing_metadata(self, tmp_path: Path) -> None:
        path = tmp_path / "nometa.ncm"
        _build_ncm(path, meta_blob=b"")
        with path.open("rb") as fp, pytest.raises(NcmError, match="音频格式"):
            read_header(fp)

    def test_oversized_metadata(self, tmp_path: Path) -> None:
        path = tmp_path / "bigmeta.ncm"
        key_blob = _encrypt_key()
        path.write_bytes(
            MAGIC
            + b"\x00" * 2
            + len(key_blob).to_bytes(4, "little")
            + key_blob
            + (17 * 1024 * 1024).to_bytes(4, "little")
        )
        with path.open("rb") as fp, pytest.raises(NcmError, match="元数据长度"):
            read_header(fp)

    def test_oversized_cover(self, tmp_path: Path) -> None:
        path = tmp_path / "bigcover.ncm"
        key_blob = _encrypt_key()
        meta_blob = _encrypt_meta({"format": "mp3"})
        path.write_bytes(
            MAGIC
            + b"\x00" * 2
            + len(key_blob).to_bytes(4, "little")
            + key_blob
            + len(meta_blob).to_bytes(4, "little")
            + meta_blob
            + b"\x00" * 9
            + (65 * 1024 * 1024).to_bytes(4, "little")
        )
        with path.open("rb") as fp, pytest.raises(NcmError, match="封面长度"):
            read_header(fp)

    def test_invalid_format_token(self, tmp_path: Path) -> None:
        path = tmp_path / "badfmt.ncm"
        _build_ncm(path, metadata={"format": "../evil"})
        with path.open("rb") as fp, pytest.raises(NcmError, match="音频格式非法"):
            read_header(fp)

    def test_missing_format(self, tmp_path: Path) -> None:
        path = tmp_path / "noformat.ncm"
        _build_ncm(path, metadata={"musicName": "x"})
        with path.open("rb") as fp, pytest.raises(NcmError, match="缺少音频格式"):
            read_header(fp)

    def test_non_string_format(self, tmp_path: Path) -> None:
        path = tmp_path / "numfmt.ncm"
        _build_ncm(path, metadata={"format": 3})
        with path.open("rb") as fp, pytest.raises(NcmError, match="缺少音频格式"):
            read_header(fp)


# ============================================================================ #
# 输出路径与端到端解锁
# ============================================================================ #
class TestOutputPath:
    """输出路径计算。"""

    def test_defaults_to_source_directory(self) -> None:
        assert build_output_path(Path("music/a.ncm"), "mp3") == Path("music/a.mp3")

    def test_uses_custom_directory(self) -> None:
        assert build_output_path(Path("music/a.ncm"), "flac", Path("out")) == Path("out/a.flac")


class TestCollectNcmFiles:
    """目录导入。"""

    def test_filters_and_sorts(self, tmp_path: Path) -> None:
        (tmp_path / "b.ncm").write_bytes(b"")
        (tmp_path / "A.NCM").write_bytes(b"")
        (tmp_path / "note.txt").write_text("x", encoding="utf-8")
        (tmp_path / "sub.ncm").mkdir()
        assert [item.name for item in collect_ncm_files(tmp_path)] == ["A.NCM", "b.ncm"]

    def test_unreadable_directory(self, tmp_path: Path) -> None:
        with pytest.raises(NcmError, match="无法读取目录"):
            collect_ncm_files(tmp_path / "missing")


class TestDecryptNcm:
    """端到端解锁。"""

    def test_writes_decrypted_audio(self, tmp_path: Path) -> None:
        source = tmp_path / "song.ncm"
        audio = bytes(range(200))
        _build_ncm(source, audio=audio)
        target = decrypt_ncm(source)
        assert target == tmp_path / "song.mp3"
        assert target.read_bytes() == _expected_audio(audio)

    def test_uses_output_directory(self, tmp_path: Path) -> None:
        source = tmp_path / "song.ncm"
        audio = b"abc"
        _build_ncm(source, metadata={"format": "flac"}, audio=audio)
        target = decrypt_ncm(source, tmp_path / "out")
        assert target == tmp_path / "out" / "song.flac"
        assert target.read_bytes() == _expected_audio(audio)

    def test_missing_source(self, tmp_path: Path) -> None:
        with pytest.raises(NcmError, match="文件不存在"):
            decrypt_ncm(tmp_path / "missing.ncm")

    def test_refuses_to_overwrite_source(self, tmp_path: Path) -> None:
        source = tmp_path / "song.ncm"
        _build_ncm(source, metadata={"format": "ncm"})
        with pytest.raises(NcmError, match="输出路径与输入文件相同"):
            decrypt_ncm(source)

    def test_cleans_partial_file_on_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = tmp_path / "song.ncm"
        _build_ncm(source)

        def boom(*args: Any, **kwargs: Any) -> int:
            raise OSError("disk full")

        monkeypatch.setattr(ncmconvertw, "decrypt_audio", boom)
        with pytest.raises(OSError, match="disk full"):
            decrypt_ncm(source)
        assert not (tmp_path / "song.mp3.part").exists()


# ============================================================================ #
# GUI 入口
# ============================================================================ #
class TestGuiEntry:
    """GUI 启动入口（不真正创建窗口）。"""

    def test_run_gui_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """tkinter 不可用时打印错误并退出。"""

        def fake_window(_path: Path | None) -> None:
            raise ImportError("no tkinter")

        monkeypatch.setattr(ncmconvertw, "NcmConvertWindow", fake_window)
        ncmconvertw.run_gui()  # 不应抛出

    def test_run_gui_starts_mainloop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常创建窗口后进入 mainloop。"""
        calls: list[str] = []

        class _Root:
            def mainloop(self) -> None:
                calls.append("mainloop")

        class _Window:
            root = _Root()

        monkeypatch.setattr(ncmconvertw, "NcmConvertWindow", lambda _path: _Window())
        ncmconvertw.run_gui()
        assert calls == ["mainloop"]

    def test_launch_cmd_calls_run_gui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []
        monkeypatch.setattr(ncmconvertw, "run_gui", lambda: calls.append(True))
        ncmconvertw.launch_cmd()
        assert calls == [True]
