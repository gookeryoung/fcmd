"""filecheck - NAS 文件完整性检查工具。

针对 NAS 故障（磁盘坏块、文件静默丢失、过期句柄等）场景，快速扫描指定
目录下所有视频剧集文件。为避免检查过慢，每个文件只读取头部与尾部各
64KB（可调），不读取整个文件；多线程并发提升局域网吞吐。

检查内容：
1. 错误（疑似丢失/损坏）：0 字节文件、无法打开或读取（I/O 错误）、断链；
2. 警告（可疑）：文件过小、文件头无法识别为任何已知容器（疑似损坏/占位）；
3. 提示：文件尾部全为 0（疑似坏块填充或截断）。

容器识别由 ``puremagic`` 的签名库完成（需 ``pip install fcmd[filecheck]``），
仅对 ``.ts`` / ``.m2ts`` / ``.mxf`` / ``.iso`` 等 puremagic 覆盖不足的格式保留极小
兜底表；扩展名与实际容器不符但内容可识别为已知容器时不作报告（播放器按内容
识别可正常播放）；
4. 剧集缺失：按 ``S01E01`` / ``EP01`` / ``第01集`` / 纯数字等命名规则识别
   集号，报告同组剧集内部疑似缺失的集数。

示例
----
    fcmd filecheck                          # 检查当前目录
    fcmd filecheck --path /volume1/media    # 检查指定 NAS 目录
    fcmd filecheck --all-files              # 检查所有文件而不仅是视频
    fcmd filecheck --workers 32             # 加大并发
"""

from __future__ import annotations

import os
import re
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import fcmd

__all__ = [
    "Issue",
    "MissingEpisodes",
    "ScanResult",
    "build_report",
    "check_one_file",
    "detect_missing_episodes",
    "parse_episode",
    "run_filecheck",
    "scan_directory",
]

# 视频文件扩展名集合（小写、含点）
VIDEO_EXTS = frozenset(
    {
        ".mp4",
        ".m4v",
        ".mkv",
        ".webm",
        ".avi",
        ".divx",
        ".mov",
        ".wmv",
        ".asf",
        ".flv",
        ".f4v",
        ".ts",
        ".tp",
        ".trp",
        ".m2ts",
        ".mts",
        ".mpg",
        ".mpeg",
        ".vob",
        ".rm",
        ".rmvb",
        ".3gp",
        ".iso",
        ".ogm",
        ".mxf",
    }
)

# 跳过的 NAS 系统目录名（回收站、缩略图、快照等）
SKIP_DIRS = frozenset(
    {
        "$RECYCLE.BIN",
        "System Volume Information",
        "Recycled",
        "Recycler",
        "#recycle",
        "#snapshot",
        ".recycle",
        ".Trash",
        ".Trashes",
        ".@__thumb",
        "@eaDir",
        "eaDir",
        "lost+found",
        ".snapshot",
        ".snapshots",
        ".zfs",
        ".trash",
    }
)

# 跳过的系统垃圾文件名
SKIP_FILE_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})

# 集号识别正则（按优先级依次尝试）
_RE_SEASON_EP = re.compile(r"[Ss](\d{1,2})[\s._\-]*[Ee][Pp]?(\d{1,4})")
_RE_CN_EP = re.compile(r"第\s*(\d{1,4})\s*[集话話期回]")
_RE_EP = re.compile(r"(?:^|[^A-Za-z0-9])[Ee][Pp]?(\d{1,4})(?![0-9pP])")
_RE_FUZZY_NUM = re.compile(r"(?:^|[\s._\-\[])(\d{1,3})(?=[\s._\-\]]|$)")
_RE_STRIP_EDGE = re.compile(r"^[\s._\-\[\]()（）]+|[\s._\-\[\]()（）]+$")
# 软件版本号特征（如 ubuntu-24.04、v3.1.5）：紧跟数字后的点分 1~2 位小数字链
_RE_VERSION_TAIL = re.compile(r"\.\d{1,2}(?:\.\d{1,2})*(?!\d|\.\d)")
# 数字位于点分版本号链的尾部（前一字符为 "数字."）
_RE_VERSION_HEAD = re.compile(r"\d\.$")

# ISOBMFF 家族扩展名：puremagic 未命中时可走结构校验兜底
_ISOBMFF_EXTS = frozenset({".mp4", ".m4v", ".mov", ".3gp"})

# 头部短于该长度时无法可靠识别容器，跳过校验（另有"过小"警告）
_MIN_HEAD_BYTES = 8

