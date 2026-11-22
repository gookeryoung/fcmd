"""ncmconvertw - NCM 音乐解锁转换器（Qt GUI 入口与解密核心）。

交互参考 ``Majjcom/ncmppGui``：选择输出目录，添加或从目录导入 ``.ncm``
文件，点击「开始转换」批量解锁为原始音频（通常为 ``.mp3`` / ``.flac``）。
窗口在 :mod:`fcmd.tools.media._ncmconvertw_qt`，本模块负责 NCM 容器解析、
解密流程与设置读写，CI 无显示环境也能单测。

NCM 容器格式与解密流程参考 ``ncmppGui`` 的 ``ncmdump.cpp``：AES-128-ECB
解出密钥流与元数据，再用 RC4 变体密钥盒逐字节异或还原音频。设置（上次
输出目录、上次导入目录、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/ncmconvertw.json``，下次启动自动恢复。

示例
----
    fcmd ncmconvertw       # 打开 NCM 转换器窗口
    ncmconvertw            # 全局命令等价入口
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import geometry as _guiwindow

if TYPE_CHECKING:
    from typing import BinaryIO

__all__ = [
    "CORE_KEY",
    "DEFAULT_GEOMETRY",
    "META_KEY",
    "NcmConvertSettings",
    "NcmError",
    "NcmHeader",
    "build_key_box",
    "build_output_path",
    "collect_ncm_files",
    "config_path",
    "decrypt_audio",
    "decrypt_metadata",
    "decrypt_ncm",
    "load_settings",
    "read_header",
    "resolve_geometry",
    "save_settings",
    "valid_geometry",
]

_DESCRIPTION = (
    "NCM 音乐解锁转换器窗口版（Qt）：批量添加/导入 .ncm 文件，后台逐文件解锁为 mp3/flac，"
    "进度与失败明细实时回传，设置持久化到 ~/.fcmd/ui/ncmconvertw.json，依赖 fcmd[crypto]"
)

DEFAULT_GEOMETRY = "860x640+240+140"
MIN_WIDTH = 720
MIN_HEIGHT = 560

MAGIC = b"CTENFDAM"
CORE_KEY = bytes.fromhex("687A4852416D736F356B496E62617857")
META_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")
KEY_PREFIX = b"neteasecloudmusic"
META_PREFIX = b"163 key(Don't modify):"
META_JSON_PREFIX = b"music:"
CHUNK_SIZE = 0x8000

MAX_KEY_LENGTH = 1024 * 1024
MAX_META_LENGTH = 16 * 1024 * 1024
MAX_COVER_LENGTH = 64 * 1024 * 1024

_FORMAT_RE = re.compile(r"[a-z0-9]{1,8}")


class NcmError(Exception):
    """NCM 文件解析或解密失败。"""


@dataclass(frozen=True)
class NcmHeader:
    """从 NCM 文件头解析出的解密参数。

    属性:
        key_box: 256 字节音频密钥盒。
        metadata: 解密后的元数据字典（含 ``format``）。
        audio_format: 规范化后的音频扩展名（不含点），如 ``mp3`` / ``flac``。
    """

    key_box: bytes
    metadata: dict[str, Any]
    audio_format: str


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/ncmconvertw.json``。"""
    return _guiconfig.config_path("ncmconvertw")


@dataclass
class NcmConvertSettings:
    """NCM 转换器窗口的持久化设置。"""

    output_dir: str = ""
    last_input_dir: str = ""
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


