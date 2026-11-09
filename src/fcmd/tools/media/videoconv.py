"""videoconv - 视频格式转换工具。

基于系统 ``ffmpeg`` / ``ffprobe``，支持 m4s、mp4、avi、mkv 等常见输入格式，
输出 mp4、mkv、avi、mov、wmv、flv、webm、gif；可配置视频/音频编码器、
比特率、帧率、分辨率缩放、CRF 质量、编码预设与外部音轨。

输入支持通配符批量处理（``*`` / ``?`` / ``[]``，``**`` 递归）：
``*.m4s``、``videos/*.mp4``、``**/*.avi``。输出文件默认写入输入文件
所在目录，命名为 ``<原名>_converted.<格式>``。

示例
----
    fcmd videoconv input.mp4                        # 转换为 mp4
    fcmd videoconv input.m4s --output-format mkv    # 转换为 mkv
    fcmd videoconv "*.m4s"                          # 批量转换当前目录
    fcmd videoconv "**/*.avi" --output-format mp4   # 递归批量转换
    fcmd videoconv input.mp4 --scale 0.5            # 缩放到 50% 分辨率
    fcmd videoconv input.avi --no-audio             # 移除音轨
    fcmd videoconv input.mp4 --audio-file bg.mp3    # 替换为外部音轨
"""

from __future__ import annotations

import contextlib
import glob
import math
import queue
import re
import subprocess
import threading
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal

import fcmd
from fcmd.console import print_error

__all__ = [
    "CRF_CODECS",
    "OUTPUT_FORMATS",
    "PRESETS",
    "SUPPORTED_INPUT_FORMATS",
    "VideoOptions",
    "build_ffmpeg_command",
    "build_output_path",
    "build_scale_filter",
    "expand_input_patterns",
    "ffmpeg_version",
    "normalize_format",
    "parse_duration",
    "parse_progress_time",
    "progress_percent",
    "run_conversion",
    "validate_options",
]

_DESCRIPTION = (
    "视频格式转换（mp4/mkv/avi/mov/wmv/flv/webm/gif）：编码器、比特率、帧率、缩放、CRF、"
    "预设与外部音轨可配，支持通配符批量，依赖系统 ffmpeg"
)

#: 支持的输入格式（小写、含点）
SUPPORTED_INPUT_FORMATS: tuple[str, ...] = (
    ".mp4",
    ".m4v",
    ".m4s",
    ".mkv",
    ".avi",
    ".mov",
    ".wmv",
    ".flv",
    ".webm",
    ".mpeg",
    ".mpg",
    ".3gp",
    ".vob",
    ".mts",
    ".m2ts",
    ".divx",
    ".asf",
    ".rm",
    ".rmvb",
    ".ts",
    ".ogv",
    ".f4v",
    ".mxf",
    ".dv",
    ".mpv",
    ".m1v",
    ".m2v",
    ".3g2",
    ".swf",
)

#: 支持的输出格式（小写、含点）
OUTPUT_FORMATS: tuple[str, ...] = (".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".gif")

#: 输出格式选项（不含点），与 ``OUTPUT_FORMATS`` 一一对应
OUTPUT_FORMAT_CHOICES: tuple[str, ...] = tuple(item.lstrip(".") for item in OUTPUT_FORMATS)

#: x264/x265 使用 CRF 质量控制，其余编码器使用目标比特率
CRF_CODECS: frozenset[str] = frozenset({"libx264", "libx265"})

#: 编码预设（由快到慢）
PRESETS: tuple[str, ...] = (
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
)

DEFAULT_VIDEO_CODEC = "libx264"
DEFAULT_AUDIO_CODEC = "aac"
DEFAULT_VIDEO_BITRATE = 2500
DEFAULT_AUDIO_BITRATE = 128
DEFAULT_CRF = 23
DEFAULT_PRESET = "medium"

OutputFormat = Literal["mp4", "avi", "mkv", "mov", "wmv", "flv", "webm", "gif"]
PresetName = Literal[
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
]

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2})(?:\.(\d+))?")
_TIME_RE = re.compile(r"(?:time|out_time)=(\d+):(\d{2}):(\d{2})(?:\.(\d+))?")
_LINE_SPLIT_RE = re.compile(r"[\r\n]+")
_CONVERTED_SUFFIX = "_converted"