# puremagic 候选结果中视为"内容可识别"的容器扩展名。
# 纯文本集合，由 puremagic 的签名库负责字节匹配，本模块不再维护签名表。
_RECOGNIZED_EXTS = frozenset(
    {
        ".mp4",
        ".m4v",
        ".mov",
        ".3gp",
        ".3g2",
        ".mkv",
        ".mka",
        ".webm",
        ".avi",
        ".divx",
        ".flv",
        ".f4v",
        ".mpg",
        ".mpeg",
        ".vob",
        ".m2v",
        ".wmv",
        ".asf",
        ".wma",
        ".rm",
        ".rmvb",
        ".ra",
        ".ogg",
        ".oga",
        ".ogv",
        ".ogm",
        ".ts",
        ".m2ts",
        ".mts",
        ".mxf",
        ".iso",
    }
)

# 容器格式名称：仅用于警告文案
_FORMAT_NAMES: dict[str, str] = {}
for _name, _exts in [
    ("MP4/MOV", (".mp4", ".m4v", ".mov", ".3gp")),
    ("MKV/WebM", (".mkv", ".webm")),
    ("AVI", (".avi", ".divx")),
    ("FLV", (".flv", ".f4v")),
    ("MPEG-TS", (".ts", ".tp", ".trp")),
    ("M2TS", (".m2ts", ".mts")),
    ("MPEG-PS", (".mpg", ".mpeg", ".vob")),
    ("WMV/ASF", (".wmv", ".asf")),
    ("RM/RMVB", (".rm", ".rmvb")),
    ("Ogg", (".ogm",)),
    ("MXF", (".mxf",)),
    ("ISO", (".iso",)),
]:
    for _ext in _exts:
        _FORMAT_NAMES[_ext] = _name

# puremagic 覆盖不足的容器：极小结构兜底（扩展名 -> (偏移, 特征字节)）。
# 只保留这几项，其余格式一律交给 puremagic 的签名库维护。
_FALLBACK_SIGS: dict[str, tuple[int, bytes]] = {
    ".ts": (0, b"\x47"),
    ".tp": (0, b"\x47"),
    ".trp": (0, b"\x47"),
    ".m2ts": (4, b"\x47"),
    ".mts": (4, b"\x47"),
    ".mxf": (0, b"\x06\x0e\x2b\x34"),
    ".iso": (0x8001, b"CD001"),
    ".ogm": (0, b"OggS"),
}

# 惰性导入的 puremagic 模块（首次文件头校验时填充）
_puremagic: Any = None


@dataclass(frozen=True)
class Issue:
    """单个文件的检查问题记录。

    Parameters
    ----------
    level:
        级别：``"错误"`` / ``"警告"`` / ``"提示"``
    path:
        文件完整路径
    message:
        问题描述
    """

    level: str
    path: str
    message: str


@dataclass(frozen=True)
class MissingEpisodes:
    """一组剧集的疑似缺失集数记录。"""

    dirpath: str
    prefix: str  # 剧集名（文件名前缀，已规范化展示）
    season: int | None
    found: int  # 识别到的集号个数
    lo: int  # 最小集号
    hi: int  # 最大集号
    missing: tuple[int, ...]  # 组内缺失的集号


@dataclass
class ScanResult:
    """一次扫描的完整结果。"""

    root: Path
    chunk_kb: int
    all_files: bool
    issues: list[Issue] = field(default_factory=list)
    dir_errors: list[str] = field(default_factory=list)
    missing: list[MissingEpisodes] = field(default_factory=list)
    stats: dict[str, float] = field(default_factory=dict)


def _require_puremagic() -> Any | None:
    """惰性导入 puremagic；缺失时返回 ``None``（由调用方决定是否提示）。

    puremagic 的签名库较大，``import fcmd`` 与工具发现路径不应为其付费。
    """
    global _puremagic  # noqa: PLW0603 - 惰性导入需 global 注入模块，避免工具发现时加载
    if _puremagic is not None:
        return _puremagic
    try:
        import puremagic
    except ImportError:  # pragma: no cover - 由 extra 保证，缺库时降级
        return None
    _puremagic = puremagic
    return puremagic


