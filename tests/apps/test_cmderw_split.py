"""文件分割 / 合并纯逻辑测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from fcmd.apps.cmderw import _cmderw_split as splitmod
from fcmd.apps.cmderw._cmderw_split import (
    default_merged_name,
    discover_parts,
    merge_parts,
    part_names,
    split_file,
    split_plan,
)


class TestSplitPlan:
    def test_by_size(self) -> None:
        assert split_plan(10, part_size=4) == [4, 4, 2]
        assert split_plan(8, part_size=4) == [4, 4]

    def test_by_parts(self) -> None:
        assert split_plan(10, parts=3) == [4, 3, 3]
        assert split_plan(10, parts=10) == [1] * 10

    def test_parts_more_than_bytes(self) -> None:
        assert split_plan(3, parts=10) == [1, 1, 1]

    def test_invalid_arguments(self) -> None:
        with pytest.raises(ValueError, match="文件为空"):
            split_plan(0, parts=2)
        with pytest.raises(ValueError, match="指定一种分割方式"):
            split_plan(10)
        with pytest.raises(ValueError, match="指定一种分割方式"):
            split_plan(10, part_size=2, parts=3)
        with pytest.raises(ValueError, match="分卷大小"):
            split_plan(10, part_size=0)
        with pytest.raises(ValueError, match="分卷份数"):
            split_plan(10, parts=0)


class TestPartNames:
    def test_three_digit_suffix(self) -> None:
        assert part_names("data.bin", 2) == ["data.bin.001", "data.bin.002"]

    def test_width_grows_for_large_counts(self) -> None:
        names = part_names("data.bin", 1000)
        assert names[0] == "data.bin.0001"
        assert names[-1] == "data.bin.1000"

    def test_invalid_count(self) -> None:
        with pytest.raises(ValueError, match="分卷份数"):
            part_names("data.bin", 0)


class TestSplitFile:
    def test_split_and_checksum(self, tmp_path: Path) -> None:
        source = tmp_path / "data.bin"
        source.write_bytes(b"0123456789")
        events: list[tuple[int, int, str]] = []
        result = split_file(source, parts=3, progress=lambda done, total, name: events.append((done, total, name)))
        assert [part.stat().st_size for part in result.parts] == [4, 3, 3]
        assert b"".join(part.read_bytes() for part in result.parts) == b"0123456789"
        assert result.size == 10
        assert result.digest == hashlib.sha256(b"0123456789").hexdigest()
        assert result.checksum.read_text(encoding="utf-8").strip().endswith(" 10")
        assert events[-1][0] == 10

    def test_by_size(self, tmp_path: Path) -> None:
        source = tmp_path / "data.bin"
        source.write_bytes(b"abcdef")
        result = split_file(source, part_size=4)
        assert [part.name for part in result.parts] == ["data.bin.001", "data.bin.002"]
        assert result.parts[1].read_bytes() == b"ef"

    def test_existing_parts_require_overwrite(self, tmp_path: Path) -> None:
        source = tmp_path / "data.bin"
        source.write_bytes(b"abcdef")
        split_file(source, parts=2)
        with pytest.raises(FileExistsError, match="分卷已存在"):
            split_file(source, parts=2)
        assert split_file(source, parts=2, overwrite=True).size == 6

    def test_empty_file(self, tmp_path: Path) -> None:
        source = tmp_path / "empty.bin"
        source.write_bytes(b"")
        with pytest.raises(ValueError, match="文件为空"):
            split_file(source, parts=2)

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="文件不存在"):
            split_file(tmp_path / "missing.bin", parts=2)

    def test_failure_cleans_created_parts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = tmp_path / "data.bin"
        source.write_bytes(b"0123456789")

        def boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("磁盘满")

        monkeypatch.setattr(splitmod, "_write_checksum", boom)
        with pytest.raises(OSError, match="磁盘满"):
            split_file(source, parts=2)
        assert not list(tmp_path.glob("data.bin.*"))


class TestDiscoverParts:
    def test_discovers_from_any_part(self, tmp_path: Path) -> None:
        source = tmp_path / "data.bin"
        source.write_bytes(b"0123456789")
        split_file(source, parts=3)
        assert [part.name for part in discover_parts(tmp_path / "data.bin.002")] == [
            "data.bin.001",
            "data.bin.002",
            "data.bin.003",
        ]

    def test_gap_is_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "data.bin.001").write_bytes(b"a")
        (tmp_path / "data.bin.003").write_bytes(b"c")
        with pytest.raises(ValueError, match="编号不连续"):
            discover_parts(tmp_path / "data.bin.001")

    def test_non_part_name(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="不是分卷文件"):
            discover_parts(tmp_path / "data.bin")

    def test_no_parts(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="未找到分卷"):
            discover_parts(tmp_path / "data.bin.009")


class TestMergeParts:
    def test_merge_roundtrip(self, tmp_path: Path) -> None:
        source = tmp_path / "data.bin"
        source.write_bytes(b"0123456789")
        result = split_file(source, parts=3)
        output = tmp_path / "merged.bin"
        events: list[tuple[int, int, str]] = []
        merged = merge_parts(
            list(reversed(result.parts)),
            output,
            progress=lambda done, total, name: events.append((done, total, name)),
        )
        assert output.read_bytes() == b"0123456789"
        assert merged.verified
        assert merged.digest == result.digest
        assert merged.size == 10
        assert events[-1][0] == 10

    def test_missing_checksum_is_not_verified(self, tmp_path: Path) -> None:
        source = tmp_path / "data.bin"
        source.write_bytes(b"abcdef")
        result = split_file(source, parts=2)
        result.checksum.unlink()
        merged = merge_parts(list(result.parts), tmp_path / "merged.bin")
        assert not merged.verified
        assert (tmp_path / "merged.bin").read_bytes() == b"abcdef"

    def test_checksum_mismatch_removes_output(self, tmp_path: Path) -> None:
        source = tmp_path / "data.bin"
        source.write_bytes(b"abcdef")
        result = split_file(source, parts=2)
        result.checksum.write_text(f"{'0' * 64}  6\n", encoding="utf-8")
        output = tmp_path / "merged.bin"
        with pytest.raises(ValueError, match="校验失败"):
            merge_parts(list(result.parts), output)
        assert not output.exists()

    def test_verify_disabled_skips_checksum(self, tmp_path: Path) -> None:
        source = tmp_path / "data.bin"
        source.write_bytes(b"abcdef")
        result = split_file(source, parts=2)
        result.checksum.write_text(f"{'0' * 64}  6\n", encoding="utf-8")
        merged = merge_parts(list(result.parts), tmp_path / "merged.bin", verify=False)
        assert not merged.verified
        assert (tmp_path / "merged.bin").read_bytes() == b"abcdef"

    def test_mixed_bases_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "a.bin.001").write_bytes(b"a")
        (tmp_path / "b.bin.002").write_bytes(b"b")
        with pytest.raises(ValueError, match="不属于同一组"):
            merge_parts([tmp_path / "a.bin.001", tmp_path / "b.bin.002"], tmp_path / "out.bin")

    def test_non_consecutive_rejected(self, tmp_path: Path) -> None:
        first = tmp_path / "a.bin.001"
        third = tmp_path / "a.bin.003"
        first.write_bytes(b"a")
        third.write_bytes(b"c")
        with pytest.raises(ValueError, match="编号不连续"):
            merge_parts([first, third], tmp_path / "out.bin")

    def test_non_part_and_missing(self, tmp_path: Path) -> None:
        plain = tmp_path / "a.bin"
        plain.write_bytes(b"a")
        with pytest.raises(ValueError, match="不是分卷"):
            merge_parts([plain], tmp_path / "out.bin")
        with pytest.raises(FileNotFoundError, match="分卷不存在"):
            merge_parts([tmp_path / "missing.bin.001"], tmp_path / "out.bin")
        with pytest.raises(ValueError, match="没有可合并"):
            merge_parts([], tmp_path / "out.bin")

    def test_default_merged_name(self, tmp_path: Path) -> None:
        assert default_merged_name(tmp_path / "data.bin.001") == "data.bin"
        with pytest.raises(ValueError, match="不是分卷文件"):
            default_merged_name(tmp_path / "data.bin")