@dataclass(frozen=True)
class VideoOptions:
    """一次视频转换的参数集合。

    Parameters
    ----------
    output_format:
        输出格式（含点，如 ``".mp4"``）
    video_codec:
        视频编码器（``libx264`` / ``libx265`` / ``mpeg4`` / ``libvpx-vp9`` 等）
    audio_codec:
        音频编码器（``aac`` / ``mp3`` / ``opus`` 等）
    video_bitrate:
        视频比特率（kbps），仅在编码器不支持 CRF 时生效
    audio_bitrate:
        音频比特率（kbps）
    frame_rate:
        目标帧率，``None`` 表示保持源帧率
    scale:
        分辨率缩放因子，1.0 表示保持原分辨率
    crf:
        CRF 质量值（0-51，越小质量越高），仅 x264/x265 生效
    preset:
        编码预设
    remove_audio:
        是否移除音轨（优先于 ``audio_file``）
    audio_file:
        用于替换/合并的外部音频文件
    """

    output_format: str = ".mp4"
    video_codec: str = DEFAULT_VIDEO_CODEC
    audio_codec: str = DEFAULT_AUDIO_CODEC
    video_bitrate: int = DEFAULT_VIDEO_BITRATE
    audio_bitrate: int = DEFAULT_AUDIO_BITRATE
    frame_rate: int | None = None
    scale: float = 1.0
    crf: int = DEFAULT_CRF
    preset: str = DEFAULT_PRESET
    remove_audio: bool = False
    audio_file: Path | None = None


def normalize_format(value: str) -> str:
    """校验并规范化输出格式（补点、转小写）。

    Raises
    ------
    ValueError
        格式不在 ``OUTPUT_FORMATS`` 中
    """
    fmt = value.strip().lower()
    if not fmt.startswith("."):
        fmt = f".{fmt}"
    if fmt not in OUTPUT_FORMATS:
        supported = ", ".join(item.lstrip(".") for item in OUTPUT_FORMATS)
        raise ValueError(f"不支持的输出格式: {value!r}（可选: {supported}）")
    return fmt


def validate_options(options: VideoOptions) -> None:
    """校验转换参数，非法时抛出 :class:`ValueError`。"""
    normalize_format(options.output_format)
    if not options.video_codec.strip():
        raise ValueError("视频编码器不能为空")
    if not options.audio_codec.strip():
        raise ValueError("音频编码器不能为空")
    if not 0 <= options.crf <= 51:
        raise ValueError(f"CRF 须在 0-51 之间: {options.crf}")
    if options.video_bitrate <= 0:
        raise ValueError(f"视频比特率须大于 0: {options.video_bitrate}")
    if options.audio_bitrate <= 0:
        raise ValueError(f"音频比特率须大于 0: {options.audio_bitrate}")
    if options.frame_rate is not None and not 1 <= options.frame_rate <= 240:
        raise ValueError(f"帧率须在 1-240 之间: {options.frame_rate}")
    if not 0 < options.scale <= 10:
        raise ValueError(f"缩放因子须在 (0, 10] 之间: {options.scale}")
    if options.preset not in PRESETS:
        raise ValueError(f"不支持的编码预设: {options.preset!r}（可选: {', '.join(PRESETS)}）")


def build_output_path(input_path: Path, output_dir: Path | None, output_format: str) -> Path:
    """生成输出文件路径，自动去掉已有的 ``_converted`` 后缀避免重复叠加。"""
    fmt = normalize_format(output_format)
    directory = output_dir.expanduser() if output_dir is not None else input_path.parent
    base = input_path.stem
    if base.endswith(_CONVERTED_SUFFIX):
        base = base[: -len(_CONVERTED_SUFFIX)]
    return directory / f"{base}{_CONVERTED_SUFFIX}{fmt}"


def build_scale_filter(scale: float) -> str | None:
    """构建缩放滤镜；缩放因子为 1.0 时返回 ``None``。

    Raises
    ------
    ValueError
        缩放因子非正数
    """
    if scale <= 0:
        raise ValueError(f"缩放因子须大于 0: {scale}")
    if math.isclose(scale, 1.0):
        return None
    return f"scale=iw*{scale:g}:ih*{scale:g}"


