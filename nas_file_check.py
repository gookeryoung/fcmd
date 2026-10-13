#!/usr/bin/env python3
"""NAS 局域网盘文件完整性检查工具。

针对 NAS 故障（磁盘坏块、文件静默丢失、过期句柄等）场景，快速扫描指定
目录下所有视频剧集文件。为避免检查过慢，每个文件只读取头部与尾部各
64KB（可调），不读取整个文件。

检查内容：
1. 错误（疑似丢失/损坏）：0 字节文件、无法打开或读取（I/O 错误）、断链；
2. 警告（可疑）：文件过小、文件头与容器格式特征不符；
3. 提示：文件尾部全为 0（疑似坏块填充或截断）；
4. 剧集缺失：按 ``S01E01`` / ``EP01`` / ``第01集`` / 纯数字等命名规则识别
   集号，报告同组剧集内部疑似缺失的集数。

用法::

    python nas_file_check.py [路径] [--workers N] [--chunk-kb 64] [--all]

默认检查当前路径，检查报告输出到被扫描的根目录下。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

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

# MP4 系容器在偏移 4~8 字节处允许出现的 box 类型
_MP4_SPECS = tuple(
    (4, magic)
    for magic in (
        b"ftyp",
        b"moov",
        b"mdat",
        b"free",
        b"skip",
        b"wide",
        b"pnot",
        b"styp",
        b"uuid",
        b"meta",
    )
)

# 容器格式头部特征表：扩展名 -> (格式描述, (偏移, 特征字节) 元组)。
# 特征字节中的 '?' 为单字节通配符；".iso" 的特征位于偏移 0x8001 处。
_HEADER_SPECS: dict[str, tuple[str, tuple[tuple[int, bytes], ...]]] = {}
for _exts, _name, _specs in [
    ((".mp4", ".m4v", ".mov", ".3gp"), "MP4/MOV", _MP4_SPECS),
    ((".mkv", ".webm"), "MKV/WebM", ((0, b"\x1a\x45\xdf\xa3"),)),
    ((".avi", ".divx"), "AVI", ((0, b"RIFF????AVI "),)),
    ((".flv", ".f4v"), "FLV", ((0, b"FLV"),)),
    ((".ts", ".tp", ".trp"), "MPEG-TS", ((0, b"\x47"),)),
    ((".m2ts", ".mts"), "M2TS", ((4, b"\x47"),)),
    ((".mpg", ".mpeg", ".vob"), "MPEG-PS", ((0, b"\x00\x00\x01\xba"), (0, b"\x00\x00\x01\xb3"))),
    ((".wmv", ".asf"), "WMV/ASF", ((0, b"\x30\x26\xb2\x75\x8e\x66\xcf\x11"),)),
    ((".rm", ".rmvb"), "RM/RMVB", ((0, b".RMF"),)),
    ((".iso",), "ISO", ((0x8001, b"CD001"),)),
]:
    for _ext in _exts:
        _HEADER_SPECS[_ext] = (_name, _specs)


@dataclass(frozen=True)
class Issue:
    """单个文件的检查问题记录。"""

    level: str  # 级别：错误 / 警告 / 提示
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


def _magic_ok(head: bytes, offset: int, magic: bytes) -> bool:
    """检查 ``head`` 在 ``offset`` 处是否匹配特征字节（'?' 为单字节通配符）。"""
    part = head[offset : offset + len(magic)]
    if len(part) != len(magic):
        return False
    return all(p == m or m == ord("?") for p, m in zip(part, magic, strict=True))


def _header_problem(head: bytes, ext: str) -> str | None:
    """检查文件头是否符合容器格式特征，返回问题描述或 ``None``。"""
    spec = _HEADER_SPECS.get(ext)
    if spec is None:
        return None
    name, magics = spec
    if any(_magic_ok(head, offset, magic) for offset, magic in magics):
        return None
    return f"{name} 头部标识不符（疑似损坏、占位或不完整文件）"


def check_one_file(path: Path, chunk_size: int) -> list[Issue]:
    """检查单个文件，只读取头部与尾部各 ``chunk_size`` 字节。

    :param path: 文件完整路径
    :param chunk_size: 头尾各读取的字节数
    :return: 问题列表（无问题则返回空列表）
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
    except OSError as exc:  # lstat 等系统调用失败
        return [Issue("错误", str(path), f"文件访问失败：{exc}")]

    issues: list[Issue] = []
    if size < 4096:
        issues.append(Issue("警告", str(path), f"文件过小（{size} 字节），疑似不完整"))
    problem = _header_problem(head, ext)
    if problem is not None:
        issues.append(Issue("警告", str(path), problem))
    if len(tail) == chunk_size and tail == b"\x00" * chunk_size and size >= 2 * chunk_size:
        issues.append(Issue("提示", str(path), "文件尾部全部为 0（疑似坏块填充或截断）"))
    return issues