def load_settings(path: Path | None = None) -> NcmConvertSettings:
    """读取设置文件；文件缺失、JSON 损坏或字段非法时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return NcmConvertSettings()

    settings = NcmConvertSettings()
    output_dir = raw.get("output_dir")
    last_input_dir = raw.get("last_input_dir")
    geometry = raw.get("geometry")
    if isinstance(output_dir, str):
        settings.output_dir = output_dir
    if isinstance(last_input_dir, str):
        settings.last_input_dir = last_input_dir
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    return settings


def save_settings(settings: NcmConvertSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略（不影响窗口关闭）。"""
    payload = {
        "output_dir": settings.output_dir,
        "last_input_dir": settings.last_input_dir,
        "geometry": settings.geometry,
    }
    _guiconfig.write_settings(payload, path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认位置与尺寸（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


# ---------------------------------------------------------------------- #
# NCM 解密
# ---------------------------------------------------------------------- #
def _read_exact(fp: BinaryIO, size: int, what: str) -> bytes:
    """读取固定长度字节；不足时抛出 :class:`NcmError`。"""
    data = fp.read(size)
    if len(data) != size:
        raise NcmError(f"NCM 文件不完整：{what} 长度不足（期望 {size} 字节，实际 {len(data)} 字节）")
    return data


def _read_u32(fp: BinaryIO) -> int:
    """读取 4 字节小端无符号整数。"""
    return int.from_bytes(_read_exact(fp, 4, "长度字段"), "little")


def _aes_ecb_decrypt(data: bytes, key: bytes) -> bytes:
    """用 AES-128-ECB 解密（无填充处理，长度须为 16 的整数倍）。

    Raises
    ------
    NcmError
        缺少 ``cryptography`` 库或数据长度非法。
    """
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise NcmError("未安装 cryptography 库，请安装: pip install fcmd[crypto]") from exc
    if not data or len(data) % 16:
        raise NcmError(f"加密数据长度不是 16 的整数倍: {len(data)}")
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return decryptor.update(data) + decryptor.finalize()


def _pkcs7_unpad(data: bytes) -> bytes:
    """去掉 PKCS#7 填充；填充字节非法时抛出 :class:`NcmError`。"""
    if not data:
        raise NcmError("解密结果为空，无法去除填充")
    pad = data[-1]
    if not 1 <= pad <= 16 or pad > len(data):
        raise NcmError(f"PKCS#7 填充非法: {pad}")
    if data[-pad:] != bytes([pad]) * pad:
        raise NcmError("PKCS#7 填充内容不一致")
    return data[:-pad]


def _xor_bytes(data: bytes, key: int) -> bytes:
    """对每个字节与 ``key`` 异或。"""
    return bytes(byte ^ key for byte in data)


def build_key_box(encrypted_key: bytes) -> bytes:
    """把文件头中的加密密钥流还原为 256 字节音频密钥盒。

    Raises
    ------
    NcmError
        密钥数据过短、AES 解密或去填充失败。
    """
    plain = _pkcs7_unpad(_aes_ecb_decrypt(_xor_bytes(encrypted_key, 0x64), CORE_KEY))
    if len(plain) <= len(KEY_PREFIX):
        raise NcmError("NCM 密钥数据过短，无法构建密钥盒")
    stream = plain[len(KEY_PREFIX) :]

    box = list(range(256))
    last = 0
    offset = 0
    for index in range(256):
        swap = box[index]
        value = (swap + last + stream[offset]) & 0xFF
        offset = (offset + 1) % len(stream)
        box[index] = box[value]
        box[value] = swap
        last = value
    return bytes(box)


def decrypt_metadata(encrypted_meta: bytes) -> dict[str, Any]:
    """解密文件头中的元数据（``163 key(Don't modify):`` + Base64 + AES）。

    Raises
    ------
    NcmError
        前缀、Base64、AES 或 JSON 任一环节非法。
    """
    raw = _xor_bytes(encrypted_meta, 0x63)
    if not raw.startswith(META_PREFIX):
        raise NcmError("NCM 元数据前缀不匹配，文件可能已损坏")
    try:
        payload = base64.b64decode(raw[len(META_PREFIX) :], validate=False)
    except (binascii.Error, ValueError) as exc:
        raise NcmError(f"NCM 元数据 Base64 解码失败: {exc}") from exc
    plain = _pkcs7_unpad(_aes_ecb_decrypt(payload, META_KEY))
    if not plain.startswith(META_JSON_PREFIX):
        raise NcmError("NCM 元数据缺少 music: 前缀")
    try:
        metadata = json.loads(plain[len(META_JSON_PREFIX) :].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise NcmError(f"NCM 元数据 JSON 解析失败: {exc}") from exc
    if not isinstance(metadata, dict):
        raise NcmError("NCM 元数据不是 JSON 对象")
    return metadata


def _normalize_format(value: Any) -> str:
    """把元数据里的 ``format`` 规范为安全的扩展名 token。

    Raises
    ------
    NcmError
        缺失或包含路径字符等非法内容。
    """
    if not isinstance(value, str) or not value.strip():
        raise NcmError("NCM 元数据缺少音频格式（format）")
    fmt = value.strip().lower().lstrip(".")
    if _FORMAT_RE.fullmatch(fmt) is None:
        raise NcmError(f"NCM 元数据中的音频格式非法: {value!r}")
    return fmt


def read_header(fp: BinaryIO) -> NcmHeader:
    """解析 NCM 文件头并定位到音频数据起始位置。

    参数:
        fp: 以二进制模式打开的 NCM 文件对象。

    返回:
        密钥盒、元数据与音频扩展名。

    Raises
    ------
    NcmError
        文件头不匹配、长度字段异常或元数据无法解密。
    """
    if _read_exact(fp, len(MAGIC), "文件头") != MAGIC:
        raise NcmError("不是有效的 NCM 文件（文件头不匹配）")
    fp.seek(2, io.SEEK_CUR)

    key_length = _read_u32(fp)
    if not 0 < key_length <= MAX_KEY_LENGTH:
        raise NcmError(f"NCM 密钥长度异常: {key_length}")
    key_box = build_key_box(_read_exact(fp, key_length, "密钥数据"))

    meta_length = _read_u32(fp)
    if meta_length > MAX_META_LENGTH:
        raise NcmError(f"NCM 元数据长度异常: {meta_length}")
    metadata = decrypt_metadata(_read_exact(fp, meta_length, "元数据")) if meta_length else {}
    audio_format = _normalize_format(metadata.get("format"))

    fp.seek(9, io.SEEK_CUR)
    cover_length = _read_u32(fp)
    if cover_length > MAX_COVER_LENGTH:
        raise NcmError(f"NCM 封面长度异常: {cover_length}")
    fp.seek(cover_length, io.SEEK_CUR)
    return NcmHeader(key_box=key_box, metadata=metadata, audio_format=audio_format)


def decrypt_audio(src: BinaryIO, dst: BinaryIO, key_box: bytes, *, chunk_size: int = CHUNK_SIZE) -> int:
    """按密钥盒流式解密音频数据，返回写入的字节数。

    参考实现每个 0x8000 分块从 1 重新计数；0x8000 是 256 的整数倍，因此
    这里用全局位置取模等价实现，分块大小不影响结果。
    """
    if len(key_box) != 256:
        raise ValueError(f"密钥盒长度须为 256 字节: {len(key_box)}")
    total = 0
    position = 0
    while True:
        block = bytearray(src.read(chunk_size))
        if not block:
            break
        for index in range(len(block)):
            position = (position + 1) & 0xFF
            mask = key_box[(key_box[position] + key_box[(key_box[position] + position) & 0xFF]) & 0xFF]
            block[index] ^= mask
        dst.write(block)
        total += len(block)
    return total


def build_output_path(source: Path, audio_format: str, output_dir: Path | None = None) -> Path:
    """按「源文件名 + 元数据格式」计算输出路径；``output_dir`` 为空时写入源目录。"""
    directory = output_dir if output_dir is not None else source.parent
    return directory / f"{source.stem}.{audio_format}"


def collect_ncm_files(directory: Path) -> list[Path]:
    """列出目录下（不递归）的 ``.ncm`` 文件，按文件名不区分大小写排序。

    Raises
    ------
    NcmError
        目录不可读取。
    """
    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
    except OSError as exc:
        raise NcmError(f"无法读取目录: {directory}（{exc}）") from exc
    return [entry for entry in entries if entry.is_file() and entry.suffix.lower() == ".ncm"]


def decrypt_ncm(source: Path, output_dir: Path | None = None) -> Path:
    """把一个 NCM 文件解密为原始音频，返回输出路径。

    先写同目录 ``.part`` 临时文件再原子替换，避免中断留下半截音频；
    目标文件已存在时直接覆盖（与参考实现一致）。

    Raises
    ------
    NcmError
        文件缺失、格式非法或解密失败。
    OSError
        读写磁盘失败。
    """
    if not source.is_file():
        raise NcmError(f"文件不存在: {source}")
    with source.open("rb") as src:
        header = read_header(src)
        target = build_output_path(source, header.audio_format, output_dir)
        if target.resolve() == source.resolve():
            raise NcmError(f"输出路径与输入文件相同: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        try:
            with tmp.open("wb") as dst:
                decrypt_audio(src, dst, header.key_box)
            tmp.replace(target)
        except Exception:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise
    return target


# ---------------------------------------------------------------------- #
# GUI 入口
# ---------------------------------------------------------------------- #
def run_gui(settings_path: Path | None = None) -> None:
    """启动 Qt 窗口（高 DPI 与主题由 ``gui.app`` 统一处理）。"""
    try:
        from fcmd.tools.media._ncmconvertw_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 安装完整时不会触发
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc

    run_qt(settings_path)


@fcmd.tool("ncmconvertw", help="打开 NCM 转换器窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 NCM 转换器窗口。"""
    run_gui()


@fcmd.main("ncmconvertw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