def expand_input_patterns(patterns: Sequence[str]) -> list[Path]:
    """把文件路径或通配符模式展开为受支持的视频文件列表。

    通配符支持 ``*`` / ``?`` / ``[]`` 与 ``**`` 递归，结果去重并保持顺序；
    非视频扩展名与目录会被过滤。通配符无匹配时返回空列表。
    """
    files: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        raw = str(Path(pattern).expanduser())
        if any(char in raw for char in "*?["):
            # glob 支持绝对路径通配符与 "**" 递归，Path.glob 不接受绝对模式
            matches = [Path(item) for item in glob.glob(raw, recursive=True)]  # noqa: PTH207
        else:
            matches = [Path(raw)]
        for item in matches:
            if not item.is_file() or item.suffix.lower() not in SUPPORTED_INPUT_FORMATS:
                continue
            resolved = item.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(item)
    return files


def _match_seconds(match: re.Match[str]) -> float:
    """把 ``HH:MM:SS[.ff]`` 正则匹配转换为秒。"""
    hours, minutes, seconds, fraction = match.groups()
    value = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    if fraction:
        value += float(f"0.{fraction}")
    return value


def parse_duration(line: str) -> float | None:
    """从 ffmpeg 输出的 ``Duration:`` 行解析媒体总时长（秒）。"""
    match = _DURATION_RE.search(line)
    return _match_seconds(match) if match else None


def parse_progress_time(line: str) -> float | None:
    """从 ffmpeg 输出的 ``time=`` / ``out_time=`` 行解析已处理时长（秒）。"""
    match = _TIME_RE.search(line)
    return _match_seconds(match) if match else None


def progress_percent(current: float, total: float) -> int:
    """计算进度百分比（0-100），总时长非正时返回 0。"""
    if total <= 0:
        return 0
    return max(0, min(100, int(current / total * 100)))


def build_ffmpeg_command(input_path: Path, output_path: Path, options: VideoOptions) -> list[str]:
    """构建 ffmpeg 参数列表（不使用 shell，路径原样传递）。

    Raises
    ------
    ValueError
        转换参数非法
    """
    validate_options(options)
    fmt = normalize_format(options.output_format)
    external_audio = options.audio_file is not None and not options.remove_audio

    cmd = ["ffmpeg", "-y", "-hide_banner", "-i", str(input_path)]
    if external_audio:
        cmd.extend(["-i", str(options.audio_file)])

    if fmt == ".gif":
        cmd.extend(["-c:v", "gif", "-an"])
    else:
        cmd.extend(["-c:v", options.video_codec])
        if options.remove_audio:
            cmd.extend(["-an"])
        elif external_audio:
            cmd.extend(
                [
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:a",
                    options.audio_codec,
                    "-b:a",
                    f"{options.audio_bitrate}k",
                    "-shortest",
                ]
            )
        else:
            cmd.extend(["-c:a", options.audio_codec, "-b:a", f"{options.audio_bitrate}k"])

        if options.video_codec in CRF_CODECS:
            cmd.extend(["-crf", str(options.crf), "-preset", options.preset])
        else:
            cmd.extend(["-b:v", f"{options.video_bitrate}k"])

    if options.frame_rate is not None:
        cmd.extend(["-r", str(options.frame_rate)])

    scale_filter = build_scale_filter(options.scale)
    if scale_filter is not None:
        cmd.extend(["-vf", scale_filter])

    cmd.append(str(output_path))
    return cmd