def _magic_matches_video(head: bytes) -> bool:
    """``puremagic`` 是否把内容识别为已知视频/媒体容器。

    取 ``magic_string`` 的全部候选（按置信度降序），只要任一候选扩展名落入
    ``_RECOGNIZED_EXTS`` 即视为可识别——纯低置信度的无关候选（如全 0 头部
    被误判为绣花文件）不会单独导致通过。

    Parameters
    ----------
    head:
        文件头部字节

    Returns
    -------
    bool
        内容可识别为已知容器返回 ``True``
    """
    module = _require_puremagic()
    if module is None:
        return False
    try:
        candidates = module.magic_string(head)
    except Exception:  # puremagic 内部解析异常不应中断整轮扫描
        return False
    return any((getattr(candidate, "extension", "") or "").lower() in _RECOGNIZED_EXTS for candidate in candidates)


def _fallback_ok(head: bytes, ext: str) -> bool:
    """扩展名专属的极小结构兜底（puremagic 未覆盖的容器）。

    Parameters
    ----------
    head:
        文件头部字节
    ext:
        小写扩展名（含点）

    Returns
    -------
    bool
        命中兜底特征返回 ``True``
    """
    sig = _FALLBACK_SIGS.get(ext)
    if sig is None:
        return False
    offset, magic = sig
    return head[offset : offset + len(magic)] == magic


def _looks_like_isobmff(head: bytes, file_size: int) -> bool:
    """按 ISOBMFF 结构校验文件头（不依赖具体 box 类型白名单）。

    合法判定：前 4 字节为 big-endian box 长度，后 4 字节为可打印 ASCII box
    类型；支持三种合法长度形态——普通长度（≥8）、largesize（前 4 字节为 1，
    8~16 字节为 64 位长度）、到文件尾的 mdat（前 4 字节为 0）。声明的 box
    长度不得超过实际文件大小（超限即疑似截断或占位内容，如 HTML 错误页）。

    Parameters
    ----------
    head:
        文件头部字节
    file_size:
        文件实际大小（字节）

    Returns
    -------
    bool
        结构合法返回 ``True``
    """
    if len(head) < 8:
        return False
    size = int.from_bytes(head[0:4], "big")
    box_type = head[4:8]
    if not all(0x20 <= b <= 0x7E for b in box_type):
        return False
    if size == 1:  # largesize：真实长度在 8~16 字节
        if len(head) < 16:
            return False
        return int.from_bytes(head[8:16], "big") >= 16 and int.from_bytes(head[8:16], "big") <= file_size
    if size == 0:  # 长度到文件尾（如未定长的 mdat）
        return True
    return 8 <= size <= file_size


def _header_problem(head: bytes, ext: str, file_size: int) -> tuple[str, str] | None:
    """检查文件头与容器格式，返回 ``(级别, 问题描述)`` 或 ``None``。

    判定顺序（自上而下，尽量减少对可正常播放文件的误报）：
    1. 扩展名不是视频容器、或 ``puremagic`` 缺失（无法识别容器）→ 不校验；
    2. 头部不足以识别容器（短于 ``_MIN_HEAD_BYTES``）→ 跳过校验
       （小文件另有"过小"警告）；
    3. ``puremagic`` 命中已知视频容器 → 通过（扩展名与实际容器不符但内容
       可识别，播放器按内容识别可正常播放，不作报告）；
    4. 扩展名专属的极小兜底表命中（``.ts``/``.m2ts``/``.mxf``/``.iso`` 等
       puremagic 覆盖不足的格式）→ 通过；
    5. ISOBMFF 家族且结构合法（非白名单 box 类型，如 msdh/PRFT 开头）→ 通过；
    6. 以上均不满足 → **警告**（疑似损坏、占位或不完整文件）。

    Parameters
    ----------
    head:
        文件头部字节
    ext:
        小写扩展名（含点）
    file_size:
        文件实际大小（字节）

    Returns
    -------
    tuple[str, str] | None
        ``(级别, 问题描述)``；无需报告时返回 ``None``
    """
    if ext not in VIDEO_EXTS or len(head) < _MIN_HEAD_BYTES:
        return None
    # 缺少 puremagic 时几乎所有真实视频都会被误报，直接降级为不校验
    if _require_puremagic() is None:
        return None
    if _magic_matches_video(head):
        return None
    if _fallback_ok(head, ext):
        return None
    if ext in _ISOBMFF_EXTS and _looks_like_isobmff(head, file_size):
        return None
    return ("警告", f"{_FORMAT_NAMES.get(ext, ext)} 头部标识不符（疑似损坏、占位或不完整文件）")


