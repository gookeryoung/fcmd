"""校验文件生成 / 解析 / 比对纯逻辑测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apps.cmder import _checksum as checksum

SHA256_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
SHA256_HELLO = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def _files(tmp_path: Path) -> tuple[Path, Path]:
    a = tmp_path / "a.txt"
    a.write_text("abc", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    b = sub / "b.txt"
    b.write_text("hello", encoding="utf-8")
    return a, b


class TestAlgorithmMapping:
    def test_suffix_mapping_is_case_insensitive(self) -> None:
        assert checksum.algorithm_for_suffix(".SHA256") == "sha256"
        assert checksum.algorithm_for_suffix(".sfv") == "crc32"
        assert checksum.algorithm_for_suffix(".zip") is None

    def test_normalize_aliases(self) -> None:
        assert checksum.normalize_algorithm(" SFV ") == "crc32"
        assert checksum.normalize_algorithm("CRC") == "crc32"
        assert checksum.normalize_algorithm("sha256") == "sha256"

    def test_normalize_rejects_unknown(self) -> None:
        with pytest.raises(ValueError, match="不支持的校验算法"):
            checksum.normalize_algorithm("sha3")

    def test_suffix_tables_are_inverse(self) -> None:
        for suffix, algorithm in checksum.SUFFIX_ALGORITHMS.items():
            assert checksum.ALGORITHM_SUFFIXES[algorithm] == suffix


class TestGenerate:
    def test_build_manifest_sha256_uses_relative_paths(self, tmp_path: Path) -> None:
        a, b = _files(tmp_path)
        text = checksum.build_manifest([b, a], tmp_path, "sha256")
        assert text == f"{SHA256_ABC}  a.txt\n{SHA256_HELLO}  sub/b.txt\n"

    def test_build_manifest_reports_progress(self, tmp_path: Path) -> None:
        a, b = _files(tmp_path)
        seen: list[tuple[int, int]] = []
        checksum.build_manifest([a, b], tmp_path, "md5", progress=lambda done, total: seen.append((done, total)))
        assert seen == [(1, 2), (2, 2)]

    def test_build_manifest_skips_directories(self, tmp_path: Path) -> None:
        a, _ = _files(tmp_path)
        text = checksum.build_manifest([a, tmp_path / "sub"], tmp_path, "sha256")
        assert text == f"{SHA256_ABC}  a.txt\n"

    def test_build_manifest_sfv_uses_uppercase_crc(self, tmp_path: Path) -> None:
        a, _ = _files(tmp_path)
        text = checksum.build_manifest([a], tmp_path, "crc32")
        assert text == "a.txt 352441C2\n"

    def test_empty_manifest_is_empty_string(self, tmp_path: Path) -> None:
        assert checksum.build_manifest([], tmp_path, "sha256") == ""

    def test_relative_name_falls_back_to_basename(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside.txt"
        assert checksum.relative_name(outside, tmp_path) == "outside.txt"

    def test_manifest_name_uses_directory_name(self, tmp_path: Path) -> None:
        assert checksum.manifest_name(tmp_path / "backup", "sha256") == "backup.sha256"
        assert checksum.manifest_name(tmp_path / "backup", "crc32") == "backup.sfv"


class TestParse:
    def test_parse_gnu_and_binary_marker(self) -> None:
        text = f"{SHA256_ABC}  a.txt\n{SHA256_HELLO} *sub/b.txt\n"
        parsed = checksum.parse_manifest(text, "sha256")
        assert parsed.errors == []
        assert parsed.entries == [
            checksum.ChecksumEntry("a.txt", SHA256_ABC),
            checksum.ChecksumEntry("sub/b.txt", SHA256_HELLO),
        ]

    def test_parse_skips_comments_and_blank_lines(self) -> None:
        text = f"\ufeff; created by cmderw\n\n# note\n{SHA256_ABC}  a.txt\n   \n"
        parsed = checksum.parse_manifest(text, "sha256")
        assert [entry.name for entry in parsed.entries] == ["a.txt"]

    def test_parse_sfv_with_spaces_in_name(self) -> None:
        parsed = checksum.parse_manifest("my file.txt 352441C2\n", "crc32")
        assert parsed.errors == []
        assert parsed.entries == [checksum.ChecksumEntry("my file.txt", "352441c2")]

    def test_parse_collects_bad_lines(self) -> None:
        text = "not a checksum\nabc  short.txt\n" + "0" * 64 + "  \n"
        parsed = checksum.parse_manifest(text, "sha256")
        assert parsed.entries == []
        assert len(parsed.errors) == 3

    def test_parse_rejects_bad_crc_line(self) -> None:
        parsed = checksum.parse_manifest("a.txt XYZ12345\n", "crc32")
        assert parsed.entries == []
        assert parsed.errors == ["第 1 行无法解析：a.txt XYZ12345"]

    def test_roundtrip(self, tmp_path: Path) -> None:
        a, b = _files(tmp_path)
        for algorithm in ("sha256", "md5", "crc32"):
            text = checksum.build_manifest([a, b], tmp_path, algorithm)
            parsed = checksum.parse_manifest(text, algorithm)
            assert parsed.errors == []
            assert [entry.name for entry in parsed.entries] == ["a.txt", "sub/b.txt"]


class TestVerify:
    def test_verify_all_ok(self, tmp_path: Path) -> None:
        a, b = _files(tmp_path)
        text = checksum.build_manifest([a, b], tmp_path, "sha256")
        report = checksum.verify_manifest(text, tmp_path, "sha256")
        assert report.failures == []
        assert len(report.results) == 2
        assert "失败：0" in checksum.report_text(report)

    def test_verify_reports_mismatch_and_missing(self, tmp_path: Path) -> None:
        _files(tmp_path)
        text = f"{SHA256_HELLO}  a.txt\n{SHA256_ABC}  gone.txt\n"
        report = checksum.verify_manifest(text, tmp_path, "sha256")
        assert len(report.failures) == 2
        assert report.results[0].actual == SHA256_ABC
        assert report.results[1].error == "文件不存在"
        report_body = checksum.report_text(report)
        assert "FAIL  a.txt" in report_body
        assert "FAIL  gone.txt" in report_body

    def test_verify_collects_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        a, _ = _files(tmp_path)

        def broken(_path: Path, _algorithm: str) -> str:
            raise OSError("拒绝访问")

        monkeypatch.setattr(checksum, "compute_digest", broken)
        text = f"{SHA256_ABC}  {a.name}\n"
        report = checksum.verify_manifest(text, tmp_path, "sha256")
        assert report.results[0].error == "拒绝访问"
        assert report.results[0].actual is None

    def test_report_includes_parse_errors(self, tmp_path: Path) -> None:
        report = checksum.verify_manifest("garbage\n", tmp_path, "sha256")
        assert len(report.errors) == 1
        assert "无法解析的行" in checksum.report_text(report)


class TestCrc32:
    def test_crc32_known_value(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("abc", encoding="utf-8")
        assert checksum.compute_crc32(target) == "352441c2"

    def test_compute_digest_dispatches_crc32(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("abc", encoding="utf-8")
        assert checksum.compute_digest(target, "crc32") == "352441c2"
        assert checksum.compute_digest(target, "sha256") == SHA256_ABC
