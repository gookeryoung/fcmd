"""filescan 扫描引擎测试：遍历过滤、编码容错、表达式求值、进度与取消。"""

from __future__ import annotations

import codecs
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from fcmd.apps.filescan._engine import (
    DEFAULT_MAX_FILE_BYTES,
    REASON_BINARY,
    REASON_DOC_CORRUPT,
    REASON_DOC_ENCRYPTED,
    REASON_DOC_NO_TEXT,
    REASON_DOC_UNSUPPORTED,
    REASON_SYMLINK,
    REASON_TOO_LARGE,
    CancelToken,
    ScanContext,
    ScanOptions,
    ScanResult,
    decode_text,
    format_size,
    normalize_exts,
    parse_ext_text,
    scan_file,
    scan_paths,
    walk_files,
)
from fcmd.apps.filescan._rules import MATCH_KEYWORD, MATCH_REGEX, Matcher, Rule, RuleSet, compile_rule, parse_expression


def _write(root: Path, relative: str, data: str | bytes, encoding: str = "utf-8") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode(encoding) if isinstance(data, str) else data)
    return path


def _write_zip(path: Path, parts: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return path


def _write_docx(path: Path, paragraphs: list[str]) -> Path:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    document = (
        '<?xml version="1.0"?><w:document xmlns:w='
        '"http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    return _write_zip(path, {"word/document.xml": document})


def _ruleset(*specs: tuple[str, str], expression: str = "") -> RuleSet:
    rules = tuple(
        Rule(
            id=rule_id,
            name=f"规则{rule_id}",
            matcher=Matcher(kind=MATCH_KEYWORD, text=text, case_sensitive=True),
        )
        for rule_id, text in specs
    )
    return RuleSet(rules=rules, expression=expression)


def _context(
    ruleset: RuleSet,
    expression: str = "",
    positive: set[str] | None = None,
    options: ScanOptions | None = None,
) -> ScanContext:
    """构造单文件扫描上下文，便于直接断言 :func:`scan_file` 行为。"""
    compiled = tuple(compile_rule(rule) for rule in ruleset.enabled_rules())
    return ScanContext(
        compiled_rules=compiled,
        expression=parse_expression(expression or " or ".join(item.id for item in compiled) or "missing"),
        positive_ids=frozenset(positive if positive is not None else {item.id for item in compiled}),
        options=options or ScanOptions(),
    )


# ---------------------------------------------------------------------- #
# 基础辅助
# ---------------------------------------------------------------------- #
def test_normalize_and_parse_ext() -> None:
    assert normalize_exts(["PY", ".md", " txt ", "py"]) == (".py", ".md", ".txt")
    assert parse_ext_text(".py;*.md, txt") == (".py", ".md", ".txt")
    assert parse_ext_text(" , ; ") == ()
    assert normalize_exts(["*"]) == ("*",)


def test_format_size() -> None:
    assert format_size(0) == "0 B"
    assert format_size(1023) == "1023 B"
    assert format_size(1024) == "1.0 KiB"
    assert format_size(1024 * 1024) == "1.0 MiB"


def test_decode_text_prefers_bom_and_falls_back() -> None:
    assert decode_text(codecs.BOM_UTF8 + "中文".encode())[0] == "utf-8-sig"
    assert decode_text("中文".encode("utf-16"))[0] == "utf-16"
    assert decode_text("中文".encode("gb18030")) == ("gb18030", "中文")
    assert decode_text(b"caf\xe9 ok")[0] == "latin-1"
    assert decode_text(b"caf\xe9 ok", ("utf-8",)) == ("utf-8", "caf\ufffd ok")


# ---------------------------------------------------------------------- #
# 遍历
# ---------------------------------------------------------------------- #
def test_walk_recursive_by_default(tmp_path: Path) -> None:
    _write(tmp_path, "a.txt", "a")
    _write(tmp_path, "sub/b.md", "b")
    result = walk_files([tmp_path])
    assert [path.name for path in result.files] == ["a.txt", "b.md"]
    assert result.seen == 2


def test_walk_non_recursive_and_depth_limit(tmp_path: Path) -> None:
    _write(tmp_path, "a.txt", "a")
    _write(tmp_path, "d1/b.txt", "b")
    _write(tmp_path, "d1/d2/c.txt", "c")
    flat = walk_files([tmp_path], ScanOptions(recursive=False))
    assert [path.name for path in flat.files] == ["a.txt"]
    one = walk_files([tmp_path], ScanOptions(max_depth=1))
    assert [path.name for path in one.files] == ["a.txt", "b.txt"]
    two = walk_files([tmp_path], ScanOptions(max_depth=2))
    assert [path.name for path in two.files] == ["a.txt", "b.txt", "c.txt"]


def test_walk_extension_and_size_filters(tmp_path: Path) -> None:
    _write(tmp_path, "keep.py", "12345")
    _write(tmp_path, "skip.md", "12345")
    _write(tmp_path, "tiny.py", "1")
    _write(tmp_path, "big.py", "x" * 100)
    options = ScanOptions(
        include_exts=normalize_exts(["py"]),
        exclude_exts=normalize_exts(["big.py"]),
        min_size=5,
        max_size=10,
    )
    result = walk_files([tmp_path], options)
    assert [path.name for path in result.files] == ["keep.py"]


def test_walk_extension_whitelist_skips_unknown(tmp_path: Path) -> None:
    _write(tmp_path, "a.txt", "alpha")
    _write(tmp_path, "b.dat", "alpha")
    _write(tmp_path, "prod.env", "TOKEN=1")
    result = walk_files([tmp_path])
    assert [path.name for path in result.files] == ["a.txt", "prod.env"]
    assert result.skipped_extension == 1


def test_walk_include_narrows_whitelist(tmp_path: Path) -> None:
    _write(tmp_path, "a.txt", "alpha")
    _write(tmp_path, "b.md", "alpha")
    narrowed = walk_files([tmp_path], ScanOptions(include_exts=normalize_exts(["md"])))
    assert [path.name for path in narrowed.files] == ["b.md"]
    assert narrowed.skipped_extension == 1
    outside = walk_files([tmp_path], ScanOptions(include_exts=normalize_exts(["py"])))
    assert outside.files == []
    assert outside.skipped_extension == 2


def test_walk_force_ext_bypasses_whitelist(tmp_path: Path) -> None:
    _write(tmp_path, "dump.dat", "alpha")
    forced = walk_files([tmp_path], ScanOptions(force_exts=normalize_exts(["dat"])))
    assert [path.name for path in forced.files] == ["dump.dat"]
    excluded = walk_files(
        [tmp_path],
        ScanOptions(force_exts=normalize_exts(["dat"]), exclude_exts=normalize_exts(["dat"])),
    )
    assert excluded.files == []
    assert excluded.skipped_extension == 1


def test_walk_whitelist_star_allows_all(tmp_path: Path) -> None:
    _write(tmp_path, "a.dat", "alpha")
    _write(tmp_path, "b.bin", "alpha")
    result = walk_files([tmp_path], ScanOptions(ext_whitelist=("*",)))
    assert [path.name for path in result.files] == ["a.dat", "b.bin"]


def test_walk_reports_bad_roots(tmp_path: Path) -> None:
    file_root = _write(tmp_path, "file.txt", "x")
    result = walk_files([tmp_path / "missing", file_root])
    assert [error.message for error in result.errors] == ["目录不存在", "不是目录"]


def test_walk_skips_symlinks_by_default(tmp_path: Path) -> None:
    target = _write(tmp_path, "target.txt", "secret")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不支持创建符号链接")
    result = walk_files([tmp_path], ScanOptions(follow_symlinks=False))
    assert [path.name for path in result.files] == ["target.txt"]
    assert any(item.reason == REASON_SYMLINK and item.path.name == "link.txt" for item in result.skipped)


def test_walk_follows_symlinks_without_cycles(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    _write(sub, "inside.txt", "x")
    try:
        (sub / "loop").symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不支持创建符号链接")
    result = walk_files([tmp_path], ScanOptions(follow_symlinks=True))
    assert [path.name for path in result.files] == ["inside.txt"]


# ---------------------------------------------------------------------- #
# 单文件匹配
# ---------------------------------------------------------------------- #
def test_scan_file_reports_lines_and_context(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.txt", "first line\nxx alpha yy\nlast\n")
    outcome = scan_file(path, _context(_ruleset(("alpha", "alpha")), options=ScanOptions(context_chars=2)))
    assert outcome.hit is not None
    match = outcome.hit.matches[0]
    assert match.line_no == 2
    assert match.excerpt == "alpha"
    assert match.context == "x alpha y"
    assert match.context_start == 2
    assert outcome.hit.encoding == "utf-8"
    assert outcome.bytes_read == path.stat().st_size


def test_scan_file_respects_hit_limit(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.txt", "alpha alpha alpha\n")
    outcome = scan_file(
        path,
        _context(_ruleset(("alpha", "alpha")), options=ScanOptions(max_hits_per_file=2)),
    )
    assert outcome.hit is not None
    assert len(outcome.hit.matches) == 2


def test_scan_file_skips_binary_and_oversize(tmp_path: Path) -> None:
    binary = _write(tmp_path, "a.bin", b"abc\x00def")
    big = _write(tmp_path, "b.txt", "x" * 50)
    ruleset = _ruleset(("alpha", "alpha"))
    binary_outcome = scan_file(binary, _context(ruleset))
    assert binary_outcome.skipped is not None and binary_outcome.skipped.reason == REASON_BINARY
    big_outcome = scan_file(big, _context(ruleset, options=ScanOptions(max_file_bytes=10)))
    assert big_outcome.skipped is not None and big_outcome.skipped.reason == REASON_TOO_LARGE


def test_scan_file_reports_read_error(tmp_path: Path) -> None:
    directory = tmp_path / "dir"
    directory.mkdir()
    outcome = scan_file(directory, _context(_ruleset(("a", "a"))))
    assert outcome.error is not None


def test_scan_file_returns_cancelled(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.txt", "alpha")
    token = CancelToken()
    token.cancel()
    context = ScanContext(
        compiled_rules=(),
        expression=parse_expression("a"),
        positive_ids=frozenset(),
        options=ScanOptions(),
        token=token,
    )
    outcome = scan_file(path, context)
    assert outcome.cancelled is True


# ---------------------------------------------------------------------- #
# 批量扫描
# ---------------------------------------------------------------------- #
def test_scan_paths_aggregates_and_sorts(tmp_path: Path) -> None:
    _write(tmp_path, "b.txt", "alpha beta\n")
    _write(tmp_path, "a.txt", "alpha\n")
    _write(tmp_path, "c.txt", "nothing\n")
    result = scan_paths([tmp_path], _ruleset(("alpha", "alpha")))
    assert [hit.path.name for hit in result.hits] == ["a.txt", "b.txt"]
    assert result.stats.hit_files == 2
    assert result.stats.hit_count == 2
    assert result.stats.files_scanned == 3
    assert result.stats.bytes_read > 0
    assert result.stats.elapsed >= 0


def test_scan_paths_expression_is_file_level(tmp_path: Path) -> None:
    _write(tmp_path, "both.txt", "alpha\nbeta\n")
    _write(tmp_path, "alpha_only.txt", "alpha\n")
    ruleset = _ruleset(("alpha", "alpha"), ("beta", "beta"), expression="alpha and not beta")
    result = scan_paths([tmp_path], ruleset)
    assert [hit.path.name for hit in result.hits] == ["alpha_only.txt"]
    assert result.hits[0].matches[0].rule_id == "alpha"


def test_scan_paths_negation_only_hit_has_no_detail(tmp_path: Path) -> None:
    _write(tmp_path, "clean.txt", "nothing\n")
    _write(tmp_path, "dirty.txt", "alpha\n")
    ruleset = _ruleset(("alpha", "alpha"), expression="not alpha")
    result = scan_paths([tmp_path], ruleset)
    assert [hit.path.name for hit in result.hits] == ["clean.txt"]
    assert result.hits[0].matches == []
    assert result.stats.hit_count == 0


def test_scan_paths_handles_encodings(tmp_path: Path) -> None:
    _write(tmp_path, "gb.txt", "中文 alpha", encoding="gb18030")
    _write(tmp_path, "utf16.txt", "alpha", encoding="utf-16")
    result = scan_paths([tmp_path], _ruleset(("alpha", "alpha")))
    encodings = {hit.path.name: hit.encoding for hit in result.hits}
    assert encodings == {"gb.txt": "gb18030", "utf16.txt": "utf-16"}


def test_scan_paths_progress_and_workers(tmp_path: Path) -> None:
    for index in range(6):
        _write(tmp_path, f"f{index}.txt", "alpha\n")
    calls: list[tuple[int, int, Path]] = []
    result = scan_paths(
        [tmp_path],
        _ruleset(("alpha", "alpha")),
        ScanOptions(workers=3),
        progress=lambda done, total, path: calls.append((done, total, path)),
    )
    assert [done for done, _total, _path in calls] == [1, 2, 3, 4, 5, 6]
    assert all(total == 6 for _done, total, _path in calls)
    assert len(result.hits) == 6


def test_scan_paths_cancel_returns_partial(tmp_path: Path) -> None:
    for index in range(20):
        _write(tmp_path, f"f{index}.txt", "alpha\n")
    token = CancelToken()
    token.cancel()
    result = scan_paths([tmp_path], _ruleset(("alpha", "alpha")), token=token)
    assert result.stats.cancelled is True
    assert result.hits == []


def test_scan_paths_cancel_during_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fcmd.apps.filescan import _engine

    for index in range(20):
        _write(tmp_path, f"f{index}.txt", "alpha\n")
    token = CancelToken()
    original = _engine.scan_file
    state = {"count": 0}

    def wrapped(*args: object, **kwargs: object) -> object:
        state["count"] += 1
        if state["count"] == 1:
            token.cancel()
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_engine, "scan_file", wrapped)
    result = scan_paths([tmp_path], _ruleset(("alpha", "alpha")), ScanOptions(workers=1), token=token)
    assert result.stats.cancelled is True


def test_scan_paths_process_mode_matches_threads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """进程池路径的结果必须与线程路径逐条一致（只是调度方式不同）。"""
    from fcmd.apps.filescan import _engine

    for index in range(12):
        _write(tmp_path, f"f{index}.txt", "alpha 13812345678\nplain line\n")
    ruleset = _ruleset(("alpha", "alpha"), ("phone", "13812345678"))
    options = ScanOptions(workers=2)

    threaded = scan_paths([tmp_path], ruleset, replace(options, use_processes=False))
    monkeypatch.setattr(_engine, "PROCESS_MIN_FILES", 1)
    monkeypatch.setattr(_engine, "PROCESS_MIN_BYTES", 1)
    processed = scan_paths([tmp_path], ruleset, options)

    def snapshot(result: ScanResult) -> tuple[object, ...]:
        return (
            [
                (str(hit.path), [(item.rule_id, item.line_no, item.start, item.end) for item in hit.matches])
                for hit in result.hits
            ],
            result.stats.files_scanned,
            result.stats.bytes_read,
            result.stats.hit_files,
            result.stats.hit_count,
        )

    assert snapshot(processed) == snapshot(threaded)
    assert processed.stats.hit_count == 24


def test_scan_paths_scans_docx_with_location(tmp_path: Path) -> None:
    """docx 默认纳入白名单，命中带段落位置且编码记为容器格式。"""
    path = _write_docx(tmp_path / "a.docx", ["姓名 张三", "手机 13812345678"])
    result = scan_paths([tmp_path], _ruleset(("phone", "13812345678")), ScanOptions(use_processes=False))

    assert result.stats.files_scanned == 1
    assert result.stats.bytes_read == path.stat().st_size
    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit.path == path
    assert hit.encoding == "docx"
    assert [item.location for item in hit.matches] == ["段落 2"]
    assert hit.matches[0].line_no == 1


def test_scan_paths_document_skip_categories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """加密 / 损坏 / 无文本层 / 缺依赖分别计数，不合并成笼统 skipped。"""
    from fcmd.apps.filescan import _extract
    from fcmd.apps.filescan._extract import KIND_UNSUPPORTED, ExtractError

    (tmp_path / "enc.docx").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32)
    (tmp_path / "bad.xlsx").write_bytes(b"not a zip at all")
    _write_zip(
        tmp_path / "blank.pptx",
        {
            "ppt/slides/slide1.xml": (
                '<?xml version="1.0"?><p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
                ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"/>'
            )
        },
    )
    _write_zip(tmp_path / "unsupported.ods", {"content.xml": '<office:document-content xmlns:office="x"/>'})

    def missing_dependency(reader: object) -> list[object]:
        raise ExtractError(KIND_UNSUPPORTED, "缺少依赖")

    monkeypatch.setitem(_extract._EXTRACTORS, ".ods", missing_dependency)

    result = scan_paths([tmp_path], _ruleset(("phone", "13812345678")), ScanOptions(use_processes=False))
    stats = result.stats
    assert stats.files_scanned == 0
    assert stats.files_skipped_encrypted == 1
    assert stats.files_skipped_corrupt == 1
    assert stats.files_skipped_no_text == 1
    assert stats.files_skipped_unsupported == 1
    assert stats.files_skipped_other == 0
    assert stats.files_skipped == 4
    reasons = {item.path.name: item.reason for item in result.skipped}
    assert reasons == {
        "enc.docx": REASON_DOC_ENCRYPTED,
        "bad.xlsx": REASON_DOC_CORRUPT,
        "blank.pptx": REASON_DOC_NO_TEXT,
        "unsupported.ods": REASON_DOC_UNSUPPORTED,
    }


def test_scan_paths_documents_disabled_skip_by_extension(tmp_path: Path) -> None:
    """关闭多文档后 .docx 退回「后缀跳过」，与支持前口径一致。"""
    _write_docx(tmp_path / "a.docx", ["手机 13812345678"])
    options = ScanOptions(use_processes=False, scan_documents=False)
    result = scan_paths([tmp_path], _ruleset(("phone", "13812345678")), options)

    assert not result.hits
    assert result.stats.files_skipped_extension == 1
    assert result.stats.files_scanned == 0


def test_scan_paths_documents_in_process_pool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """文档抽取在子进程内完成：任务参数只用 (str, int)，可 pickle。"""
    from fcmd.apps.filescan import _engine

    _write_docx(tmp_path / "a.docx", ["手机 13812345678"])
    monkeypatch.setattr(_engine, "PROCESS_MIN_FILES", 1)
    monkeypatch.setattr(_engine, "PROCESS_MIN_BYTES", 1)

    result = scan_paths([tmp_path], _ruleset(("phone", "13812345678")), ScanOptions(workers=2))

    assert len(result.hits) == 1
    assert result.hits[0].matches[0].location == "段落 1"


def test_scan_paths_empty_and_filtered(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", "alpha\n")
    result = scan_paths([tmp_path], _ruleset(("alpha", "alpha")), ScanOptions(include_exts=(".py",)))
    assert result.hits == []
    assert result.stats.files_seen == 1
    assert result.stats.elapsed >= 0

    missing = scan_paths([tmp_path / "missing"], _ruleset(("alpha", "alpha")))
    assert [error.message for error in missing.errors] == ["目录不存在"]


def test_scan_paths_skip_stats(tmp_path: Path) -> None:
    _write(tmp_path, "bin.txt", b"\x00\x01")
    _write(tmp_path, "big.txt", "x" * 100)
    _write(tmp_path, "skip.dat", "alpha")
    result = scan_paths(
        [tmp_path],
        _ruleset(("alpha", "alpha")),
        ScanOptions(max_file_bytes=10),
    )
    assert {item.reason for item in result.skipped} == {REASON_BINARY, REASON_TOO_LARGE}
    assert result.stats.files_skipped_extension == 1
    assert result.stats.files_skipped_binary == 1
    assert result.stats.files_skipped_other == 1
    assert result.stats.files_skipped == 3


def test_scan_paths_reads_no_more_than_limit(tmp_path: Path) -> None:
    """上限之内的文件正常扫描，确认默认上限常量可被覆盖。"""
    assert DEFAULT_MAX_FILE_BYTES == 8 * 1024 * 1024
    _write(tmp_path, "a.txt", "alpha")
    result = scan_paths([tmp_path], _ruleset(("alpha", "alpha")), ScanOptions(max_file_bytes=1024))
    assert len(result.hits) == 1


def test_regex_rule_matches_ignore_case(tmp_path: Path) -> None:
    _write(tmp_path, "a.txt", "TOKEN=abc\n")
    rule = Rule(
        id="token",
        name="令牌",
        matcher=Matcher(kind=MATCH_REGEX, pattern=r"token=\w+", case_sensitive=False),
    )
    result = scan_paths([tmp_path], RuleSet(rules=(rule,)))
    assert result.hits[0].matches[0].excerpt == "TOKEN=abc"
