"""videoconvw - 视频格式转换器（Tk GUI）。

交互参考 ``bitool_gui.videoconv``：选择输入视频与输出目录，设置输出格式、
视频/音频编码器、比特率、帧率、缩放、CRF、预设与外部音轨，一键转换。
转换在后台线程执行，ffmpeg 的进度与日志实时回传界面，可随时中止；设置
（上次输入/输出、编码参数、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/videoconvw.json``，下次启动自动恢复。

示例
----
    fcmd videoconvw        # 打开视频转换器窗口
    videoconvw             # 全局命令等价入口
"""

from __future__ import annotations

import contextlib
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.tools import _guiconfig, _guitheme, _guiwindow
from fcmd.tools.media.videoconv import (
    DEFAULT_AUDIO_BITRATE,
    DEFAULT_AUDIO_CODEC,
    DEFAULT_CRF,
    DEFAULT_PRESET,
    DEFAULT_VIDEO_BITRATE,
    DEFAULT_VIDEO_CODEC,
    OUTPUT_FORMAT_CHOICES,
    PRESETS,
    SUPPORTED_INPUT_FORMATS,
    VideoOptions,
    build_output_path,
    ffmpeg_version,
    normalize_format,
    run_conversion,
    validate_options,
)

__all__ = [
    "DEFAULT_GEOMETRY",
    "VideoConvSettings",
    "config_path",
    "load_settings",
    "parse_frame_rate",
    "parse_scale",
    "resolve_geometry",
    "save_settings",
    "valid_geometry",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "视频转换器窗口版（Tk）：文件/输出目录选择、格式与编码参数可视化、"
    "进度与日志实时回传、可中断，设置持久化到 ~/.fcmd/ui/videoconvw.json，依赖系统 ffmpeg"
)

DEFAULT_GEOMETRY = "980x760+200+80"
MIN_WIDTH = 900
MIN_HEIGHT = 640

VIDEO_CODEC_CHOICES: tuple[str, ...] = ("libx264", "libx265", "libvpx-vp9", "mpeg4", "libxvid")
AUDIO_CODEC_CHOICES: tuple[str, ...] = ("aac", "mp3", "opus", "libvorbis", "ac3", "flac")


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/videoconvw.json``。"""
    return _guiconfig.config_path("videoconvw")


@dataclass
class VideoConvSettings:
    """视频转换器窗口的持久化设置。"""

    input_path: str = ""
    output_dir: str = ""
    output_format: str = "mp4"
    video_codec: str = DEFAULT_VIDEO_CODEC
    audio_codec: str = DEFAULT_AUDIO_CODEC
    video_bitrate: int = DEFAULT_VIDEO_BITRATE
    audio_bitrate: int = DEFAULT_AUDIO_BITRATE
    frame_rate: int = 0  # 0 表示保持源帧率
    scale: float = 1.0
    crf: int = DEFAULT_CRF
    preset: str = DEFAULT_PRESET
    remove_audio: bool = False
    audio_file: str = ""
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


def parse_frame_rate(value: int | str) -> int | None:
    """解析帧率输入：空串或 0 表示保持源帧率，其余须为 1-240。

    Raises
    ------
    ValueError
        非整数或超出范围
    """
    if isinstance(value, str) and not value.strip():
        return None
    try:
        rate = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"帧率须为整数: {value!r}") from exc
    if rate == 0:
        return None
    if not 1 <= rate <= 240:
        raise ValueError(f"帧率须在 1-240 之间（0 表示保持源帧率）: {rate}")
    return rate


def parse_scale(value: int | float | str) -> float:
    """解析缩放因子，须在 ``(0, 10]`` 之间。

    Raises
    ------
    ValueError
        非数值或超出范围
    """
    try:
        scale = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"缩放因子须为数值: {value!r}") from exc
    if not 0 < scale <= 10:
        raise ValueError(f"缩放因子须在 (0, 10] 之间: {scale}")
    return scale


def _parse_int(value: Any, name: str, low: int, high: int) -> int:
    """把配置值解析为指定范围的整数，非法时抛出 :class:`ValueError`。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name}须为整数: {value!r}")
    if not low <= value <= high:
        raise ValueError(f"{name}须在 {low}-{high} 之间: {value}")
    return value


