"""_edit 测试：字段白名单、取值校验、预演、备份与原子写入。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apps.peview import _edit
from fcmd.apps.peview._pe import load_pe
from fcmd.apps.peview._reader import PEError, compute_checksum


def _copy(source: Path, tmp_path: Path, name: str = "sample.exe") -> Path:
    target = tmp_path / name
    target.write_bytes(source.read_bytes())
    return target


class TestParseValue:
    """数值与赋值解析。"""

    def test_decimal_and_hex(self) -> None:
        assert _edit.parse_value("16") == 16
        assert _edit.parse_value("0x10") == 0x10
        assert _edit.parse_value(" 1_000 ") == 1000

    @pytest.mark.parametrize("text", ["", "   ", "abc", "0x", "0xZZ"])
    def test_invalid(self, text: str) -> None:
        with pytest.raises(PEError, match=r"无法解析|不能为空"):
            _edit.parse_value(text)

    def test_assignments(self) -> None:
        assert _edit.parse_assignments(["entry_point=0x2000", "subsystem=3"]) == {
            "entry_point": 0x2000,
            "subsystem": 3,
        }

    @pytest.mark.parametrize("item", ["entry_point", "=3", " =5"])
    def test_assignment_requires_name_and_value(self, item: str) -> None:
        with pytest.raises(PEError, match="NAME=VALUE"):
            _edit.parse_assignments([item])


class TestPlanEdits:
    """白名单与取值校验（不写文件）。"""

    def test_accepts_whitelisted_fields(self, pe32_file: Path) -> None:
        pe = load_pe(pe32_file)
        changes = _edit.plan_edits(pe, {"entry_point": 0x1100, "subsystem": 2})
        assert [(change.name, change.old, change.new) for change in changes] == [
            ("entry_point", 0x1000, 0x1100),
            ("subsystem", 3, 2),
        ]
        assert changes[0].offset == pe.optional.offset + 16

    @pytest.mark.parametrize(
        ("name", "message"),
        [
            ("e_lfanew", "DOS 头 e_lfanew 改变文件布局"),
            ("size_of_headers", "头部大小 改变文件布局"),
            ("section.0.virtual_address", "改变文件布局"),
            ("section.0.size_of_raw_data", "改变文件布局"),
            ("directory.0.virtual_address", "数据目录字段"),
        ],
    )
    def test_rejects_structural_fields(self, pe32_file: Path, name: str, message: str) -> None:
        with pytest.raises(PEError, match=message):
            _edit.plan_edits(load_pe(pe32_file), {name: 1})

    def test_rejects_unknown_field(self, pe32_file: Path) -> None:
        with pytest.raises(PEError, match="未知字段"):
            _edit.plan_edits(load_pe(pe32_file), {"nope": 1})

    def test_rejects_empty_edit(self, pe32_file: Path) -> None:
        with pytest.raises(PEError, match="没有指定"):
            _edit.plan_edits(load_pe(pe32_file), {})

    def test_rejects_out_of_range(self, pe32_file: Path) -> None:
        with pytest.raises(PEError, match="超出 2 字节范围"):
            _edit.plan_edits(load_pe(pe32_file), {"subsystem": 0x10000})
        with pytest.raises(PEError, match="超出 4 字节范围"):
            _edit.plan_edits(load_pe(pe32_file), {"entry_point": -1})

    def test_image_base_alignment(self, pe64_file: Path) -> None:
        pe = load_pe(pe64_file)
        with pytest.raises(PEError, match="64K 对齐"):
            _edit.plan_edits(pe, {"image_base": 0x140001000})
        assert _edit.plan_edits(pe, {"image_base": 0x140010000})[0].new == 0x140010000

    def test_entry_point_within_image(self, pe64_file: Path) -> None:
        pe = load_pe(pe64_file)
        with pytest.raises(PEError, match="小于 SizeOfImage"):
            _edit.plan_edits(pe, {"entry_point": 0x2000})
        assert _edit.plan_edits(pe, {"entry_point": 0x1FFF})[0].new == 0x1FFF

    def test_section_alignment_code(self, pe64_file: Path) -> None:
        pe = load_pe(pe64_file)
        with pytest.raises(PEError, match="节对齐码"):
            _edit.plan_edits(pe, {"section.0.characteristics": 0x60F00020})
        assert _edit.plan_edits(pe, {"section.0.characteristics": 0xE0000020})[0].new == 0xE0000020

    def test_plan_does_not_touch_file(self, pe32_file: Path) -> None:
        before = pe32_file.read_bytes()
        _edit.plan_edits(load_pe(pe32_file), {"subsystem": 2})
        assert pe32_file.read_bytes() == before


class TestVerifyGuards:
    """写入后校验的两条防线（回读值 / 结构指纹）。"""

    def test_detects_wrong_value(self, pe32_file: Path) -> None:
        pe = load_pe(pe32_file)
        change = _edit.FieldChange(
            name="subsystem",
            label="Subsystem",
            offset=pe.optional.offset + 68,
            size=2,
            old=pe.optional.subsystem,
            new=0xFFFF,
        )
        with pytest.raises(PEError, match="回读"):
            _edit._verify(pe, pe, (change,))

    def test_detects_structure_change(self, pe32_file: Path, pe64_file: Path) -> None:
        with pytest.raises(PEError, match="结构发生变化"):
            _edit._verify(load_pe(pe32_file), load_pe(pe64_file), ())


class TestApplyEdits:
    """预演 / 写入 / 备份 / 失败清理。"""

    def test_dry_run_keeps_file(self, pe32_file: Path, tmp_path: Path) -> None:
        target = _copy(pe32_file, tmp_path)
        before = target.read_bytes()
        result = _edit.apply_edits(target, {"subsystem": 2}, apply=False, allow_signature_break=True)
        assert result.dry_run is True
        assert result.backup is None
        assert result.changes[0].new == 2
        assert target.read_bytes() == before
        assert not list(tmp_path.glob("*.backup*"))
        assert not list(tmp_path.glob("*.tmp"))

    def test_apply_creates_backup_and_writes(self, pe32_file: Path, tmp_path: Path) -> None:
        target = _copy(pe32_file, tmp_path)
        before = target.read_bytes()
        result = _edit.apply_edits(target, {"subsystem": 2}, apply=True, allow_signature_break=True)
        after = target.read_bytes()
        assert result.dry_run is False
        assert result.backup is not None
        assert result.backup.read_bytes() == before
        assert after != before
        pe = load_pe(target)
        assert pe.optional.subsystem == 2
        assert pe.optional.checksum == compute_checksum(pe.data, pe.optional.offset + 64)
        assert result.checksum == pe.optional.checksum
        assert result.signature_break is True
        assert not list(tmp_path.glob("*.tmp"))

    def test_backup_numbering(self, pe64_file: Path, tmp_path: Path) -> None:
        target = _copy(pe64_file, tmp_path, "app.exe")
        first = _edit.apply_edits(target, {"subsystem": 2}, apply=True, allow_signature_break=True)
        second = _edit.apply_edits(target, {"subsystem": 3}, apply=True, allow_signature_break=True)
        assert first.backup is not None and first.backup.name == "app.exe.backup1"
        assert second.backup is not None and second.backup.name == "app.exe.backup2"
        assert load_pe(target).optional.subsystem == 3

    def test_signature_requires_confirmation(self, pe32_file: Path, tmp_path: Path) -> None:
        target = _copy(pe32_file, tmp_path)
        before = target.read_bytes()
        with pytest.raises(PEError, match="Authenticode"):
            _edit.apply_edits(target, {"subsystem": 2}, apply=True)
        assert target.read_bytes() == before
        assert not list(tmp_path.glob("*.backup*"))

    def test_signature_flag_reported(self, pe32_file: Path) -> None:
        result = _edit.apply_edits(pe32_file, {"subsystem": 2}, allow_signature_break=True)
        assert result.signature_break is True

    def test_no_checksum_recompute_reports_problem(self, pe64_file: Path, tmp_path: Path) -> None:
        target = _copy(pe64_file, tmp_path)
        original_checksum = load_pe(target).optional.checksum
        result = _edit.apply_edits(
            target,
            {"subsystem": 2},
            apply=True,
            allow_signature_break=True,
            recompute_checksum=False,
        )
        assert any("校验和不一致" in problem for problem in result.problems)
        assert load_pe(target).optional.checksum == original_checksum
        assert load_pe(target).optional.subsystem == 2

    def test_explicit_checksum_wins(self, pe64_file: Path, tmp_path: Path) -> None:
        target = _copy(pe64_file, tmp_path)
        result = _edit.apply_edits(target, {"checksum": 0x1234}, apply=True, allow_signature_break=True)
        assert result.checksum == 0x1234
        assert load_pe(target).optional.checksum == 0x1234

    def test_invalid_edit_leaves_file_untouched(self, pe32_file: Path, tmp_path: Path) -> None:
        target = _copy(pe32_file, tmp_path)
        before = target.read_bytes()
        with pytest.raises(PEError, match="改变文件布局"):
            _edit.apply_edits(
                target, {"entry_point": 0x1100, "size_of_image": 1}, apply=True, allow_signature_break=True
            )
        assert target.read_bytes() == before
        assert not list(tmp_path.glob("*.backup*"))
        assert not list(tmp_path.glob("*.tmp"))

    def test_write_failure_cleans_up(self, pe32_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = _copy(pe32_file, tmp_path)
        before = target.read_bytes()

        def boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(_edit.shutil, "copy2", boom)
        with pytest.raises(OSError, match="disk full"):
            _edit.apply_edits(target, {"subsystem": 2}, apply=True, allow_signature_break=True)
        assert target.read_bytes() == before
        assert not list(tmp_path.glob("*.tmp"))
        assert not list(tmp_path.glob("*.backup*"))

    def test_verify_failure_aborts(self, pe32_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = _copy(pe32_file, tmp_path)
        before = target.read_bytes()

        def fake_verify(*_args: object, **_kwargs: object) -> None:
            raise PEError("写入后校验失败：模拟")

        monkeypatch.setattr(_edit, "_verify", fake_verify)
        with pytest.raises(PEError, match="模拟"):
            _edit.apply_edits(target, {"subsystem": 2}, apply=True, allow_signature_break=True)
        assert target.read_bytes() == before
        assert not list(tmp_path.glob("*.tmp"))
        assert not list(tmp_path.glob("*.backup*"))