def check_one_file(path: Path, chunk_size: int) -> list[Issue]:
    """检查单个文件，只读取头部与尾部各 ``chunk_size`` 字节。

    Parameters
    ----------
    path:
        文件完整路径
    chunk_size:
        头尾各读取的字节数

    Returns
    -------
    list[Issue]
        问题列表（无问题则返回空列表）
    """
    ext = path.suffix.lower()
    try:
        if path.is_symlink() and not path.exists():
            return [Issue("错误", str(path), "符号链接目标不存在（断链）")]
        try:
            size = path.stat().st_size
        except OSError as exc:
            return [Issue("错误", str(path), f"无法获取文件信息：{exc}")]
        if size == 0:
            return [Issue("错误", str(path), "文件大小为 0 字节（疑似丢失或占位文件）")]

        tail = b""
        try:
            with path.open("rb") as fh:
                head = fh.read(chunk_size)
                if size > len(head):
                    fh.seek(max(size - chunk_size, 0))
                    tail = fh.read(chunk_size)
        except OSError as exc:
            return [Issue("错误", str(path), f"文件打开或读取失败（疑似坏块/文件丢失）：{exc}")]
    except OSError as exc:  # pragma: no cover - lstat 等系统调用失败，难以稳定复现
        return [Issue("错误", str(path), f"文件访问失败：{exc}")]

    issues: list[Issue] = []
    if size < 4096:
        issues.append(Issue("警告", str(path), f"文件过小（{size} 字节），疑似不完整"))
    problem = _header_problem(head, ext, size)
    if problem is not None:
        level, message = problem
        issues.append(Issue(level, str(path), message))
    if len(tail) == chunk_size and tail == b"\x00" * chunk_size and size >= 2 * chunk_size:
        issues.append(Issue("提示", str(path), "文件尾部全部为 0（疑似坏块填充或截断）"))
    return issues


def parse_episode(stem: str) -> tuple[str, int | None, int] | None:
    """从文件名（不含扩展名）中识别集号。

    按 ``S01E05`` → ``第01集`` → ``EP03`` → 独立数字的优先级识别；
    独立数字排除 1900-2100（年份）、超过 999 的编号与软件版本号
    （如 ``ubuntu-24.04``、``v3.1.5``），减少误报。

    Parameters
    ----------
    stem:
        文件名去扩展名后的部分

    Returns
    -------
    tuple[str, int | None, int] | None
        ``(前缀, 季号或 None, 集号)``；无法识别返回 ``None``
    """
    match = _RE_SEASON_EP.search(stem)
    if match:
        return stem[: match.start()], int(match.group(1)), int(match.group(2))
    match = _RE_CN_EP.search(stem)
    if match:
        return stem[: match.start()], None, int(match.group(1))
    match = _RE_EP.search(stem)
    if match:
        return stem[: match.start()], None, int(match.group(1))
    match = _RE_FUZZY_NUM.search(stem)
    if match:
        num = int(match.group(1))
        # 排除软件版本号（如 ubuntu-24.04、v3.1.5）：集号前后接点分小数字链
        # 视为版本号而非剧集（清晰度 1080p/720p 等不影响识别）
        if (
            num <= 999
            and not 1900 <= num <= 2100
            and not _RE_VERSION_TAIL.match(stem, match.end())
            and not _RE_VERSION_HEAD.search(stem[: match.start(1)])
        ):
            return stem[: match.start()], None, num
    return None


def _norm_prefix(prefix: str) -> str:
    """规范化剧集名前缀，用于同目录下的剧集分组。"""
    return re.sub(r"[\W_]+", "", prefix, flags=re.UNICODE).lower()


def _display_prefix(prefix: str) -> str:
    """生成用于报告展示的剧集名。"""
    cleaned = _RE_STRIP_EDGE.sub("", prefix)
    return cleaned if cleaned else "(文件名为纯数字)"


def detect_missing_episodes(video_records: list[tuple[str, str]]) -> list[MissingEpisodes]:
    """根据扫描到的视频文件名检测同组剧集内部缺失的集数。

    同目录、同前缀、同季号的文件归为一组；集号少于 2 个的组不判断，
    只报告组内区间 ``[min, max]`` 的内部缺口（避免"只下载了后半部"误报）。

    Parameters
    ----------
    video_records:
        ``(目录, 文件名)`` 列表，仅含视频扩展名文件

    Returns
    -------
    list[MissingEpisodes]
        疑似缺失集数的分组列表（按目录、剧集名排序）
    """
    groups: dict[tuple[str, str, int | None], set[int]] = defaultdict(set)
    prefixes: dict[tuple[str, str, int | None], str] = {}
    for dirpath, filename in video_records:
        parsed = parse_episode(Path(filename).stem)
        if parsed is None:
            continue
        raw_prefix, season, num = parsed
        key = (dirpath, _norm_prefix(raw_prefix), season)
        groups[key].add(num)
        prefixes.setdefault(key, _display_prefix(raw_prefix))

    results: list[MissingEpisodes] = []
    for key, nums in groups.items():
        if len(nums) < 2:
            continue
        dirpath, _, season = key
        internal = tuple(n for n in range(min(nums), max(nums) + 1) if n not in nums)
        if internal:
            results.append(
                MissingEpisodes(
                    dirpath=dirpath,
                    prefix=prefixes[key],
                    season=season,
                    found=len(nums),
                    lo=min(nums),
                    hi=max(nums),
                    missing=internal,
                )
            )
    results.sort(key=lambda r: (r.dirpath, r.prefix))
    return results