def parse_episode(stem: str) -> tuple[str, int | None, int] | None:
    """从文件名（不含扩展名）中识别集号。

    :param stem: 文件名去扩展名后的部分
    :return: ``(前缀, 季号或 None, 集号)``；无法识别返回 ``None``
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
        if num <= 999 and not 1900 <= num <= 2100:  # 排除年份与超大编号
            return stem[: match.start()], None, num
    return None


def _norm_prefix(prefix: str) -> str:
    """规范化剧集名前缀，用于同目录下的剧集分组。"""
    return re.sub(r"[\W_]+", "", prefix, flags=re.UNICODE).lower()


def _display_prefix(prefix: str) -> str:
    """生成用于报告展示的剧集名。"""
    cleaned = _RE_STRIP_EDGE.sub("", prefix)
    return cleaned if cleaned else "(文件名为纯数字)"


def detect_missing_episodes(
    video_records: list[tuple[str, str]],
) -> list[MissingEpisodes]:
    """根据扫描到的视频文件名检测同组剧集内部缺失的集数。

    :param video_records: ``(目录, 文件名)`` 列表，仅含视频扩展名文件
    :return: 疑似缺失集数的分组列表
    """
    groups: dict[tuple[str, str, int | None], set[int]] = defaultdict(set)
    prefixes: dict[tuple[str, str, int | None], str] = {}
    for dirpath, filename in video_records:
        stem = Path(filename).stem
        parsed = parse_episode(stem)
        if parsed is None:
            continue
        raw_prefix, season, num = parsed
        key = (dirpath, _norm_prefix(raw_prefix), season)
        groups[key].add(num)
        prefixes.setdefault(key, _display_prefix(raw_prefix))

    results: list[MissingEpisodes] = []
    for key, nums in groups.items():
        if len(nums) < 2:  # 集号太少不做缺失判断，避免误报
            continue
        dirpath, _, season = key
        # 只报告组内区间 [min, max] 的内部缺口，避免"只下载了后半部"误报
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


def scan(root: Path, workers: int, chunk_size: int, all_files: bool) -> ScanResult:
    """遍历根目录并并发检查文件。

    :param root: 扫描根目录
    :param workers: 并发线程数
    :param chunk_size: 每个文件头尾各读取的字节数
    :param all_files: 是否检查所有文件（否则只检查视频扩展名文件）
    :return: 扫描结果
    """
    result = ScanResult(root=root, chunk_kb=chunk_size // 1024, all_files=all_files)
    video_records: list[tuple[str, str]] = []
    stats: dict[str, float] = {"dirs": 0, "checked": 0}
    futures: dict[Future[list[Issue]], Path] = {}
    started = time.monotonic()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: result.dir_errors.append(_walk_error(e))):
            stats["dirs"] += 1
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

        for done, future in enumerate(as_completed(futures), 1):
            result.issues.extend(future.result())
            if done % 500 == 0:
                print(f"  已检查 {done} 个文件...", file=sys.stderr)

    stats["checked"] = len(futures)
    stats["elapsed"] = round(time.monotonic() - started, 2)
    result.stats = stats
    result.issues.sort(key=lambda i: (i.level != "错误", i.path))
    result.missing = detect_missing_episodes(video_records)
    return result


def build_report(result: ScanResult) -> str:
    """生成文本检查报告。"""
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
        f"  遍历目录数：{result.stats.get('dirs', 0)}",
        f"  检查文件数：{result.stats.get('checked', 0)}",
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="NAS 文件完整性检查工具：快速筛查丢失/损坏的视频剧集文件与缺失集数。",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="要检查的根目录（默认：当前路径）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(16, max(4, (os.cpu_count() or 4) * 2)),
        help="并发检查线程数（默认：CPU 核数的 2 倍，上限 16）",
    )
    parser.add_argument(
        "--chunk-kb",
        type=int,
        default=64,
        help="每个文件头尾各读取的大小，单位 KB（默认：64）",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="检查所有文件而不仅是视频文件",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    args = parse_args(argv)
    root = Path(args.path).expanduser().resolve() if args.path else Path.cwd()
    if not root.is_dir():
        print(f"错误：路径不存在或不是目录：{root}", file=sys.stderr)
        return 2
    chunk_kb = max(4, args.chunk_kb)

    print(f"开始扫描：{root}")
    result = scan(root, max(1, args.workers), chunk_kb * 1024, args.all)
    report = build_report(result)

    report_path = root / f"文件检查报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with report_path.open("w", encoding="utf-8-sig", errors="replace") as fh:
        fh.write(report + "\n")

    errors = [i for i in result.issues if i.level == "错误"]
    warns = [i for i in result.issues if i.level == "警告"]
    print(f"扫描完成：检查 {result.stats.get('checked', 0)} 个文件，耗时 {result.stats.get('elapsed', 0)} 秒")
    print(f"  错误（疑似丢失/损坏）：{len(errors)} 个")
    print(f"  警告（可疑）：{len(warns)} 个")
    print(f"  目录访问错误：{len(result.dir_errors)} 个")
    print(f"  疑似缺失集数的剧集：{len(result.missing)} 组")
    print(f"报告已输出：{report_path}")
    return 1 if (errors or warns or result.dir_errors or result.missing) else 0


if __name__ == "__main__":
    sys.exit(main())
