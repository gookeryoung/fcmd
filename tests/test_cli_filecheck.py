"""filecheck 工具测试。

验证 ``fcmd.cli.media.filecheck`` 模块：
- 工具注册与 run_tool 调用
- check_one_file 文件完整性检查（0 字节/过小/头部不符/尾部全 0/断链/读取失败）
- parse_episode 集号识别
- detect_missing_episodes 缺失集数检测
- scan_directory 目录扫描（跳过目录/全部文件模式/目录错误）
- build_report 报告生成
- run_filecheck 端到端输出
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

import fcmd as fx
import fcmd.cli.media.filecheck
from fcmd.apis.toolkit import run_tool
from fcmd.cli._discovery import tool_description
from fcmd.cli.media.filecheck import (
    Issue,
    MissingEpisodes,
    ScanResult,
    _magic_ok,
    build_report,
    check_one_file,
    detect_missing_episodes,
    parse_episode,
    run_filecheck,
    scan_directory,
)

# MP4 文件头（ftyp box）
_MP4_HEAD = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2"
_MKV_HEAD = b"\x1a\x45\xdf\xa3"
_CHUNK = 64 * 1024


def _make_video(path: Path, head: bytes, size: int = 200 * 1024, fill: bytes = b"\x01") -> Path:
    """创建一个指定头部与总大小的测试视频文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(head + fill * (size - len(head)))
    return path


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestToolsRegistration:
    """filecheck 工具的注册验证。"""

    def test_filecheck_single_command(self) -> None:
        """filecheck 应为单命令工具（无子命令）。"""
        assert fx.list_subcommands("filecheck") == []

    def test_filecheck_description(self) -> None:
        """filecheck 应注册了工具描述。"""
        assert tool_description("filecheck")


# ---------------------------------------------------------------------- #
# check_one_file 文件检查
# ---------------------------------------------------------------------- #
class TestMagicOk:
    """``_magic_ok`` 特征字节匹配测试。"""

    def test_short_head_returns_false(self) -> None:
        """头部不足以覆盖特征位置时返回 False。"""
        assert _magic_ok(b"\x1a", 0, b"\x1a\x45\xdf\xa3") is False

    def test_wildcard_byte(self) -> None:
        """``?`` 通配单字节。"""
        assert _magic_ok(b"RIFF\x99\x98\x97\x96AVI ", 0, b"RIFF????AVI ") is True


