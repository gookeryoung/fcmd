"""videoconvw - 视频格式转换器（Qt GUI）。

选择输入视频与输出目录，设置输出格式、视频/音频编码器、比特率、帧率、
缩放、CRF、预设与外部音轨，一键转换。转换在后台线程执行，ffmpeg 的进度
与日志实时回传界面，可随时中止；设置（上次输入/输出、编码参数、窗口位置）
保存在 ``${FCMD_HOME:-~/.fcmd}/ui/videoconvw.json``，下次启动自动恢复。

示例
----
    fcmd videoconvw        # 打开视频转换器窗口
    videoconvw             # 全局命令等价入口
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import geometry as _guiwindow
from fcmd.tools.media.videoconv import (
    DEFAULT_AUDIO_BITRATE,
    DEFAULT_AUDIO_CODEC,
    DEFAULT_CRF,
    DEFAULT_PRESET,
    DEFAULT_VIDEO_BITRATE,
    DEFAULT_VIDEO_CODEC,
    PRESETS,
    normalize_format,
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
    "视频转换器窗口版（Qt）：文件/输出目录选择、格式与编码参数可视化、"
    "进度与日志实时回传、可中断，设置持久化到 ~/.fcmd/ui/videoconvw.json，依赖系统 ffmpeg"
)

DEFAULT_GEOMETRY = "980x760+200+80"
MIN_WIDTH = 900
MIN_HEIGHT = 640


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
# GUI 入口
# ---------------------------------------------------------------------- #
def run_gui(settings_path: Path | None = None) -> None:
    """启动 Qt 窗口（高 DPI 与主题由 ``gui.qt.app`` 统一处理）。"""
    try:
        from fcmd.tools.media._videoconvw_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 安装完整时不会触发
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc

    run_qt(settings_path)


@fcmd.tool("videoconvw", help="打开视频转换器窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开视频转换器窗口。"""
    run_gui()


@fcmd.main("videoconvw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