def _skip_dir(name: str) -> bool:
    """判断目录是否应跳过（NAS 系统目录等）。"""
    return name in SKIP_DIRS or name.startswith(".@") or name.startswith("#")


def _skip_file(name: str) -> bool:
    """判断文件是否应跳过（系统垃圾文件等）。"""
    return name in SKIP_FILE_NAMES or name.startswith("._")


def _walk_error(err: OSError) -> str:
    """将 :func:`os.walk` 的目录遍历错误转换为描述文本。"""
    return f"{err.filename or '?'}：{err}"


def scan_directory(root: Path, workers: int, chunk_size: int, all_files: bool) -> ScanResult:
    """遍历根目录并并发检查文件。

    Parameters
    ----------
    root:
        扫描根目录
    workers:
        并发线程数
    chunk_size:
        每个文件头尾各读取的字节数
    all_files:
        是否检查所有文件（否则只检查视频扩展名文件）

    Returns
    -------
    ScanResult
        扫描结果（含问题、目录错误、缺失集数与统计信息）
    """
    result = ScanResult(root=root, chunk_kb=chunk_size // 1024, all_files=all_files)
    video_records: list[tuple[str, str]] = []
    futures: dict[Future[list[Issue]], Path] = {}
    started = time.monotonic()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: result.dir_errors.append(_walk_error(e))):
            dirnames[:] = [d for d in dirnames if not _skip_dir(d)]
            for filename in filenames:
                if _skip_file(filename):
                    continue
                ext = Path(filename).suffix.lower()
                if ext in VIDEO_EXTS:
                    video_records.append((dirpath, filename))
                if all_files or ext in VIDEO_EXTS:
                    path = Path(dirpath) / filename
                    futures[pool.submit(check_one_file, path, chunk_size)] = path

        for future in as_completed(futures):
            result.issues.extend(future.result())

    result.stats = {
        "dirs": float(sum(1 for _ in os.walk(root))),
        "checked": float(len(futures)),
        "elapsed": round(time.monotonic() - started, 2),
    }
    result.issues.sort(key=lambda i: (i.level != "错误", i.path))
    result.missing = detect_missing_episodes(video_records)
    return result


def build_report(result: ScanResult) -> str:
    """生成文本检查报告。

    Parameters
    ----------
    result:
        扫描结果

    Returns
    -------
    str
        报告全文
    """
    errors = [i for i in result.issues if i.level == "错误"]
    warns = [i for i in result.issues if i.level == "警告"]
    notes = [i for i in result.issues if i.level == "提示"]
    lines: list[str] = [
        "NAS 文件完整性检查报告",
        "=" * 60,
        f"检查时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"检查根目录：{result.root}",
        f"检查范围：{'所有文件' if result.all_files else '视频文件'}"
        f"（每个文件仅读取头尾各 {result.chunk_kb}KB，未做全文件校验）",
        f"耗时：{result.stats.get('elapsed', 0)} 秒",
        "",
        "一、检查概览",
        f"  遍历目录数：{result.stats.get('dirs', 0):.0f}",
        f"  检查文件数：{result.stats.get('checked', 0):.0f}",
        f"  错误（疑似丢失/损坏）：{len(errors)} 个",
        f"  警告（可疑）：{len(warns)} 个",
        f"  提示：{len(notes)} 个",
        f"  目录访问错误：{len(result.dir_errors)} 个",
        f"  疑似缺失集数的剧集：{len(result.missing)} 组",
        "",
    ]

    lines.append("二、疑似丢失或损坏的文件")
    if errors:
        for idx, item in enumerate(errors, 1):
            lines.append(f"  {idx}. {item.path}")
            lines.append(f"     原因：{item.message}")
    else:
        lines.append("  （无）")
    lines.append("")

    lines.append("三、可疑文件（警告/提示）")
    if warns or notes:
        for idx, item in enumerate([*warns, *notes], 1):
            lines.append(f"  {idx}. [{item.level}] {item.path}")
            lines.append(f"     原因：{item.message}")
    else:
        lines.append("  （无）")
    lines.append("")

    lines.append("四、剧集疑似缺失集数（基于文件名集号识别，仅供参考）")
    if result.missing:
        for idx, item in enumerate(result.missing, 1):
            season_txt = f"第 {item.season} 季，" if item.season is not None else ""
            lines.append(
                f"  {idx}. 目录：{item.dirpath}\n"
                f"     剧集：{item.prefix}（{season_txt}识别到 {item.found} 个集号，"
                f"范围 {item.lo}-{item.hi}）\n"
                f"     疑似缺失：{', '.join(str(n) for n in item.missing)}"
            )
    else:
        lines.append("  （无）")
    lines.append("")

    lines.append("五、目录访问错误")
    if result.dir_errors:
        for idx, msg in enumerate(result.dir_errors, 1):
            lines.append(f"  {idx}. {msg}")
    else:
        lines.append("  （无）")
    lines.append("")
    lines.append(
        "说明：本工具仅读取每个文件头尾少量字节做初步筛查；对错误级文件建议\n"
        "      进一步人工确认或用播放器/全量校验工具复核。"
    )
    return "\n".join(lines)


