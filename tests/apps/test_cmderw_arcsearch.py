"""归档内搜索纯逻辑测试：名称 glob、内容流式匹配、跳过规则与上限。"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from fcmd.apps.cmderw._cmderw_arcsearch import (
    ArchiveHit,
    format_archive_hit,
    search_archive,
)


def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as handle:
        for name, data in members.items():
            handle.writestr(name, data)
    return path


def make_tar_gz(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as handle:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            handle.addfile(info, io.BytesIO(data))
    return path


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    return make_zip(
        tmp_path / "sample.zip",
        {
            "docs/a.txt": b"Hello World\nsecond hello\n",
            "b.log": b"nothing here\n",
            "bin.dat": b"\x00\x01hello\n",
        },
    )


class TestNameSearch:
    def test_glob_matches_member_path(self, archive: Path) -> None:
        report = search_archive(archive, "*.txt")
        assert [hit.member for hit in report.hits] == ["docs/a.txt"]
        assert report.hits[0].line == 0
        assert report.scanned == 3

    def test_case_sensitivity(self, archive: Path) -> None:
        assert search_archive(archive, "*.TXT").hits == ()
        assert [hit.member for hit in search_archive(archive, "*.TXT", case_sensitive=False).hits] == ["docs/a.txt"]

    def test_tar_name_search(self, tmp_path: Path) -> None:
        tarball = make_tar_gz(tmp_path / "sample.tar.gz", {"x/y.md": b"# hi\n"})
        assert [hit.member for hit in search_archive(tarball, "*.md").hits] == ["x/y.md"]


class TestContentSearch:
    def test_hits_include_line_numbers(self, archive: Path) -> None:
        report = search_archive(archive, "hello", content=True, case_sensitive=False)
        assert [(hit.member, hit.line, hit.text) for hit in report.hits] == [
            ("docs/a.txt", 1, "Hello World"),
            ("docs/a.txt", 2, "second hello"),
        ]

    def test_case_sensitive(self, archive: Path) -> None:
        report = search_archive(archive, "hello", content=True, case_sensitive=True)
        assert [(hit.line, hit.text) for hit in report.hits] == [(2, "second hello")]

    def test_whole_word(self, archive: Path) -> None:
        report = search_archive(archive, "hell", content=True, case_sensitive=False, whole_word=True)
        assert report.hits == ()

    def test_regex_pattern(self, archive: Path) -> None:
        report = search_archive(archive, r"h.llo", content=True, case_sensitive=False)
        assert len(report.hits) == 2

    def test_binary_member_skipped(self, archive: Path) -> None:
        report = search_archive(archive, "hello", content=True, case_sensitive=False)
        assert all(hit.member != "bin.dat" for hit in report.hits)

    def test_max_hits_truncates(self, archive: Path) -> None:
        report = search_archive(archive, "hello", content=True, case_sensitive=False, max_hits=1)
        assert len(report.hits) == 1
        assert report.truncated

    def test_max_member_size_skips(self, archive: Path) -> None:
        report = search_archive(archive, "hello", content=True, case_sensitive=False, max_member_size=1)
        assert report.hits == ()
        assert report.skipped == 3

    def test_max_lines_limits_each_member(self, archive: Path) -> None:
        report = search_archive(archive, "hello", content=True, case_sensitive=False, max_lines=1)
        assert [(hit.line, hit.text) for hit in report.hits] == [(1, "Hello World")]

    def test_tar_content_search(self, tmp_path: Path) -> None:
        tarball = make_tar_gz(tmp_path / "sample.tar.gz", {"x/y.md": b"# hello\n"})
        report = search_archive(tarball, "hello", content=True, case_sensitive=False)
        assert [(hit.member, hit.line, hit.text) for hit in report.hits] == [("x/y.md", 1, "# hello")]

    def test_encrypted_member_skipped_for_content(self, tmp_path: Path) -> None:
        pyzipper = pytest.importorskip("pyzipper")
        archive = tmp_path / "secret.zip"
        with pyzipper.AESZipFile(archive, "w", encryption=pyzipper.WZ_AES) as handle:
            handle.setpassword(b"pw")
            handle.writestr("secret.txt", b"hello\n")
        assert search_archive(archive, "hello", content=True, case_sensitive=False).skipped == 1
        assert [hit.member for hit in search_archive(archive, "*.txt").hits] == ["secret.txt"]


class TestValidation:
    def test_missing_archive(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="归档不存在"):
            search_archive(tmp_path / "missing.zip", "*.txt")

    def test_unsupported_format(self, tmp_path: Path) -> None:
        path = tmp_path / "single.gz"
        path.write_bytes(b"x")
        with pytest.raises(ValueError, match="不支持包内搜索"):
            search_archive(path, "*.txt")

    def test_empty_pattern(self, archive: Path) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            search_archive(archive, "")

    def test_invalid_max_hits(self, archive: Path) -> None:
        with pytest.raises(ValueError, match="命中上限"):
            search_archive(archive, "*", max_hits=0)


class TestFormat:
    def test_name_and_content_hits(self) -> None:
        assert format_archive_hit(ArchiveHit("a.txt")) == "a.txt"
        assert format_archive_hit(ArchiveHit("a.txt", 3, "text")) == "a.txt:3: text"
