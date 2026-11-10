"""ncmconvertw - NCM 音乐解锁转换器（Tk GUI）。

交互参考 ``Majjcom/ncmppGui``：选择输出目录，添加或从目录导入 ``.ncm``
文件，点击「开始转换」批量解锁为原始音频（通常为 ``.mp3`` / ``.flac``）。
转换在后台线程逐文件执行，进度、成功与失败明细实时回传日志区。

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
import queue
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fcmd
from fcmd.tools import _guiconfig, _guitheme, _guiwindow

if TYPE_CHECKING:
    from collections.abc import Iterable
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

__version__ = "0.1.0"

_DESCRIPTION = (
    "NCM 音乐解锁转换器窗口版（Tk）：批量添加/导入 .ncm 文件，后台逐文件解锁为 mp3/flac，"
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
# Tk 界面
# ---------------------------------------------------------------------- #
class NcmConvertWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk NCM 转换器主窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self._tk = tk
        self._filedialog = filedialog
        self._messagebox = messagebox
        self._ttk = ttk
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)
        self._files: list[Path] = []
        self._queue: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._running = False
        self._closing = False

        self.root, self._theme = _guiwindow.create_root(
            title=f"NCM 转换器 v{__version__}",
            saved_geometry=self._settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self.close,
        )
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=3)
        self.root.rowconfigure(3, weight=2)

        self._output_dir_var = tk.StringVar(value=self._settings.output_dir)
        self._status_var = tk.StringVar(value="就绪")
        self._count_var = tk.StringVar(value="共 0 个文件")
        self._last_dir = self._settings.last_input_dir

        self._build_menu()
        self._build_widgets()
        _guitheme.install_theme_switcher(self.root)

    # ------------------------------------------------------------------ #
    # 界面构建
    # ------------------------------------------------------------------ #
    def _build_menu(self) -> None:
        _guiwindow.build_menu(self.root, on_close=self.close, on_about=self._show_about)

    def _build_widgets(self) -> None:
        ttk = self._ttk
        spacing = self._theme.spacing

        io_frame = ttk.LabelFrame(self.root, text="输出设置", padding=spacing.sm)
        io_frame.grid(row=0, column=0, sticky="ew", padx=spacing.md, pady=(spacing.md, spacing.xs))
        io_frame.columnconfigure(1, weight=1)

        ttk.Label(io_frame, text="输出目录：").grid(row=0, column=0, sticky="w")
        ttk.Entry(io_frame, textvariable=self._output_dir_var).grid(
            row=0, column=1, sticky="ew", padx=(spacing.xs, spacing.sm)
        )
        self._browse_output_button = ttk.Button(io_frame, text="浏览…", command=self._browse_output_dir)
        self._browse_output_button.grid(row=0, column=2)
        ttk.Label(
            io_frame,
            text="留空时解锁到每个 NCM 文件所在目录，文件名为 <原名>.<格式>",
            foreground=self._theme.palette.muted,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(spacing.sm, 0))

        files_frame = ttk.LabelFrame(self.root, text="待转换的 NCM 文件", padding=spacing.sm)
        files_frame.grid(row=1, column=0, sticky="nsew", padx=spacing.md, pady=spacing.xs)
        files_frame.columnconfigure(0, weight=1)
        files_frame.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(files_frame)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, spacing.xs))
        self._add_files_button = ttk.Button(toolbar, text="添加文件…", command=self._add_files)
        self._add_files_button.pack(side="left")
        self._import_dir_button = ttk.Button(toolbar, text="导入目录…", command=self._import_dir)
        self._import_dir_button.pack(side="left", padx=(spacing.xs, 0))
        self._remove_button = ttk.Button(toolbar, text="移除选中", command=self._remove_selected)
        self._remove_button.pack(side="left", padx=(spacing.sm, 0))
        self._clear_button = ttk.Button(toolbar, text="清空", command=self._clear_files, style="Danger.TButton")
        self._clear_button.pack(side="right")

        list_frame = ttk.Frame(files_frame)
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self._listbox = self._tk.Listbox(list_frame, selectmode="extended", activestyle="none", exportselection=False)
        list_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self._listbox.yview)
        self._listbox.configure(yscrollcommand=list_scroll.set)
        self._listbox.grid(row=0, column=0, sticky="nsew")
        list_scroll.grid(row=0, column=1, sticky="ns")

        action_frame = ttk.Frame(self.root)
        action_frame.grid(row=2, column=0, sticky="ew", padx=spacing.md, pady=(spacing.xs, 0))
        action_frame.columnconfigure(1, weight=1)
        self._convert_button = ttk.Button(
            action_frame, text="开始转换", command=self._start_conversion, style="Accent.TButton"
        )
        self._convert_button.grid(row=0, column=0)
        self._progress = ttk.Progressbar(action_frame, mode="determinate", maximum=1, value=0)
        self._progress.grid(row=0, column=1, sticky="ew", padx=spacing.md)
        self._status_label = ttk.Label(
            action_frame, textvariable=self._status_var, foreground=self._theme.palette.muted
        )
        self._status_label.grid(row=0, column=2, sticky="e")

        log_frame = ttk.LabelFrame(self.root, text="日志", padding=spacing.sm)
        log_frame.grid(row=3, column=0, sticky="nsew", padx=spacing.md, pady=(spacing.xs, spacing.xs))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self._log_text = _guiwindow.create_log(log_frame, height=8, grid=True)

        footer = ttk.Frame(self.root)
        footer.grid(row=4, column=0, sticky="ew", padx=spacing.md, pady=(0, spacing.sm))
        footer.columnconfigure(1, weight=1)
        ttk.Label(footer, textvariable=self._count_var, foreground=self._theme.palette.muted).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(footer, text="仅支持 .ncm 文件，依赖 fcmd[crypto]", foreground=self._theme.palette.muted).grid(
            row=0, column=2, sticky="e"
        )

        self._append_log("添加 .ncm 文件或导入目录后点击「开始转换」。")

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _show_about(self) -> None:
        """关于对话框。"""
        self._messagebox.showinfo(
            "关于",
            f"NCM 转换器 v{__version__}\n\n"
            "解密 NCM 容器并还原为原始音频（mp3/flac）\n"
            "支持批量添加、目录导入与失败明细\n"
            "依赖 cryptography（pip install fcmd[crypto]）",
        )

    def _browse_output_dir(self) -> None:
        """选择输出目录。"""
        directory = self._filedialog.askdirectory(title="选择输出目录", initialdir=self._last_dir or None)
        if directory:
            self._output_dir_var.set(directory)
            self._last_dir = directory

    def _add_files(self) -> None:
        """选择一个或多个 NCM 文件加入列表。"""
        filenames = self._filedialog.askopenfilenames(
            title="选择 NCM 文件",
            initialdir=self._last_dir or None,
            filetypes=[("NCM 文件", "*.ncm"), ("所有文件", "*.*")],
        )
        if not filenames:
            return
        self._last_dir = str(Path(filenames[0]).parent)
        self._add_paths(Path(name) for name in filenames)

    def _import_dir(self) -> None:
        """导入目录下（不递归）的全部 NCM 文件。"""
        directory = self._filedialog.askdirectory(title="选择包含 NCM 文件的目录", initialdir=self._last_dir or None)
        if not directory:
            return
        self._last_dir = directory
        try:
            found = collect_ncm_files(Path(directory).expanduser())
        except NcmError as exc:
            self._append_log(f"错误：{exc}")
            self._messagebox.showerror("导入失败", str(exc))
            return
        if not found:
            self._append_log(f"目录中没有 .ncm 文件：{directory}")
            return
        self._add_paths(found)

    def _add_paths(self, paths: Iterable[Path]) -> None:
        """把合法 NCM 文件去重后加入列表并刷新界面。"""
        known = {str(path) for path in self._files}
        added = 0
        ignored = 0
        for path in paths:
            if path.suffix.lower() != ".ncm":
                ignored += 1
                continue
            text = str(path)
            if text in known:
                continue
            known.add(text)
            self._files.append(path)
            added += 1
        self._refresh_list()
        if added:
            self._append_log(f"已添加 {added} 个文件")
        if ignored:
            self._append_log(f"已忽略 {ignored} 个非 .ncm 文件")

    def _remove_selected(self) -> None:
        """移除列表中选中的文件。"""
        selected = list(self._listbox.curselection())
        if not selected:
            return
        for index in reversed(selected):
            del self._files[index]
        self._refresh_list()
        self._append_log(f"已移除 {len(selected)} 个文件")

    def _clear_files(self) -> None:
        """清空待转换列表。"""
        if not self._files:
            return
        count = len(self._files)
        self._files.clear()
        self._refresh_list()
        self._append_log(f"已清空 {count} 个文件")

    def _refresh_list(self) -> None:
        """按 ``self._files`` 重绘文件列表与计数。"""
        self._listbox.delete(0, "end")
        for path in self._files:
            self._listbox.insert("end", str(path))
        self._count_var.set(f"共 {len(self._files)} 个文件")

    def _append_log(self, message: str) -> None:
        """向只读日志区追加带时间戳的一行并滚动到底部。"""
        _guiwindow.append_log(self._log_text, message, timestamp=True)

    def _set_busy(self, busy: bool) -> None:
        """转换期间禁用输入控件，避免重复提交或改列表。"""
        self._running = busy
        state = "disabled" if busy else "normal"
        for button in (
            self._add_files_button,
            self._import_dir_button,
            self._remove_button,
            self._clear_button,
            self._browse_output_button,
            self._convert_button,
        ):
            button.configure(state=state)

    # ------------------------------------------------------------------ #
    # 转换（后台线程 + 队列回传，避免阻塞 Tk 主循环）
    # ------------------------------------------------------------------ #
    def _start_conversion(self) -> None:
        """校验输出目录与文件列表，启动后台转换线程。"""
        if self._running:
            return
        if not self._files:
            self._messagebox.showwarning("提醒", "没有输入的文件")
            return

        raw_output = self._output_dir_var.get().strip()
        output_dir = Path(raw_output).expanduser() if raw_output else None
        if output_dir is not None:
            if not output_dir.exists():
                self._messagebox.showerror("错误", "输出目录不存在")
                return
            if not output_dir.is_dir():
                self._messagebox.showerror("错误", "不是目录")
                return

        self._cancel = threading.Event()
        self._queue = queue.Queue()
        self._progress.configure(maximum=len(self._files), value=0)
        self._status_var.set("正在转换…")
        self._status_label.configure(foreground=self._theme.palette.muted)
        self._set_busy(True)
        location = str(output_dir) if output_dir is not None else "各文件所在目录"
        self._append_log(f"开始转换 {len(self._files)} 个文件，输出到：{location}")

        self._worker = _guiwindow.start_worker(
            self.root,
            self._run_conversion,
            (list(self._files), output_dir),
            self._poll_queue,
        )

    def _run_conversion(self, files: list[Path], output_dir: Path | None) -> None:
        """后台线程：逐文件解锁并把进度 / 结果写入队列。"""
        total = len(files)
        succeeded = 0
        failed = 0
        for index, path in enumerate(files, 1):
            if self._cancel.is_set():
                self._queue.put(("cancelled", index - 1, total))
                break
            try:
                target = decrypt_ncm(path, output_dir)
            except (NcmError, OSError) as exc:
                failed += 1
                self._queue.put(("error", path.name, str(exc)))
            else:
                succeeded += 1
                self._queue.put(("log", f"[{index}/{total}] {path.name} → {target}"))
            self._queue.put(("progress", index, total, path.name))
        self._queue.put(("done", succeeded, failed, total))

    def _poll_queue(self) -> None:
        """在主线程消费后台事件，更新进度条、日志与状态。"""
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break

            kind = event[0]
            if kind == "progress":
                _, done, total, _name = event
                self._progress.configure(maximum=max(total, 1), value=done)
                self._status_var.set(f"{done}/{total}")
            elif kind == "log":
                self._append_log(event[1])
            elif kind == "error":
                _, name, message = event
                self._append_log(f"失败：{name}：{message}")
            elif kind == "cancelled":
                _, done, total = event
                self._append_log(f"已中止：完成 {done}/{total}")
            elif kind == "done":
                _, succeeded, failed, total = event
                self._finish(succeeded, failed, total)
                return

        if self._running:
            self.root.after(_guiwindow.WORKER_INTERVAL_MS, self._poll_queue)

    def _finish(self, succeeded: int, failed: int, total: int) -> None:
        """转换结束：恢复控件、更新状态并提示汇总结果。"""
        self._set_busy(False)
        self._worker = None
        self._progress.configure(value=total)
        if failed:
            self._status_var.set(f"完成：成功 {succeeded}，失败 {failed}")
            self._status_label.configure(foreground=self._theme.palette.danger)
            self._append_log(f"转换结束：成功 {succeeded}，失败 {failed}，共 {total} 个")
            self._messagebox.showwarning("完成", f"解锁已完成，但 {failed} 个文件失败，详见日志。")
        else:
            self._status_var.set("✓ 全部完成")
            self._status_label.configure(foreground=self._theme.palette.success)
            self._append_log(f"转换结束：全部成功，共 {succeeded} 个")
            self._messagebox.showinfo("完成", "解锁已完成")

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """关闭窗口：运行中先确认，随后保存设置并销毁窗口。"""
        if self._closing:
            return
        if self._running:
            confirmed = self._messagebox.askyesno("确认", "转换正在进行中，确定要退出吗？")
            if not confirmed:
                return
            self._cancel.set()
            if self._worker is not None:
                self._worker.join(timeout=2)
        self._closing = True
        save_settings(
            NcmConvertSettings(
                output_dir=self._output_dir_var.get(),
                last_input_dir=self._last_dir,
                geometry=self.root.winfo_geometry(),
            ),
            self._settings_path,
        )
        self.root.destroy()


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(lambda: NcmConvertWindow(settings_path), "NCM 转换器")


@fcmd.tool("ncmconvertw", help="打开 NCM 转换器窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 NCM 转换器窗口。"""
    run_gui()


@fcmd.main("ncmconvertw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