def run_filecheck(
    path: Path | None = None,
    workers: int = min(16, max(4, (os.cpu_count() or 4) * 2)),
    chunk_kb: int = 64,
    all_files: bool = False,
) -> None:
    """执行文件完整性检查并输出报告到被扫描的根目录。

    Parameters
    ----------
    path:
        要检查的根目录，``None`` 时使用当前工作目录
    workers:
        并发检查线程数
    chunk_kb:
        每个文件头尾各读取的大小，单位 KB（最小 4）
    all_files:
        是否检查所有文件而不仅是视频文件
    """
    root = path.expanduser().resolve() if path is not None else Path.cwd()
    if not root.is_dir():
        print(f"错误：路径不存在或不是目录：{root}")
        return
    size_kb = max(4, chunk_kb)

    if _require_puremagic() is None:
        print("未安装 puremagic 库，将跳过文件头容器校验；请安装: pip install fcmd[filecheck]")

    print(f"开始扫描：{root}")
    result = scan_directory(root, max(1, workers), size_kb * 1024, all_files)
    report = build_report(result)

    report_path = root / f"文件检查报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    # utf-8-sig 便于 Windows 记事本直接打开；surrogateescape 文件名降级为替换符
    with report_path.open("w", encoding="utf-8-sig", errors="replace") as fh:
        fh.write(report + "\n")

    errors = [i for i in result.issues if i.level == "错误"]
    warns = [i for i in result.issues if i.level == "警告"]
    print(f"扫描完成：检查 {result.stats.get('checked', 0):.0f} 个文件，耗时 {result.stats.get('elapsed', 0)} 秒")
    print(f"  错误（疑似丢失/损坏）：{len(errors)} 个")
    print(f"  警告（可疑）：{len(warns)} 个")
    print(f"  目录访问错误：{len(result.dir_errors)} 个")
    print(f"  疑似缺失集数的剧集：{len(result.missing)} 组")
    print(f"报告已输出：{report_path}")


@fcmd.tool("filecheck", help="NAS 文件完整性检查：筛查丢失/损坏的视频剧集文件与缺失集数")
def filecheck_cmd(
    path: Path | None = None,
    workers: int = min(16, max(4, (os.cpu_count() or 4) * 2)),
    chunk_kb: int = 64,
    all_files: bool = False,
) -> None:
    """快速筛查 NAS 故障导致的丢失/损坏视频剧集文件与缺失集数。

    每个文件只读取头尾各 64KB（可调），不读取整个文件，多线程并发检查。
    报告输出到被扫描的根目录下。

    Parameters
    ----------
    path:
        要检查的根目录（默认：当前路径）
    workers:
        并发检查线程数（默认：CPU 核数的 2 倍，上限 16）
    chunk_kb:
        每个文件头尾各读取的大小，单位 KB（默认：64）
    all_files:
        检查所有文件而不仅是视频文件
    """
    run_filecheck(path=path, workers=workers, chunk_kb=chunk_kb, all_files=all_files)


@fcmd.main("filecheck")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