def ffmpeg_version() -> str | None:
    """返回 ``ffmpeg -version`` 首行；ffmpeg 缺失或执行失败时返回 ``None``。"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    first_line = result.stdout.splitlines()
    return first_line[0].strip() if first_line else "ffmpeg"


def _pump_stream(stream: IO[str], lines: queue.Queue[str | None]) -> None:
    """后台线程：按 ``\\r`` / ``\\n`` 切分 stderr，把非空行写入队列，结束时写入 ``None``。"""
    buffer = ""
    try:
        while True:
            chunk = stream.read(1024)
            if not chunk:
                break
            buffer += chunk
            parts = _LINE_SPLIT_RE.split(buffer)
            buffer = parts.pop()
            for part in parts:
                if part.strip():
                    lines.put(part)
        if buffer.strip():
            lines.put(buffer)
    except (OSError, ValueError):  # 进程被终止时管道可能已关闭
        pass
    finally:
        lines.put(None)


def _stop_process(process: subprocess.Popen[str]) -> None:
    """尽量温和地终止 ffmpeg 进程，超时后强制结束。"""
    with contextlib.suppress(OSError):
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            process.kill()


class _ProgressTracker:
    """解析 ffmpeg stderr 行并回调进度（按百分比变化去重）。"""

    def __init__(self, callback: Callable[[int, str], None] | None) -> None:
        self.total: float | None = None
        self.last_percent = -1
        self.callback = callback

    def feed(self, line: str) -> None:
        """消费一行 ffmpeg 输出，必要时触发进度回调。"""
        if self.total is None:
            duration = parse_duration(line)
            if duration is not None and duration > 0:
                self.total = duration
                return
        current = parse_progress_time(line)
        if current is None or self.total is None or self.callback is None:
            return
        percent = progress_percent(current, self.total)
        if percent != self.last_percent:
            self.last_percent = percent
            self.callback(percent, f"转换中 {current:.1f}s / {self.total:.1f}s")


def _execute_ffmpeg(
    cmd: Sequence[str],
    progress: Callable[[int, str], None] | None,
    cancel: threading.Event | None,
) -> bool:
    """执行 ffmpeg 命令，解析进度并支持协作式取消。"""
    try:
        process = subprocess.Popen(
            list(cmd),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print_error(f"启动 ffmpeg 失败：{exc}")
        return False

    stderr = process.stderr
    if stderr is None:  # pragma: no cover - PIPE 保证非 None
        print_error("无法读取 ffmpeg 输出")
        return False

    lines: queue.Queue[str | None] = queue.Queue()
    threading.Thread(target=_pump_stream, args=(stderr, lines), daemon=True).start()

    tail: deque[str] = deque(maxlen=20)
    tracker = _ProgressTracker(progress)
    cancelled = False
    finished = False

    while not finished:
        if cancel is not None and cancel.is_set():
            if process.poll() is None:
                cancelled = True
                _stop_process(process)
            break
        try:
            line = lines.get(timeout=0.1)
        except queue.Empty:
            if process.poll() is not None:
                finished = True
            continue
        if line is None:
            break

        tail.append(line)
        tracker.feed(line)

    returncode = process.wait()
    if cancelled:
        print_error("转换已取消")
        return False
    if returncode != 0:
        detail = "\n".join(tail)
        print_error(f"ffmpeg 转换失败（返回码 {returncode}）" + (f"\n{detail}" if detail else ""))
        return False
    if progress is not None:
        progress(100, "转换完成")
    return True


def _validate_input(input_path: Path) -> bool:
    """校验输入文件存在且扩展名受支持。"""
    if not input_path.is_file():
        print_error(f"输入文件不存在：{input_path}")
        return False
    ext = input_path.suffix.lower()
    if ext not in SUPPORTED_INPUT_FORMATS:
        supported = ", ".join(sorted(SUPPORTED_INPUT_FORMATS))
        print_error(f"不支持的输入格式: {ext or '(无扩展名)'}（支持: {supported}）")
        return False
    return True


def run_conversion(
    input_path: Path,
    output_path: Path,
    options: VideoOptions,
    progress: Callable[[int, str], None] | None = None,
    cancel: threading.Event | None = None,
) -> bool:
    """校验输入并执行一次视频转换。

    Parameters
    ----------
    input_path:
        输入视频文件
    output_path:
        输出文件路径（父目录不存在时自动创建）
    options:
        转换参数
    progress:
        进度回调，参数为 ``(百分比, 描述)``
    cancel:
        协作式取消事件，置位后终止 ffmpeg 进程

    Returns
    -------
    bool
        转换成功返回 ``True``
    """
    if cancel is not None and cancel.is_set():
        print_error("转换已取消")
        return False
    if not _validate_input(input_path):
        return False

    try:
        cmd = build_ffmpeg_command(input_path, output_path, options)
    except ValueError as exc:
        print_error(str(exc))
        return False

    audio_file = options.audio_file
    if audio_file is not None and not options.remove_audio and not audio_file.is_file():
        print_error(f"外部音频文件不存在：{audio_file}")
        return False

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print_error(f"创建输出目录失败：{exc}")
        return False

    return _execute_ffmpeg(cmd, progress, cancel)


@fcmd.tool(
    "videoconv",
    help="视频格式转换（编码器/比特率/帧率/缩放/CRF/预设/外部音轨，支持通配符批量）",
    description=_DESCRIPTION,
)
def videoconv_cmd(  # noqa: PLR0913, PLR0917
    inputs: list[str],
    output_dir: Path | None = None,
    output_format: OutputFormat = "mp4",
    video_codec: str = DEFAULT_VIDEO_CODEC,
    audio_codec: str = DEFAULT_AUDIO_CODEC,
    video_bitrate: int = DEFAULT_VIDEO_BITRATE,
    audio_bitrate: int = DEFAULT_AUDIO_BITRATE,
    frame_rate: int | None = None,
    scale: float = 1.0,
    crf: int = DEFAULT_CRF,
    preset: PresetName = DEFAULT_PRESET,
    no_audio: bool = False,
    audio_file: Path | None = None,
) -> None:
    """在不同格式之间转换视频文件，支持通配符批量处理。

    Parameters
    ----------
    inputs:
        输入视频文件或通配符模式（如 ``*.m4s``、``**/*.mp4``），可多个
    output_dir:
        输出目录，默认与输入文件同目录
    output_format:
        输出格式（默认 mp4）
    video_codec:
        视频编码器（默认 libx264）
    audio_codec:
        音频编码器（默认 aac）
    video_bitrate:
        视频比特率 kbps，非 CRF 编码器生效（默认 2500）
    audio_bitrate:
        音频比特率 kbps（默认 128）
    frame_rate:
        目标帧率，省略时保持源帧率
    scale:
        分辨率缩放因子（默认 1.0，0.5 表示缩放到一半）
    crf:
        恒定速率因子 0-51，越小质量越高（默认 23）
    preset:
        编码预设（默认 medium）
    no_audio:
        移除音轨
    audio_file:
        用于替换/合并的外部音频文件
    """
    options = VideoOptions(
        output_format=normalize_format(output_format),
        video_codec=video_codec,
        audio_codec=audio_codec,
        video_bitrate=video_bitrate,
        audio_bitrate=audio_bitrate,
        frame_rate=frame_rate,
        scale=scale,
        crf=crf,
        preset=preset,
        remove_audio=no_audio,
        audio_file=audio_file.expanduser() if audio_file is not None else None,
    )
    try:
        validate_options(options)
    except ValueError as exc:
        print_error(str(exc))
        return

    if ffmpeg_version() is None:
        print_error("未找到 ffmpeg，请安装并加入 PATH")
        return

    files = expand_input_patterns(inputs)
    if not files:
        print_error(f"未找到受支持的视频文件：{', '.join(inputs)}")
        return

    resolved_output_dir = output_dir.expanduser() if output_dir is not None else None

    def make_progress(name: str) -> Callable[[int, str], None]:
        state = {"last": -1}

        def callback(percent: int, message: str) -> None:
            if percent == 100 or percent - state["last"] >= 5:
                state["last"] = percent
                print(f"  {name}: {percent}% {message}")

        return callback

    successful = 0
    for index, source in enumerate(files, 1):
        target = build_output_path(source, resolved_output_dir, options.output_format)
        if len(files) > 1:
            print(f"[{index}/{len(files)}] {source.name}")
        print(f"  {source} -> {target}")
        if run_conversion(source, target, options, progress=make_progress(source.name)):
            successful += 1

    failed = len(files) - successful
    if len(files) > 1:
        print(f"批量转换完成：成功 {successful}，失败 {failed}，共 {len(files)} 个")
    elif successful == 0:
        print_error("转换失败")
    else:
        print("转换成功")


@fcmd.main("videoconv")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