def load_settings(path: Path | None = None) -> VideoConvSettings:
    """读取设置文件；文件缺失、JSON 损坏或字段非法时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return VideoConvSettings()

    settings = VideoConvSettings()
    text_fields = {
        "input_path": "input_path",
        "output_dir": "output_dir",
        "audio_file": "audio_file",
    }
    for key, attr in text_fields.items():
        value = raw.get(key)
        if isinstance(value, str):
            setattr(settings, attr, value)

    output_format = raw.get("output_format")
    if isinstance(output_format, str):
        with contextlib.suppress(ValueError):
            settings.output_format = normalize_format(output_format).lstrip(".")

    video_codec = raw.get("video_codec")
    if isinstance(video_codec, str) and video_codec.strip():
        settings.video_codec = video_codec
    audio_codec = raw.get("audio_codec")
    if isinstance(audio_codec, str) and audio_codec.strip():
        settings.audio_codec = audio_codec

    for key, attr, low, high in (
        ("video_bitrate", "video_bitrate", 1, 50000),
        ("audio_bitrate", "audio_bitrate", 1, 1024),
        ("crf", "crf", 0, 51),
    ):
        with contextlib.suppress(ValueError):
            setattr(settings, attr, _parse_int(raw.get(key), key, low, high))

    with contextlib.suppress(ValueError):
        rate = parse_frame_rate(raw.get("frame_rate", 0))
        settings.frame_rate = rate or 0
    with contextlib.suppress(ValueError):
        settings.scale = parse_scale(raw.get("scale", 1.0))

    preset = raw.get("preset")
    if isinstance(preset, str) and preset in PRESETS:
        settings.preset = preset
    remove_audio = raw.get("remove_audio")
    if isinstance(remove_audio, bool):
        settings.remove_audio = remove_audio

    geometry = raw.get("geometry")
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    return settings


def save_settings(settings: VideoConvSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略（不影响窗口关闭）。"""
    payload = {
        "input_path": settings.input_path,
        "output_dir": settings.output_dir,
        "output_format": settings.output_format,
        "video_codec": settings.video_codec,
        "audio_codec": settings.audio_codec,
        "video_bitrate": settings.video_bitrate,
        "audio_bitrate": settings.audio_bitrate,
        "frame_rate": settings.frame_rate,
        "scale": settings.scale,
        "crf": settings.crf,
        "preset": settings.preset,
        "remove_audio": settings.remove_audio,
        "audio_file": settings.audio_file,
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
# Tk 界面
# ---------------------------------------------------------------------- #
class VideoConvWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk 视频转换器主窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self._tk = tk
        self._filedialog = filedialog
        self._messagebox = messagebox
        self._ttk = ttk
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)
        self._queue: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._running = False
        self._closing = False

        self.root, self._theme = _guiwindow.create_root(
            title=f"视频转换器 v{__version__}",
            saved_geometry=self._settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self.close,
        )
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(4, weight=1)

        self._input_var = tk.StringVar(value=self._settings.input_path)
        self._output_dir_var = tk.StringVar(value=self._settings.output_dir)
        self._format_var = tk.StringVar(value=self._settings.output_format)
        self._video_codec_var = tk.StringVar(value=self._settings.video_codec)
        self._audio_codec_var = tk.StringVar(value=self._settings.audio_codec)
        self._video_bitrate_var = tk.StringVar(value=str(self._settings.video_bitrate))
        self._audio_bitrate_var = tk.StringVar(value=str(self._settings.audio_bitrate))
        self._frame_rate_var = tk.StringVar(value=str(self._settings.frame_rate))
        self._scale_var = tk.StringVar(value=f"{self._settings.scale:g}")
        self._crf_var = tk.StringVar(value=str(self._settings.crf))
        self._preset_var = tk.StringVar(value=self._settings.preset)
        self._remove_audio_var = tk.BooleanVar(value=self._settings.remove_audio)
        self._audio_file_var = tk.StringVar(value=self._settings.audio_file)
        self._status_var = tk.StringVar(value="就绪")
        self._ffmpeg_var = tk.StringVar(value="正在检测 ffmpeg…")

        self._build_menu()
        self._build_widgets()
        _guitheme.install_theme_switcher(self.root)
        self.root.after(50, self._check_ffmpeg)

    # ------------------------------------------------------------------ #
    # 界面构建
    # ------------------------------------------------------------------ #
    def _build_menu(self) -> None:
        _guiwindow.build_menu(self.root, on_close=self.close, on_about=self._show_about)

    def _build_widgets(self) -> None:
        ttk = self._ttk

        io_frame = ttk.LabelFrame(self.root, text="输入与输出", padding=self._theme.spacing.sm)
        io_frame.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=self._theme.spacing.md,
            pady=(self._theme.spacing.md, self._theme.spacing.xs),
        )
        io_frame.columnconfigure(1, weight=1)

        ttk.Label(io_frame, text="输入视频：").grid(row=0, column=0, sticky="w")
        ttk.Entry(io_frame, textvariable=self._input_var).grid(
            row=0, column=1, sticky="ew", padx=(self._theme.spacing.xs, self._theme.spacing.sm)
        )
        self._browse_input_button = ttk.Button(io_frame, text="浏览…", command=self._browse_input)
        self._browse_input_button.grid(row=0, column=2)

        ttk.Label(io_frame, text="输出目录：").grid(row=1, column=0, sticky="w", pady=(self._theme.spacing.sm, 0))
        ttk.Entry(io_frame, textvariable=self._output_dir_var).grid(
            row=1,
            column=1,
            sticky="ew",
            padx=(self._theme.spacing.xs, self._theme.spacing.sm),
            pady=(self._theme.spacing.sm, 0),
        )
        self._browse_output_button = ttk.Button(io_frame, text="浏览…", command=self._browse_output_dir)
        self._browse_output_button.grid(row=1, column=2, pady=(self._theme.spacing.sm, 0))

        ttk.Label(
            io_frame,
            text="输出目录留空时写入输入文件所在目录，文件名为 <原名>_converted.<格式>",
            foreground=self._theme.palette.muted,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(self._theme.spacing.sm, 0))

        options_frame = ttk.LabelFrame(self.root, text="转换设置", padding=self._theme.spacing.sm)
        options_frame.grid(row=1, column=0, sticky="ew", padx=self._theme.spacing.md, pady=self._theme.spacing.xs)
        for column in (1, 3):
            options_frame.columnconfigure(column, weight=1)

        ttk.Label(options_frame, text="输出格式：").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            options_frame,
            textvariable=self._format_var,
            values=OUTPUT_FORMAT_CHOICES,
            state="readonly",
            width=12,
        ).grid(
            row=0,
            column=1,
            sticky="w",
            padx=(self._theme.spacing.xs, self._theme.spacing.lg),
            pady=self._theme.spacing.xxs,
        )
        ttk.Label(options_frame, text="视频编码器：").grid(row=0, column=2, sticky="w")
        ttk.Combobox(
            options_frame,
            textvariable=self._video_codec_var,
            values=VIDEO_CODEC_CHOICES,
            width=14,
        ).grid(row=0, column=3, sticky="w", padx=(self._theme.spacing.xs, 0), pady=self._theme.spacing.xxs)

        ttk.Label(options_frame, text="音频编码器：").grid(row=1, column=0, sticky="w")
        ttk.Combobox(
            options_frame,
            textvariable=self._audio_codec_var,
            values=AUDIO_CODEC_CHOICES,
            width=12,
        ).grid(
            row=1,
            column=1,
            sticky="w",
            padx=(self._theme.spacing.xs, self._theme.spacing.lg),
            pady=self._theme.spacing.xxs,
        )
        ttk.Label(options_frame, text="视频比特率 (kbps)：").grid(row=1, column=2, sticky="w")
        ttk.Spinbox(
            options_frame,
            from_=1,
            to=50000,
            increment=100,
            textvariable=self._video_bitrate_var,
            width=12,
        ).grid(row=1, column=3, sticky="w", padx=(self._theme.spacing.xs, 0), pady=self._theme.spacing.xxs)

        ttk.Label(options_frame, text="音频比特率 (kbps)：").grid(row=2, column=0, sticky="w")
        ttk.Spinbox(
            options_frame,
            from_=1,
            to=1024,
            increment=32,
            textvariable=self._audio_bitrate_var,
            width=12,
        ).grid(
            row=2,
            column=1,
            sticky="w",
            padx=(self._theme.spacing.xs, self._theme.spacing.lg),
            pady=self._theme.spacing.xxs,
        )
        ttk.Label(options_frame, text="帧率 (0=源帧率)：").grid(row=2, column=2, sticky="w")
        ttk.Spinbox(
            options_frame,
            from_=0,
            to=240,
            increment=1,
            textvariable=self._frame_rate_var,
            width=12,
        ).grid(row=2, column=3, sticky="w", padx=(self._theme.spacing.xs, 0), pady=self._theme.spacing.xxs)

        ttk.Label(options_frame, text="分辨率缩放：").grid(row=3, column=0, sticky="w")
        ttk.Spinbox(
            options_frame,
            from_=0.1,
            to=10.0,
            increment=0.1,
            textvariable=self._scale_var,
            width=12,
        ).grid(
            row=3,
            column=1,
            sticky="w",
            padx=(self._theme.spacing.xs, self._theme.spacing.lg),
            pady=self._theme.spacing.xxs,
        )
        ttk.Label(options_frame, text="CRF 质量 (0-51)：").grid(row=3, column=2, sticky="w")
        ttk.Spinbox(
            options_frame,
            from_=0,
            to=51,
            increment=1,
            textvariable=self._crf_var,
            width=12,
        ).grid(row=3, column=3, sticky="w", padx=(self._theme.spacing.xs, 0), pady=self._theme.spacing.xxs)

        ttk.Label(options_frame, text="编码预设：").grid(row=4, column=0, sticky="w")
        ttk.Combobox(
            options_frame,
            textvariable=self._preset_var,
            values=PRESETS,
            state="readonly",
            width=12,
        ).grid(
            row=4,
            column=1,
            sticky="w",
            padx=(self._theme.spacing.xs, self._theme.spacing.lg),
            pady=self._theme.spacing.xxs,
        )
        ttk.Checkbutton(options_frame, text="移除音轨", variable=self._remove_audio_var).grid(
            row=4, column=2, columnspan=2, sticky="w", pady=self._theme.spacing.xxs
        )

        ttk.Label(options_frame, text="外部音轨：").grid(row=5, column=0, sticky="w")
        ttk.Entry(options_frame, textvariable=self._audio_file_var).grid(
            row=5,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=(self._theme.spacing.xs, self._theme.spacing.sm),
            pady=self._theme.spacing.xxs,
        )
        self._browse_audio_button = ttk.Button(options_frame, text="浏览…", command=self._browse_audio_file)
        self._browse_audio_button.grid(row=5, column=3, sticky="w", pady=self._theme.spacing.xxs)

        action_frame = ttk.Frame(self.root)
        action_frame.grid(row=2, column=0, sticky="ew", padx=self._theme.spacing.md, pady=(self._theme.spacing.xs, 0))
        action_frame.columnconfigure(2, weight=1)

        self._start_button = ttk.Button(
            action_frame, text="开始转换", command=self._start_conversion, style="Accent.TButton"
        )
        self._start_button.grid(row=0, column=0, padx=(0, self._theme.spacing.sm))
        self._stop_button = ttk.Button(
            action_frame, text="停止", command=self._stop_conversion, state="disabled", style="Danger.TButton"
        )
        self._stop_button.grid(row=0, column=1, padx=(0, self._theme.spacing.sm))
        self._status_label = ttk.Label(
            action_frame, textvariable=self._status_var, foreground=self._theme.palette.muted
        )
        self._status_label.grid(row=0, column=2, sticky="w", padx=(self._theme.spacing.sm, 0))
        self._reset_button = ttk.Button(action_frame, text="重置设置", command=self._reset_settings)
        self._reset_button.grid(row=0, column=3)

        progress_frame = ttk.Frame(self.root)
        progress_frame.grid(
            row=3,
            column=0,
            sticky="ew",
            padx=self._theme.spacing.md,
            pady=(self._theme.spacing.sm, self._theme.spacing.xs),
        )
        progress_frame.columnconfigure(0, weight=1)
        self._progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=100, value=0)
        self._progress.grid(row=0, column=0, sticky="ew")

        log_frame = ttk.LabelFrame(self.root, text="日志", padding=self._theme.spacing.sm)
        log_frame.grid(
            row=4,
            column=0,
            sticky="nsew",
            padx=self._theme.spacing.md,
            pady=(self._theme.spacing.xs, self._theme.spacing.xs),
        )
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self._log_text = _guiwindow.create_log(log_frame, height=10, grid=True)

        status_frame = ttk.Frame(self.root)
        status_frame.grid(
            row=5,
            column=0,
            sticky="ew",
            padx=self._theme.spacing.md,
            pady=(self._theme.spacing.xs, self._theme.spacing.sm),
        )
        status_frame.columnconfigure(0, weight=1)
        self._ffmpeg_label = ttk.Label(
            status_frame, textvariable=self._ffmpeg_var, foreground=self._theme.palette.muted
        )
        self._ffmpeg_label.grid(row=0, column=0, sticky="w")
        ttk.Label(status_frame, text="依赖系统 ffmpeg", foreground=self._theme.palette.muted).grid(
            row=0, column=1, sticky="e"
        )

        self._append_log("选择输入视频并设置转换参数后点击「开始转换」。")

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _show_about(self) -> None:
        """关于对话框。"""
        self._messagebox.showinfo(
            "关于",
            f"视频转换器 v{__version__}\n\n"
            f"输入格式：{'、'.join(item.lstrip('.') for item in SUPPORTED_INPUT_FORMATS)}\n"
            f"输出格式：{'、'.join(OUTPUT_FORMAT_CHOICES)}\n"
            "依赖系统 ffmpeg（含 ffprobe）",
        )

    def _browse_input(self) -> None:
        """选择输入视频文件。"""
        patterns = " ".join(f"*{item}" for item in SUPPORTED_INPUT_FORMATS)
        filename = self._filedialog.askopenfilename(
            title="选择输入视频",
            filetypes=[("视频文件", patterns), ("所有文件", "*.*")],
        )
        if filename:
            self._input_var.set(filename)
            self._output_dir_var.set(str(Path(filename).parent))

    def _browse_output_dir(self) -> None:
        """选择输出目录。"""
        directory = self._filedialog.askdirectory(title="选择输出目录")
        if directory:
            self._output_dir_var.set(directory)

    def _browse_audio_file(self) -> None:
        """选择用于替换/合并的外部音频文件。"""
        filename = self._filedialog.askopenfilename(
            title="选择音频文件",
            filetypes=[
                ("音频文件", "*.mp3 *.wav *.aac *.flac *.ogg *.m4a *.wma"),
                ("所有文件", "*.*"),
            ],
        )
        if filename:
            self._audio_file_var.set(filename)

    def _append_log(self, message: str) -> None:
        """向只读日志区追加带时间戳的一行并滚动到底部。"""
        _guiwindow.append_log(self._log_text, message, timestamp=True)

    def _set_busy(self, busy: bool) -> None:
        """转换期间禁用按钮，避免重复提交。"""
        self._running = busy
        self._start_button.configure(state="disabled" if busy else "normal")
        self._stop_button.configure(state="normal" if busy else "disabled")
        self._reset_button.configure(state="disabled" if busy else "normal")

    def _collect_options(self) -> VideoOptions:
        """从界面控件收集并校验转换参数。"""
        audio_text = self._audio_file_var.get().strip()
        options = VideoOptions(
            output_format=normalize_format(self._format_var.get()),
            video_codec=self._video_codec_var.get().strip(),
            audio_codec=self._audio_codec_var.get().strip(),
            video_bitrate=int(self._video_bitrate_var.get()),
            audio_bitrate=int(self._audio_bitrate_var.get()),
            frame_rate=parse_frame_rate(self._frame_rate_var.get()),
            scale=parse_scale(self._scale_var.get()),
            crf=int(self._crf_var.get()),
            preset=self._preset_var.get(),
            remove_audio=self._remove_audio_var.get(),
            audio_file=Path(audio_text).expanduser() if audio_text else None,
        )
        validate_options(options)
        return options

    def _check_ffmpeg(self) -> None:
        """检测 ffmpeg 并在状态栏显示版本或缺失提示。"""
        version = ffmpeg_version()
        if version:
            self._ffmpeg_var.set(f"✓ {version}")
            self._ffmpeg_label.configure(foreground=self._theme.palette.success)
            self._append_log(f"ffmpeg 可用：{version}")
        else:
            self._ffmpeg_var.set("✗ 未找到 ffmpeg，请安装并加入 PATH")
            self._ffmpeg_label.configure(foreground=self._theme.palette.danger)
            self._append_log("警告：未找到 ffmpeg，转换功能不可用")

    # ------------------------------------------------------------------ #
    # 转换（后台线程 + 队列回传，避免阻塞 Tk 主循环）
    # ------------------------------------------------------------------ #
    def _start_conversion(self) -> None:
        """校验输入与参数，启动后台转换线程。"""
        if self._running:
            return

        raw_input = self._input_var.get().strip()
        if not raw_input:
            self._messagebox.showwarning("提示", "请选择要转换的视频文件")
            return
        input_path = Path(raw_input).expanduser()
        if not input_path.is_file():
            self._messagebox.showerror("错误", f"输入文件不存在：\n{input_path}")
            return
        if input_path.suffix.lower() not in SUPPORTED_INPUT_FORMATS:
            self._messagebox.showerror("错误", f"不支持的输入格式：{input_path.suffix or '(无扩展名)'}")
            return

        try:
            options = self._collect_options()
        except (ValueError, TypeError) as exc:
            self._messagebox.showerror("参数错误", str(exc))
            return

        if options.audio_file is not None and not options.remove_audio and not options.audio_file.is_file():
            self._messagebox.showerror("错误", f"外部音频文件不存在：\n{options.audio_file}")
            return

        raw_output_dir = self._output_dir_var.get().strip()
        output_dir = Path(raw_output_dir).expanduser() if raw_output_dir else None
        output_path = build_output_path(input_path, output_dir, options.output_format)

        self._append_log(f"输入：{input_path}")
        self._append_log(f"输出：{output_path}")
        self._cancel = threading.Event()
        self._queue = queue.Queue()
        self._progress.configure(value=0)
        self._status_var.set("正在转换…")
        self._status_label.configure(foreground=self._theme.palette.muted)
        self._set_busy(True)

        self._worker = _guiwindow.start_worker(
            self.root,
            self._run_conversion,
            (input_path, output_path, options),
            self._poll_queue,
        )

    def _run_conversion(self, input_path: Path, output_path: Path, options: VideoOptions) -> None:
        """后台线程：执行转换并把事件写入队列。"""
        import io

        def progress(percent: int, message: str) -> None:
            self._queue.put(("progress", percent, message))

        captured = io.StringIO()
        try:
            with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
                success = run_conversion(
                    input_path,
                    output_path,
                    options,
                    progress=progress,
                    cancel=self._cancel,
                )
        except Exception as exc:  # 后台异常须转事件回主线程，避免界面卡在“转换中”
            captured.write(f"后台异常：{exc}\n")
            success = False
        for line in captured.getvalue().splitlines():
            if line.strip():
                self._queue.put(("log", line))
        self._queue.put(("done", success, output_path))

    def _poll_queue(self) -> None:
        """在主线程消费后台事件，更新进度条、日志与状态。"""
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break

            kind = event[0]
            if kind == "progress":
                _, percent, message = event
                self._progress.configure(value=percent)
                self._status_var.set(f"进度：{percent}% - {message}")
                self._append_log(message)
            elif kind == "log":
                self._append_log(event[1])
            elif kind == "done":
                _, success, output_path = event
                self._finish(success, output_path)
                return

        if self._running:
            self.root.after(80, self._poll_queue)

    def _finish(self, success: bool, output_path: Path) -> None:
        """转换结束：恢复按钮、显示结果并提示。"""
        self._set_busy(False)
        self._worker = None
        if success:
            self._progress.configure(value=100)
            self._status_var.set("✓ 转换成功")
            self._status_label.configure(foreground=self._theme.palette.success)
            self._append_log(f"转换成功：{output_path}")
            self._messagebox.showinfo("转换完成", f"视频已生成：\n{output_path}")
        else:
            self._status_var.set("✗ 转换失败或已取消")
            self._status_label.configure(foreground=self._theme.palette.danger)
            self._append_log("转换失败或已取消，详见日志")
            self._messagebox.showwarning("转换失败", "转换失败或已取消，详见日志与命令行输出")

    def _stop_conversion(self) -> None:
        """请求中止后台转换。"""
        if self._running:
            self._cancel.set()
            self._status_var.set("正在停止…")
            self._status_label.configure(foreground=self._theme.palette.muted)
            self._append_log("已请求中止转换")

    def _reset_settings(self) -> None:
        """把界面参数恢复为默认值（不改变输入/输出路径）。"""
        self._format_var.set("mp4")
        self._video_codec_var.set(DEFAULT_VIDEO_CODEC)
        self._audio_codec_var.set(DEFAULT_AUDIO_CODEC)
        self._video_bitrate_var.set(str(DEFAULT_VIDEO_BITRATE))
        self._audio_bitrate_var.set(str(DEFAULT_AUDIO_BITRATE))
        self._frame_rate_var.set("0")
        self._scale_var.set("1")
        self._crf_var.set(str(DEFAULT_CRF))
        self._preset_var.set(DEFAULT_PRESET)
        self._remove_audio_var.set(False)
        self._audio_file_var.set("")
        self._append_log("转换参数已重置为默认值")

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _save_current_settings(self) -> None:
        """把当前界面状态写入设置文件（非法值回退默认值）。"""
        with contextlib.suppress(ValueError):
            self._format_var.set(normalize_format(self._format_var.get()).lstrip("."))
        save_settings(
            VideoConvSettings(
                input_path=self._input_var.get(),
                output_dir=self._output_dir_var.get(),
                output_format=self._format_var.get(),
                video_codec=self._video_codec_var.get().strip() or DEFAULT_VIDEO_CODEC,
                audio_codec=self._audio_codec_var.get().strip() or DEFAULT_AUDIO_CODEC,
                video_bitrate=self._int_or_default(self._video_bitrate_var.get(), DEFAULT_VIDEO_BITRATE),
                audio_bitrate=self._int_or_default(self._audio_bitrate_var.get(), DEFAULT_AUDIO_BITRATE),
                frame_rate=self._int_or_default(self._frame_rate_var.get(), 0),
                scale=self._scale_or_default(self._scale_var.get()),
                crf=self._int_or_default(self._crf_var.get(), DEFAULT_CRF),
                preset=self._preset_var.get(),
                remove_audio=self._remove_audio_var.get(),
                audio_file=self._audio_file_var.get(),
                geometry=self.root.winfo_geometry(),
            ),
            self._settings_path,
        )

    @staticmethod
    def _int_or_default(value: str, default: int) -> int:
        """把界面整数输入转成 int，非法时返回默认值。"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _scale_or_default(value: str) -> float:
        """把界面缩放输入转成 float，非法时返回 1.0。"""
        try:
            return parse_scale(value)
        except (TypeError, ValueError):
            return 1.0

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
        self._save_current_settings()
        self.root.destroy()


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(lambda: VideoConvWindow(settings_path), "视频转换器")


@fcmd.tool("videoconvw", help="打开视频转换器窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开视频转换器窗口。"""
    run_gui()


@fcmd.main("videoconvw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
