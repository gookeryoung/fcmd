"""文件类型魔数与归档成员识别。"""

from __future__ import annotations

import zipfile
from pathlib import Path

from fcmd.apps.filescan._engine import ScanOptions, scan_paths
from fcmd.apps.filescan._filetype import archive_members, detect_file_type
from fcmd.apps.filescan._rules import MATCH_FILETYPE, Matcher, Rule, RuleSet


def test_detect_file_type_by_magic(tmp_path: Path) -> None:
    pdf = tmp_path / "rename.txt"
    pdf.write_bytes(b"%PDF-1.7\n")
    assert detect_file_type(pdf) == "pdf"
    text = tmp_path / "notes.txt"
    text.write_text("hello", encoding="utf-8")
    assert detect_file_type(text) == "text"
    exe = tmp_path / "fake.txt"
    exe.write_bytes(b"MZ\x90\x00")
    assert detect_file_type(exe) == "exe"


def test_archive_members_lists_zip_entries(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("secret/customers.xlsx", "x")
    assert archive_members(archive) == ["secret/customers.xlsx"]


def test_filetype_rule_matches_renamed_pdf(tmp_path: Path) -> None:
    (tmp_path / "renamed.txt").write_bytes(b"%PDF-1.7\n")
    ruleset = RuleSet(
        rules=(Rule(id="pdf", name="PDF", matcher=Matcher(kind=MATCH_FILETYPE, value="pdf")),),
    )
    result = scan_paths([tmp_path], ruleset, ScanOptions())
    assert len(result.hits) == 1
    assert result.hits[0].matches[0].location == "文件类型"