class TestCheckOneFile:
    """``check_one_file`` 单文件检查测试。"""

    def test_normal_video_no_issue(self, tmp_path: Path) -> None:
        """正常 MP4 文件无问题。"""
        video = _make_video(tmp_path / "good.mp4", _MP4_HEAD)
        assert check_one_file(video, _CHUNK) == []

    def test_zero_byte_file(self, tmp_path: Path) -> None:
        """0 字节文件报错误级问题。"""
        video = tmp_path / "empty.mkv"
        video.write_bytes(b"")
        issues = check_one_file(video, _CHUNK)
        assert len(issues) == 1
        assert issues[0].level == "错误"
        assert "0 字节" in issues[0].message

    def test_small_file_warning(self, tmp_path: Path) -> None:
        """小于 4096 字节的文件报过小警告。"""
        video = tmp_path / "tiny.mp4"
        video.write_bytes(_MP4_HEAD + b"\x01" * 100)
        issues = check_one_file(video, _CHUNK)
        assert any(i.level == "警告" and "过小" in i.message for i in issues)

    def test_cross_container_passes(self, tmp_path: Path) -> None:
        """扩展名与实际容器不符但内容可识别为已知容器时不报告。"""
        # mkv 扩展名但写 MP4 头（混后缀目录常见情况）
        video = _make_video(tmp_path / "fake.mkv", _MP4_HEAD)
        assert check_one_file(video, _CHUNK) == []
        # mp4 扩展名但为 RM/RMVB 内容，同样不报告
        rm_video = _make_video(tmp_path / "real.mp4", b".RMF")
        assert check_one_file(rm_video, _CHUNK) == []

    def test_isobmff_nonwhitelist_box_no_warning(self, tmp_path: Path) -> None:
        """ISOBMFF 非白名单 box 类型（如 msdh/moof）开头但结构合法时不报警。"""
        head = b"\x00\x00\x00\x20moof" + b"\x00" * 28
        video = _make_video(tmp_path / "dash.mp4", head)
        assert check_one_file(video, _CHUNK) == []

    def test_isobmff_largesize_box_no_warning(self, tmp_path: Path) -> None:
        """ISOBMFF largesize（前 4 字节为 1，64 位长度）合法时不报警。"""
        head = b"\x00\x00\x00\x01moov" + (200 * 1024).to_bytes(8, "big")
        video = _make_video(tmp_path / "large.mov", head)
        assert check_one_file(video, _CHUNK) == []

    def test_isobmff_size_to_eof_no_warning(self, tmp_path: Path) -> None:
        """ISOBMFF box 长度为 0（到文件尾）合法时不报警。"""
        head = b"\x00\x00\x00\x00mdat" + b"\x01" * 100
        video = _make_video(tmp_path / "raw.mp4", head)
        assert check_one_file(video, _CHUNK) == []

    def test_isobmff_oversize_box_warns(self, tmp_path: Path) -> None:
        """ISOBMFF box 长度超过实际文件大小时报警告（疑似占位/截断）。"""
        head = b"\x00\x00\x10\x00moof" + b"<html>404 Not Found</html>"
        video = tmp_path / "placeholder.mp4"
        video.write_bytes(head)
        issues = check_one_file(video, _CHUNK)
        assert any(i.level == "警告" and "头部标识不符" in i.message for i in issues)

    def test_isobmff_nonprintable_type_warns(self, tmp_path: Path) -> None:
        """ISOBMFF box 类型含不可打印字符时报警告。"""
        head = b"\x00\x00\x00\x20\x00\x01\x02\x03" + b"\x00" * 24
        video = _make_video(tmp_path / "binary.mp4", head)
        issues = check_one_file(video, _CHUNK)
        assert any(i.level == "警告" and "头部标识不符" in i.message for i in issues)

    def test_header_match_no_warning(self, tmp_path: Path) -> None:
        """AVI 通配符特征（RIFF????AVI ）匹配时不报警。"""
        avi_head = b"RIFF\x00\x00\x00\x00AVI LIST"
        video = _make_video(tmp_path / "ok.avi", avi_head)
        assert check_one_file(video, _CHUNK) == []

    def test_ts_sync_byte(self, tmp_path: Path) -> None:
        """TS 文件同步字节不符时报警告。"""
        video = _make_video(tmp_path / "bad.ts", b"NOTATS", fill=b"\x00")
        issues = check_one_file(video, _CHUNK)
        assert any("MPEG-TS" in i.message for i in issues)

    def test_iso_volume_id(self, tmp_path: Path) -> None:
        """ISO 卷标识位于偏移 0x8001，正确匹配与不符两种情况。"""
        ok = bytearray(b"\x01" * (200 * 1024))
        ok[0x8001:0x8006] = b"CD001"
        ok_path = tmp_path / "ok.iso"
        ok_path.write_bytes(bytes(ok))
        assert check_one_file(ok_path, _CHUNK) == []

        bad = bytearray(b"\x01" * (200 * 1024))
        bad_path = tmp_path / "bad.iso"
        bad_path.write_bytes(bytes(bad))
        issues = check_one_file(bad_path, _CHUNK)
        assert any("ISO" in i.message for i in issues)

    def test_tail_all_zero_hint(self, tmp_path: Path) -> None:
        """尾部全 0 且文件足够大时报提示。"""
        video = _make_video(tmp_path / "zero_tail.mp4", _MP4_HEAD, fill=b"\x00")
        issues = check_one_file(video, _CHUNK)
        assert any(i.level == "提示" and "尾部全部为 0" in i.message for i in issues)

    def test_tail_zero_small_file_no_hint(self, tmp_path: Path) -> None:
        """文件不足 2 倍 chunk 时不报尾部全 0 提示。"""
        video = tmp_path / "small.mp4"
        video.write_bytes(_MP4_HEAD + b"\x01" * (_CHUNK + 1024))
        assert check_one_file(video, _CHUNK) == []

    def test_broken_symlink(self, tmp_path: Path) -> None:
        """断链报错误级问题。"""
        if sys.platform == "win32":
            pytest.skip("Windows 创建符号链接需要特权")
        link = tmp_path / "broken.mkv"
        link.symlink_to(tmp_path / "不存在.mkv")
        issues = check_one_file(link, _CHUNK)
        assert len(issues) == 1
        assert "断链" in issues[0].message

    def test_stat_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """stat 失败时报无法获取文件信息。"""
        video = tmp_path / "x.mp4"
        video.write_bytes(_MP4_HEAD + b"\x01" * 8192)

        def bad_stat(self: Path) -> os.stat_result:
            raise OSError(5, "Input/output error")

        monkeypatch.setattr(Path, "stat", bad_stat)
        issues = check_one_file(video, _CHUNK)
        assert len(issues) == 1
        assert "无法获取文件信息" in issues[0].message

    def test_open_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """open/读取失败时报疑似坏块/文件丢失。"""
        video = tmp_path / "x.mp4"
        video.write_bytes(_MP4_HEAD + b"\x01" * 8192)

        def bad_open(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
            raise OSError(5, "Input/output error")

        monkeypatch.setattr(Path, "open", bad_open)
        issues = check_one_file(video, _CHUNK)
        assert len(issues) == 1
        assert "疑似坏块/文件丢失" in issues[0].message

    def test_unknown_ext_no_header_check(self, tmp_path: Path) -> None:
        """无特征定义的扩展名不做头部校验。"""
        video = _make_video(tmp_path / "data.dat", b"RANDOMHEAD")
        issues = check_one_file(video, _CHUNK)
        assert all("头部标识不符" not in i.message for i in issues)

    def test_short_head_skips_header_check(self, tmp_path: Path) -> None:
        """头部不足以覆盖特征位置时跳过头部校验（不误报）。"""
        video = tmp_path / "tiny.mkv"
        video.write_bytes(b"\x1a")  # 1 字节，不够 MKV 4 字节特征
        issues = check_one_file(video, _CHUNK)
        assert all("头部标识不符" not in i.message for i in issues)
        assert any("过小" in i.message for i in issues)


# ---------------------------------------------------------------------- #
# parse_episode 集号识别
# ---------------------------------------------------------------------- #
class TestParseEpisode:
    """``parse_episode`` 集号识别测试。"""

    def test_season_ep(self) -> None:
        """S01E05 形式识别季号与集号。"""
        assert parse_episode("权游.S01E05.1080p") == ("权游.", 1, 5)

    def test_season_ep_with_ep_suffix(self) -> None:
        """S01EP05 形式（EP 后缀）同样识别。"""
        assert parse_episode("show.S01EP05") == ("show.", 1, 5)

    def test_chinese_episode(self) -> None:
        """第01集形式识别。"""
        assert parse_episode("舌尖上的中国 第03集") == ("舌尖上的中国 ", None, 3)

    def test_ep_form(self) -> None:
        """EP03 / E03 形式识别。"""
        assert parse_episode("地球脉动.EP03") == ("地球脉动", None, 3)
        assert parse_episode("show E05") == ("show", None, 5)

    def test_bare_number(self) -> None:
        """纯数字文件名识别为集号。"""
        assert parse_episode("07") == ("", None, 7)

    def test_year_excluded(self) -> None:
        """年份与超大编号不误报为集号。"""
        assert parse_episode("Blade.Runner.2049") is None
        assert parse_episode("纪录片.2019") is None

    def test_version_number_excluded(self) -> None:
        """软件版本号（操作系统镜像等）不误报为集号。"""
        assert parse_episode("ubuntu-24.04.3-desktop-amd64") is None
        assert parse_episode("ubuntu-22.04") is None
        assert parse_episode("ubuntu-24.04-desktop") is None
        assert parse_episode("centos-7.9") is None
        assert parse_episode("某软件 v3.1") is None
        assert parse_episode("某软件 v3.1.5") is None

    def test_episode_with_resolution_still_detected(self) -> None:
        """清晰度后缀（1080p/720p）不阻断集号识别。"""
        assert parse_episode("show.05.1080p") == ("show", None, 5)
        assert parse_episode("show.12.720p") == ("show", None, 12)

    def test_no_episode(self) -> None:
        """无集号特征返回 None。"""
        assert parse_episode("预告片.trailer".replace(".trailer", "")) is None


# ---------------------------------------------------------------------- #
# detect_missing_episodes 缺失集数检测
# ---------------------------------------------------------------------- #
class TestDetectMissingEpisodes:
    """``detect_missing_episodes`` 缺失集数检测测试。"""

    def test_internal_gap(self) -> None:
        """组内缺口被报告。"""
        records = [
            ("/m", "show.S01E01.mkv"),
            ("/m", "show.S01E03.mkv"),
            ("/m", "show.S01E04.mkv"),
        ]
        missing = detect_missing_episodes(records)
        assert len(missing) == 1
        assert missing[0].missing == (2,)
        assert missing[0].season == 1
        assert missing[0].lo == 1
        assert missing[0].hi == 4

    def test_single_episode_no_report(self) -> None:
        """组内集号少于 2 个不报告。"""
        assert detect_missing_episodes([("/m", "show.S01E01.mkv")]) == []

    def test_no_internal_gap(self) -> None:
        """连续集号无缺口不报告。"""
        records = [("/m", f"show.E0{n}.mkv") for n in (1, 2, 3)]
        assert detect_missing_episodes(records) == []

    def test_partial_collection_no_false_positive(self) -> None:
        """只有后半部集号（无内部缺口）不误报。"""
        records = [("/m", f"show.E{n}.mkv") for n in (45, 46, 48)]
        missing = detect_missing_episodes(records)
        assert len(missing) == 1
        assert missing[0].missing == (47,)  # 只报内部缺口

    def test_different_seasons_separate_groups(self) -> None:
        """不同季号分组各自判断。"""
        records = [
            ("/m", "show.S01E01.mkv"),
            ("/m", "show.S01E03.mkv"),
            ("/m", "show.S02E01.mkv"),
            ("/m", "show.S02E02.mkv"),
        ]
        missing = detect_missing_episodes(records)
        assert len(missing) == 1
        assert missing[0].missing == (2,)
        assert missing[0].season == 1

    def test_unparsable_files_ignored(self) -> None:
        """无法识别集号的文件被忽略。"""
        records = [("/m", "制作花絮.mkv"), ("/m", "海报.jpg")]
        assert detect_missing_episodes(records) == []

    def test_bare_number_display_prefix(self) -> None:
        """纯数字文件名的剧集名展示为占位文本。"""
        records = [("/m", "01.mkv"), ("/m", "03.mkv")]
        missing = detect_missing_episodes(records)
        assert len(missing) == 1
        assert missing[0].prefix == "(文件名为纯数字)"

    def test_software_iso_not_grouped(self) -> None:
        """操作系统镜像等版本文件不组成剧集组。"""
        records = [
            ("/m", "ubuntu-20.04.1-desktop-amd64.iso"),
            ("/m", "ubuntu-22.04.3-desktop-amd64.iso"),
            ("/m", "ubuntu-24.04-desktop-amd64.iso"),
        ]
        assert detect_missing_episodes(records) == []


# ---------------------------------------------------------------------- #
# scan_directory 目录扫描
# ---------------------------------------------------------------------- #
class TestScanDirectory:
    """``scan_directory`` 目录扫描测试。"""

    def _build_tree(self, root: Path) -> None:
        """构建模拟 NAS 剧集库测试树。"""
        _make_video(root / "剧集/权游/Season 1/权游.S01E01.mp4", _MP4_HEAD)
        _make_video(root / "剧集/权游/Season 1/权游.S01E03.mp4", _MP4_HEAD)
        (root / "剧集/纪录片/地球脉动.EP01.mkv").parent.mkdir(parents=True, exist_ok=True)
        (root / "剧集/纪录片/地球脉动.EP01.mkv").write_bytes(b"")
        _make_video(root / "剧集/纪录片/地球脉动.EP03.mkv", _MKV_HEAD)
        _make_video(root / "剧集/纪录片/新闻联播.ts", b"NOTATS", fill=b"\x00")
        # 系统/垃圾文件与目录
        (root / "@eaDir").mkdir(exist_ok=True)
        _make_video(root / "@eaDir/thumb.mp4", _MP4_HEAD)
        (root / ".DS_Store").write_bytes(b"junk")
        (root / "._thumb.mp4").write_bytes(b"junk")

    def test_scan_video_files(self, tmp_path: Path) -> None:
        """默认只扫描视频文件，识别问题与缺失集数。"""
        root = tmp_path / "nas"
        self._build_tree(root)
        result = scan_directory(root, workers=4, chunk_size=_CHUNK, all_files=False)

        levels = {(i.level, "0 字节" in i.message) for i in result.issues}
        assert ("错误", True) in levels
        assert any("MPEG-TS" in i.message for i in result.issues)
        # 0 字节 + EP03 缺失：EP01 为 0 字节仍参与集号识别
        assert any(m.prefix == "地球脉动" and m.missing == (2,) for m in result.missing)
        assert any(m.prefix == "权游" and m.missing == (2,) for m in result.missing)
        assert result.stats["checked"] == 5.0

    def test_scan_skips_system_dirs_and_files(self, tmp_path: Path) -> None:
        """@eaDir 目录与 .DS_Store/._ 文件被跳过。"""
        root = tmp_path / "nas"
        self._build_tree(root)
        result = scan_directory(root, workers=2, chunk_size=_CHUNK, all_files=False)
        assert all("@eaDir" not in i.path for i in result.issues)
        assert all(".DS_Store" not in i.path for i in result.issues)

    def test_scan_all_files_mode(self, tmp_path: Path) -> None:
        """all_files 模式检查所有文件。"""
        root = tmp_path / "nas"
        (root / "docs").mkdir(parents=True)
        (root / "docs/readme.txt").write_bytes(b"hello")  # 非视频、小于 4096
        result = scan_directory(root, workers=2, chunk_size=_CHUNK, all_files=True)
        assert any("readme.txt" in i.path and "过小" in i.message for i in result.issues)

    def test_scan_dir_error_collected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """目录访问错误被收集到结果中。"""
        root = tmp_path / "nas"
        root.mkdir()
        original_walk = os.walk

        def walk_with_error(top: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
            onerror = kwargs.get("onerror")
            if str(top) == str(root) and onerror is not None:
                onerror(OSError(13, "Permission denied", str(root / "secret")))
            yield from original_walk(top, **kwargs)

        monkeypatch.setattr("fcmd.cli.media.filecheck.os.walk", walk_with_error)
        result = scan_directory(root, workers=2, chunk_size=_CHUNK, all_files=False)
        assert len(result.dir_errors) == 1
        assert "Permission denied" in result.dir_errors[0]

    def test_scan_empty_dir(self, tmp_path: Path) -> None:
        """空目录扫描结果干净。"""
        root = tmp_path / "empty"
        root.mkdir()
        result = scan_directory(root, workers=2, chunk_size=_CHUNK, all_files=False)
        assert result.issues == []
        assert result.missing == []


# ---------------------------------------------------------------------- #
# build_report 报告生成
# ---------------------------------------------------------------------- #
class TestBuildReport:
    """``build_report`` 报告生成测试。"""

    def test_report_contains_all_sections(self) -> None:
        """报告包含全部五个章节与问题详情。"""
        result = ScanResult(root=Path("/nas"), chunk_kb=64, all_files=False)
        result.issues = [
            Issue("错误", "/nas/a.mp4", "文件大小为 0 字节"),
            Issue("警告", "/nas/b.ts", "MPEG-TS 头部标识不符"),
            Issue("提示", "/nas/c.mp4", "文件尾部全部为 0"),
        ]
        result.dir_errors = ["/nas/locked：Permission denied"]
        result.missing = [
            MissingEpisodes(
                dirpath="/nas/剧集",
                prefix="权游",
                season=1,
                found=4,
                lo=1,
                hi=5,
                missing=(2, 3),
            )
        ]
        result.stats = {"dirs": 10.0, "checked": 20.0, "elapsed": 1.5}
        report = build_report(result)
        for section in (
            "一、检查概览",
            "二、疑似丢失或损坏的文件",
            "三、可疑文件",
            "四、剧集疑似缺失集数",
            "五、目录访问错误",
        ):
            assert section in report
        assert "/nas/a.mp4" in report
        assert "第 1 季" in report
        assert "2, 3" in report
        assert "20" in report

    def test_report_empty_result(self) -> None:
        """无问题时各章节显示（无）。"""
        result = ScanResult(root=Path("/nas"), chunk_kb=64, all_files=True)
        result.stats = {"dirs": 1.0, "checked": 0.0, "elapsed": 0.0}
        report = build_report(result)
        assert report.count("（无）") == 4
        assert "所有文件" in report


# ---------------------------------------------------------------------- #
# run_filecheck 与 run_tool 端到端
# ---------------------------------------------------------------------- #
class TestRunFilecheck:
    """``run_filecheck`` 与 ``run_tool`` 端到端测试。"""

    def test_run_filecheck_writes_report(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """run_filecheck 在扫描根目录生成报告文件。"""
        _make_video(tmp_path / "剧/a.mp4", _MP4_HEAD)
        run_filecheck(path=tmp_path, workers=2)
        reports = list(tmp_path.glob("文件检查报告_*.txt"))
        assert len(reports) == 1
        content = reports[0].read_text(encoding="utf-8-sig")
        assert "NAS 文件完整性检查报告" in content
        out = capsys.readouterr().out
        assert "报告已输出" in out

    def test_run_filecheck_invalid_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        """无效路径打印错误提示且不抛异常。"""
        run_filecheck(path=Path("/nonexistent/nas/path"))
        assert "路径不存在" in capsys.readouterr().out

    def test_run_filecheck_small_chunk_kb_clamped(self, tmp_path: Path) -> None:
        """chunk_kb 小于 4 时被钳制为 4。"""
        run_filecheck(path=tmp_path, workers=2, chunk_kb=1)
        reports = list(tmp_path.glob("文件检查报告_*.txt"))
        assert "头尾各 4KB" in reports[0].read_text(encoding="utf-8-sig")

    def test_run_tool_filecheck(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd filecheck --path <dir> 通过 run_tool 调用并生成报告。"""
        _make_video(tmp_path / "剧/b.mkv", _MKV_HEAD)
        code = run_tool("filecheck", ["--path", str(tmp_path), "--workers", "2"])
        assert code == 0
        assert list(tmp_path.glob("文件检查报告_*.txt"))

    def test_run_tool_filecheck_all_files(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd filecheck --all-files 检查所有文件。"""
        (tmp_path / "note.txt").write_bytes(b"data")
        code = run_tool("filecheck", ["--path", str(tmp_path), "--all-files", "--workers", "2"])
        assert code == 0
        out = capsys.readouterr().out
        assert "1 个文件" in out
