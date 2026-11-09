"""reshack 工具测试。

验证 ``fcmd.tools.system.reshack``：
- 工具注册（list/extract/replace/export-all 子命令）
- PE 文件头识别、类型名 / 扩展名 / 大小格式化
- 资源枚举、导出、全部导出、替换（含备份与各类失败分支）
- CLI 端到端输出与错误提示
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.tools.system import reshack
from fcmd.tools.system.reshack import (
    RT_BITMAP,
    RT_ICON,
    PEError,
    PEFile,
    PEFileInfo,
    ResourceEntry,
    backup_path,
    bitmap_to_bmp,
    export_payload,
    format_file_info,
    format_file_size,
    format_resource_report,
    format_resource_table,
    icon_to_ico,
    is_pe_file,
    resource_extension,
    resource_type_name,
)


# ============================================================================ #
# 假 PE 对象（避免测试依赖真实二进制）
# ============================================================================ #
class _Struct:
    def __init__(self, rva: int, size: int) -> None:
        self.OffsetToData = rva
        self.Size = size


class _DataEntry:
    def __init__(self, language: int, rva: int, size: int) -> None:
        self.lang = language
        self.struct = _Struct(rva, size)


class _LangEntry:
    def __init__(self, data: _DataEntry) -> None:
        self.data = data


class _Directory:
    def __init__(self, entries: list[Any]) -> None:
        self.entries = entries


class _TypeEntry:
    def __init__(self, type_id: int, entries: list[Any]) -> None:
        self.id = type_id
        self.directory = _Directory(entries)


class _NameEntry:
    def __init__(self, name_id: int, entries: list[Any]) -> None:
        self.id = name_id
        self.directory = _Directory(entries)


class FakePE:
    """最小化的 pefile.PE 替身（资源树 + 数据读写记录）。"""

    def __init__(self, entries: list[tuple[int, int, int, int, bytes]]) -> None:
        self.FILE_HEADER = SimpleNamespace(Machine=0x8664)
        self.OPTIONAL_HEADER = SimpleNamespace(
            Subsystem=2,
            Magic=0x20B,
            ImageBase=0x140000000,
            AddressOfEntryPoint=0x1000,
        )
        self._data: dict[int, bytes] = {}
        type_nodes: dict[int, list[_NameEntry]] = {}
        for type_id, name_id, language, rva, payload in entries:
            self._data[rva] = payload
            name_nodes = type_nodes.setdefault(type_id, [])
            name_node = next((node for node in name_nodes if node.id == name_id), None)
            if name_node is None:
                name_node = _NameEntry(name_id, [])
                name_nodes.append(name_node)
            name_node.directory.entries.append(_LangEntry(_DataEntry(language, rva, len(payload))))
        self.DIRECTORY_ENTRY_RESOURCE = _Directory(
            [_TypeEntry(type_id, nodes) for type_id, nodes in type_nodes.items()]
        )
        self.writes: list[tuple[int, bytes]] = []
        self.write_path: str | None = None
        self.closed = False
        self.dll = False

    def is_dll(self) -> bool:
        return self.dll

    def get_data(self, rva: int, size: int) -> bytes:
        return self._data.get(rva, b"")[:size]

    def get_offset_from_rva(self, rva: int) -> int:
        return rva - 0x2000 + 0x400

    def set_bytes_at_offset(self, offset: int, data: bytes) -> bool:
        self.writes.append((offset, data))
        return True

    def write(self, filename: str) -> None:
        self.write_path = filename
        Path(filename).write_bytes(b"PEFILE")

    def close(self) -> None:
        self.closed = True


def _fake_module(pe: FakePE) -> SimpleNamespace:
    return SimpleNamespace(PE=lambda _path: pe)


def _write_pe_file(path: Path, offset: int = 0x40) -> Path:
    header = bytearray(max(offset + 4, 64))
    header[0:2] = b"MZ"
    header[0x3C:0x40] = offset.to_bytes(4, "little")
    header[offset : offset + 4] = b"PE\x00\x00"
    path.write_bytes(bytes(header))
    return path


class TestRequirePefile:
    """pefile 惰性导入缓存与缺失降级。"""

    def test_caches_module(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = SimpleNamespace()
        calls: list[str] = []
        monkeypatch.setattr(reshack, "_pefile_module", None)
        monkeypatch.setattr(
            reshack.importlib,
            "import_module",
            lambda name: calls.append(name) or fake,
        )
        assert reshack._require_pefile() is fake
        assert reshack._require_pefile() is fake
        assert calls == ["pefile"]
        reshack._pefile_module = None

    def test_missing_module(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(_name: str) -> Any:
            raise ImportError("missing")

        monkeypatch.setattr(reshack, "_pefile_module", None)
        monkeypatch.setattr(reshack.importlib, "import_module", boom)
        assert reshack._require_pefile() is None
        reshack._pefile_module = None


RESOURCE_ENTRIES: list[tuple[int, int, int, int, bytes]] = [
    (3, 1, 0, 0x2100, b"icon-data"),
    (3, 2, 0, 0x2200, b"other-icon"),
    (16, 1, 1033, 0x2300, b"version-info"),
    (24, 1, 0, 0x2400, b"<manifest/>"),
]


@pytest.fixture
def pe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[PEFile, FakePE, Path]:
    """已加载 FakePE 的 PEFile、假 PE 对象与磁盘文件路径。"""
    file_path = _write_pe_file(tmp_path / "app.exe")
    fake = FakePE(RESOURCE_ENTRIES)
    monkeypatch.setattr(reshack, "_require_pefile", lambda: _fake_module(fake))
    wrapper = PEFile()
    wrapper.open(file_path)
    return wrapper, fake, file_path


# ============================================================================ #
# 注册与基础函数
# ============================================================================ #
def test_tool_registered() -> None:
    """reshack 应注册 list/extract/replace/export-all 四个子命令。"""
    assert set(_TOOL_REGISTRY["reshack"]) == {"list", "extract", "replace", "export-all"}


class TestHelpers:
    """格式化与识别辅助函数。"""

    @pytest.mark.parametrize(
        ("size", "expected"),
        [(0, "0 B"), (512, "512 B"), (2048, "2.0 KB"), (3 * 1024 * 1024, "3.0 MB")],
    )
    def test_format_file_size(self, size: int, expected: str) -> None:
        assert format_file_size(size) == expected

    def test_resource_type_name(self) -> None:
        assert resource_type_name(3) == "Icon"
        assert resource_type_name(999) == "Unknown (999)"

    def test_resource_extension(self) -> None:
        assert resource_extension(3) == ".ico"
        assert resource_extension(999) == ".bin"

    def test_is_pe_file_valid(self, tmp_path: Path) -> None:
        assert is_pe_file(_write_pe_file(tmp_path / "a.exe")) is True

    def test_is_pe_file_signature_after_first_64_bytes(self, tmp_path: Path) -> None:
        assert is_pe_file(_write_pe_file(tmp_path / "b.dll", offset=0x80)) is True

    def test_is_pe_file_signature_inside_header(self, tmp_path: Path) -> None:
        assert is_pe_file(_write_pe_file(tmp_path / "c.exe", offset=0x20)) is True

    def test_is_pe_file_rejects_wrong_suffix(self, tmp_path: Path) -> None:
        path = _write_pe_file(tmp_path / "a.txt")
        assert is_pe_file(path) is False

    def test_is_pe_file_rejects_missing(self, tmp_path: Path) -> None:
        assert is_pe_file(tmp_path / "missing.exe") is False

    def test_is_pe_file_rejects_truncated(self, tmp_path: Path) -> None:
        path = tmp_path / "short.exe"
        path.write_bytes(b"MZ")
        assert is_pe_file(path) is False

    def test_is_pe_file_rejects_bad_signature(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.exe"
        header = bytearray(64)
        header[0:2] = b"MZ"
        header[0x3C:0x40] = (0x40).to_bytes(4, "little")
        path.write_bytes(bytes(header))
        assert is_pe_file(path) is False

    def test_is_pe_file_handles_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = _write_pe_file(tmp_path / "locked.exe")
        monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()))
        assert is_pe_file(path) is False


class TestBackupPath:
    """备份路径去重。"""

    def test_first_backup(self, tmp_path: Path) -> None:
        path = tmp_path / "a.exe"
        assert backup_path(path) == tmp_path / "a.exe.backup"

    def test_increments_when_exists(self, tmp_path: Path) -> None:
        path = tmp_path / "a.exe"
        (tmp_path / "a.exe.backup").write_bytes(b"1")
        assert backup_path(path) == tmp_path / "a.exe.backup1"

    def test_increments_twice(self, tmp_path: Path) -> None:
        path = tmp_path / "a.exe"
        (tmp_path / "a.exe.backup").write_bytes(b"1")
        (tmp_path / "a.exe.backup1").write_bytes(b"2")
        assert backup_path(path) == tmp_path / "a.exe.backup2"


class TestReports:
    """文件信息与资源表格输出。"""

    INFO = PEFileInfo(
        path=Path("app.exe"),
        machine="x64（64 位）",
        subsystem="Windows GUI",
        is_dll=False,
        is_64bit=True,
        image_base=0x140000000,
        entry_point=0x1000,
        resource_count=2,
    )

    def test_format_file_info(self) -> None:
        text = format_file_info(self.INFO)
        assert "app.exe" in text
        assert "可执行文件（64 位）" in text
        assert "0x140000000" in text
        assert "入口点: 0x1000" in text

    def test_format_file_info_dll(self) -> None:
        info = PEFileInfo(
            path=Path("a.dll"),
            machine="x86（32 位）",
            subsystem="Windows GUI",
            is_dll=True,
            is_64bit=False,
            image_base=0x10000000,
            entry_point=0,
            resource_count=0,
        )
        assert "DLL（32 位）" in format_file_info(info)

    def test_format_resource_table_empty(self) -> None:
        assert format_resource_table([]) == "(该文件没有资源)"

    def test_format_resource_table_rows(self, pe: tuple[PEFile, FakePE, Path]) -> None:
        wrapper, _fake, _path = pe
        text = format_resource_table(wrapper.resources())
        assert "Icon" in text
        assert "Manifest" in text

    def test_format_resource_report(self, pe: tuple[PEFile, FakePE, Path]) -> None:
        wrapper, _fake, _path = pe
        text = format_resource_report(wrapper.file_info(), wrapper.resources())
        assert "资源类型:" in text
        assert "Manifest" in text
        assert "资源数量: 4" in text

    def test_format_resource_report_without_resources(self) -> None:
        info = PEFileInfo(
            path=Path("a.exe"),
            machine="x64（64 位）",
            subsystem="Windows GUI",
            is_dll=False,
            is_64bit=True,
            image_base=0,
            entry_point=0,
            resource_count=0,
        )
        text = format_resource_report(info, [])
        assert "资源类型:" not in text
        assert "(该文件没有资源)" in text


class TestExportPayload:
    """Icon / Bitmap 导出时的文件头包装。"""

    @staticmethod
    def _dib(width: int = 16, height: int = 16, bit_count: int = 32) -> bytes:
        header = bytearray(40)
        header[0:4] = (40).to_bytes(4, "little")
        header[4:8] = width.to_bytes(4, "little", signed=True)
        header[8:12] = (height * 2).to_bytes(4, "little", signed=True)
        header[12:14] = (1).to_bytes(2, "little")
        header[14:16] = bit_count.to_bytes(2, "little")
        return bytes(header) + b"\x00" * 16

    def test_icon_to_ico_header(self) -> None:
        dib = self._dib()
        ico = icon_to_ico(dib)
        assert ico is not None
        assert ico[:6] == b"\x00\x00\x01\x00\x01\x00"
        assert ico[6:8] == bytes((16, 16))
        assert int.from_bytes(ico[14:18], "little") == len(dib)
        assert int.from_bytes(ico[18:22], "little") == 22
        assert ico[22:] == dib

    def test_icon_to_ico_uses_zero_for_256(self) -> None:
        ico = icon_to_ico(self._dib(width=256, height=256))
        assert ico is not None
        assert ico[6:8] == b"\x00\x00"

    @pytest.mark.parametrize("data", [b"short", b"x" * 20, bytes(40)])
    def test_icon_to_ico_rejects_invalid(self, data: bytes) -> None:
        assert icon_to_ico(data) is None

    def test_bitmap_to_bmp_header(self) -> None:
        dib = self._dib(bit_count=24)
        bmp = bitmap_to_bmp(dib)
        assert bmp is not None
        assert bmp[:2] == b"BM"
        assert int.from_bytes(bmp[2:6], "little") == len(dib) + 14
        assert int.from_bytes(bmp[10:14], "little") == 54
        assert bmp[14:] == dib

    def test_bitmap_to_bmp_palette_offset(self) -> None:
        dib = bytearray(self._dib(bit_count=8))
        dib[32:36] = (16).to_bytes(4, "little")
        bmp = bitmap_to_bmp(bytes(dib))
        assert bmp is not None
        assert int.from_bytes(bmp[10:14], "little") == 14 + 40 + 16 * 4

    @pytest.mark.parametrize("data", [b"short", b"x" * 20])
    def test_bitmap_to_bmp_rejects_short(self, data: bytes) -> None:
        assert bitmap_to_bmp(data) is None

    def test_bitmap_to_bmp_rejects_old_header(self) -> None:
        data = (12).to_bytes(4, "little") + bytes(36)
        assert bitmap_to_bmp(data) is None

    def test_export_payload_icon(self) -> None:
        resource = ResourceEntry(RT_ICON, "Icon", 1, 0, 0x2000, 56, self._dib())
        payload = export_payload(resource)
        assert payload.startswith(b"\x00\x00\x01\x00\x01\x00")

    def test_export_payload_bitmap(self) -> None:
        resource = ResourceEntry(RT_BITMAP, "Bitmap", 1, 0, 0x2000, 56, self._dib())
        assert export_payload(resource).startswith(b"BM")

    def test_export_payload_other_type(self) -> None:
        resource = ResourceEntry(24, "Manifest", 1, 0, 0x2000, 3, b"xml")
        assert export_payload(resource) == b"xml"

    def test_export_payload_invalid_header_falls_back(self) -> None:
        resource = ResourceEntry(RT_ICON, "Icon", 1, 0, 0x2000, 9, b"icon-data")
        assert export_payload(resource) == b"icon-data"


# ============================================================================ #
# PEFile 加载与资源操作
# ============================================================================ #
class TestPEFileOpen:
    """加载失败分支。"""

    def test_open_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(PEError, match="文件不存在"):
            PEFile().open(tmp_path / "missing.exe")

    def test_open_not_pe(self, tmp_path: Path) -> None:
        path = tmp_path / "plain.exe"
        path.write_bytes(b"not a pe")
        with pytest.raises(PEError, match="不是有效的 PE 文件"):
            PEFile().open(path)

    def test_open_without_pefile(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = _write_pe_file(tmp_path / "app.exe")
        monkeypatch.setattr(reshack, "_require_pefile", lambda: None)
        with pytest.raises(PEError, match="fcmd\\[pe\\]"):
            PEFile().open(path)

    def test_open_parse_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = _write_pe_file(tmp_path / "app.exe")

        def boom(_path: str) -> Any:
            raise ValueError("bad pe")

        monkeypatch.setattr(reshack, "_require_pefile", lambda: SimpleNamespace(PE=boom))
        with pytest.raises(PEError, match="解析 PE 文件失败"):
            PEFile().open(path)

    def test_operations_require_loaded(self) -> None:
        wrapper = PEFile()
        with pytest.raises(PEError, match="尚未加载"):
            wrapper.file_info()
        with pytest.raises(PEError, match="尚未加载"):
            wrapper.resources()

    def test_close_resets_state(self, pe: tuple[PEFile, FakePE, Path]) -> None:
        wrapper, fake, _path = pe
        wrapper.close()
        assert fake.closed is True
        assert wrapper.path is None
        with pytest.raises(PEError, match="尚未加载"):
            wrapper.resources()

    def test_close_without_load(self) -> None:
        PEFile().close()

    def test_close_ignores_non_callable(self, pe: tuple[PEFile, FakePE, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        wrapper, fake, _path = pe
        monkeypatch.setattr(fake, "close", None)
        wrapper.close()
        assert wrapper.path is None

    def test_require_path_without_path(self, pe: tuple[PEFile, FakePE, Path]) -> None:
        wrapper, _fake, _path = pe
        wrapper.path = None
        with pytest.raises(PEError, match="尚未加载"):
            wrapper._require_path()

    def test_context_manager(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = _write_pe_file(tmp_path / "app.exe")
        fake = FakePE(RESOURCE_ENTRIES)
        monkeypatch.setattr(reshack, "_require_pefile", lambda: _fake_module(fake))
        with PEFile() as wrapper:
            wrapper.open(path)
        assert fake.closed is True


class TestPEFileResources:
    """资源枚举与查询。"""

    def test_file_info(self, pe: tuple[PEFile, FakePE, Path]) -> None:
        wrapper, _fake, path = pe
        info = wrapper.file_info()
        assert info.path == path
        assert info.machine == "x64（64 位）"
        assert info.subsystem == "Windows GUI"
        assert info.is_64bit is True
        assert info.resource_count == 4

    def test_file_info_unknown_machine(self, pe: tuple[PEFile, FakePE, Path]) -> None:
        wrapper, fake, _path = pe
        fake.FILE_HEADER.Machine = 0x1234
        fake.OPTIONAL_HEADER.Subsystem = 99
        info = wrapper.file_info()
        assert "Unknown (0x1234)" in info.machine
        assert "Unknown (99)" in info.subsystem

    def test_resources_sorted_and_cached(self, pe: tuple[PEFile, FakePE, Path]) -> None:
        wrapper, _fake, _path = pe
        first = wrapper.resources()
        second = wrapper.resources()
        assert [(item.type_id, item.name_id) for item in first] == [
            (3, 1),
            (3, 2),
            (16, 1),
            (24, 1),
        ]
        assert first == second

    def test_resources_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = _write_pe_file(tmp_path / "empty.exe")
        fake = FakePE([])
        del fake.DIRECTORY_ENTRY_RESOURCE
        monkeypatch.setattr(reshack, "_require_pefile", lambda: _fake_module(fake))
        wrapper = PEFile()
        wrapper.open(path)
        assert wrapper.resources() == []
        assert wrapper.resource_types() == {}

    def test_resources_skips_named_entries(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = _write_pe_file(tmp_path / "named.exe")
        fake = FakePE(RESOURCE_ENTRIES)
        # 命名资源（id 为 None）与缺失 directory 的项都应被跳过
        fake.DIRECTORY_ENTRY_RESOURCE.entries.append(SimpleNamespace(id=None, directory=None))
        fake.DIRECTORY_ENTRY_RESOURCE.entries.append(SimpleNamespace(id=99, directory=None))
        monkeypatch.setattr(reshack, "_require_pefile", lambda: _fake_module(fake))
        wrapper = PEFile()
        wrapper.open(path)
        assert len(wrapper.resources()) == 4

    def test_resources_skips_named_names_and_missing_data(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = _write_pe_file(tmp_path / "named2.exe")
        fake = FakePE(RESOURCE_ENTRIES)
        type_entry = fake.DIRECTORY_ENTRY_RESOURCE.entries[0]
        type_entry.directory.entries.append(SimpleNamespace(id=None, directory=None))
        name_entry = type_entry.directory.entries[0]
        name_entry.directory.entries.append(SimpleNamespace(data=None))
        name_entry.directory.entries.append(
            SimpleNamespace(
                data=SimpleNamespace(
                    lang=0,
                    struct=SimpleNamespace(OffsetToData=0x9999, Size=4),
                )
            )
        )
        monkeypatch.setattr(reshack, "_require_pefile", lambda: _fake_module(fake))
        wrapper = PEFile()
        wrapper.open(path)
        resources = wrapper.resources()
        dropped = next(item for item in resources if item.offset == 0x9999)
        assert dropped.data == b""

    def test_resource_types(self, pe: tuple[PEFile, FakePE, Path]) -> None:
        wrapper, _fake, _path = pe
        assert wrapper.resource_types() == {3: "Icon", 16: "Version", 24: "Manifest"}

    def test_get_resource(self, pe: tuple[PEFile, FakePE, Path]) -> None:
        wrapper, _fake, _path = pe
        assert wrapper.get_resource(16, 1, 1033) is not None
        assert wrapper.get_resource(16, 1, 0) is None

    def test_resources_tolerates_read_error(
        self, pe: tuple[PEFile, FakePE, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wrapper, fake, _path = pe

        def boom(_rva: int, _size: int) -> bytes:
            raise ValueError("bad rva")

        monkeypatch.setattr(fake, "get_data", boom)
        assert wrapper.resources()[0].data == b""

    def test_locate_struct_misses(self, pe: tuple[PEFile, FakePE, Path]) -> None:
        wrapper, _fake, _path = pe
        assert wrapper._locate_struct(99, 1, 0) is None
        assert wrapper._locate_struct(3, 99, 0) is None
        assert wrapper._locate_struct(3, 1, 999) is None


class TestPEFileExport:
    """资源导出。"""

    def test_export_resource(self, pe: tuple[PEFile, FakePE, Path], tmp_path: Path) -> None:
        wrapper, _fake, _path = pe
        output = tmp_path / "nested" / "icon.ico"
        resource = wrapper.export_resource(3, 1, output)
        assert resource is not None
        assert output.read_bytes() == b"icon-data"

    def test_export_missing(self, pe: tuple[PEFile, FakePE, Path], tmp_path: Path) -> None:
        wrapper, _fake, _path = pe
        assert wrapper.export_resource(3, 9, tmp_path / "x.ico") is None

    def test_export_write_error(self, pe: tuple[PEFile, FakePE, Path], tmp_path: Path) -> None:
        wrapper, _fake, _path = pe
        target = tmp_path / "dir"
        target.mkdir()
        with pytest.raises(PEError, match="写入资源文件失败"):
            wrapper.export_resource(3, 1, target)

    def test_export_all(self, pe: tuple[PEFile, FakePE, Path], tmp_path: Path) -> None:
        wrapper, _fake, _path = pe
        exported, total, directory = wrapper.export_all(tmp_path / "out")
        assert (exported, total) == (4, 4)
        assert (directory / "Icon" / "1_0.ico").read_bytes() == b"icon-data"
        assert (directory / "Version" / "1_1033.txt").read_bytes() == b"version-info"

    def test_export_all_skips_write_error(
        self, pe: tuple[PEFile, FakePE, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wrapper, _fake, _path = pe
        original = Path.write_bytes

        def flaky(self: Path, data: bytes) -> int:
            if self.name == "2_0.ico":
                raise OSError("locked")
            return original(self, data)

        monkeypatch.setattr(Path, "write_bytes", flaky)
        exported, total, _directory = wrapper.export_all(tmp_path / "out")
        assert (exported, total) == (3, 4)

    def test_export_all_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = _write_pe_file(tmp_path / "empty.exe")
        fake = FakePE([])
        monkeypatch.setattr(reshack, "_require_pefile", lambda: _fake_module(fake))
        wrapper = PEFile()
        wrapper.open(path)
        assert wrapper.export_all(tmp_path / "out") == (0, 0, tmp_path / "out")


class TestPEFileReplace:
    """资源替换与失败回滚。"""

    def test_replace_success(self, pe: tuple[PEFile, FakePE, Path], tmp_path: Path) -> None:
        wrapper, fake, path = pe
        original = path.read_bytes()
        old_size, new_size, backup = wrapper.replace_resource(3, 1, b"new")
        assert (old_size, new_size) == (9, 3)
        assert backup == tmp_path / "app.exe.backup"
        assert backup.read_bytes() == original
        assert fake.write_path == str(path)
        assert fake.writes == [(0x500, b"new" + b"\x00" * 6)]
        assert wrapper.resources() is not None

    def test_replace_missing(self, pe: tuple[PEFile, FakePE, Path]) -> None:
        wrapper, _fake, _path = pe
        with pytest.raises(PEError, match="未找到资源"):
            wrapper.replace_resource(3, 99, b"x")

    def test_replace_too_large(self, pe: tuple[PEFile, FakePE, Path]) -> None:
        wrapper, _fake, _path = pe
        with pytest.raises(PEError, match="无法安全扩容"):
            wrapper.replace_resource(3, 1, b"x" * 100)

    def test_replace_locate_failure(self, pe: tuple[PEFile, FakePE, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        wrapper, _fake, _path = pe
        monkeypatch.setattr(wrapper, "_locate_struct", lambda *_args: None)
        with pytest.raises(PEError, match="定位资源失败"):
            wrapper.replace_resource(3, 1, b"x")

    def test_replace_rva_failure(self, pe: tuple[PEFile, FakePE, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        wrapper, fake, _path = pe

        def boom(_rva: int) -> int:
            raise ValueError("bad rva")

        monkeypatch.setattr(fake, "get_offset_from_rva", boom)
        with pytest.raises(PEError, match="RVA 转换文件偏移失败"):
            wrapper.replace_resource(3, 1, b"x")

    def test_replace_write_returns_false(
        self, pe: tuple[PEFile, FakePE, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wrapper, fake, _path = pe
        monkeypatch.setattr(fake, "set_bytes_at_offset", lambda *_args: False)
        with pytest.raises(PEError, match="偏移超出文件范围"):
            wrapper.replace_resource(3, 1, b"x")

    def test_replace_write_raises(self, pe: tuple[PEFile, FakePE, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        wrapper, fake, _path = pe

        def boom(_offset: int, _data: bytes) -> bool:
            raise RuntimeError("write failed")

        monkeypatch.setattr(fake, "set_bytes_at_offset", boom)
        with pytest.raises(PEError, match="写入资源数据失败"):
            wrapper.replace_resource(3, 1, b"x")

    def test_replace_backup_failure(self, pe: tuple[PEFile, FakePE, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        wrapper, _fake, _path = pe

        def boom(*_args: Any, **_kwargs: Any) -> None:
            raise OSError("no space")

        monkeypatch.setattr(reshack.shutil, "copy2", boom)
        with pytest.raises(PEError, match="创建备份失败"):
            wrapper.replace_resource(3, 1, b"x")

    def test_replace_write_failure_restores_backup(
        self, pe: tuple[PEFile, FakePE, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wrapper, fake, path = pe
        original = path.read_bytes()

        def boom(_filename: str) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(fake, "write", boom)
        with pytest.raises(PEError, match="保存 PE 文件失败"):
            wrapper.replace_resource(3, 1, b"x")
        assert path.read_bytes() == original
        assert not (path.parent / "app.exe.backup").exists()


# ============================================================================ #
# CLI 端到端
# ============================================================================ #
class TestCli:
    """run_tool 端到端。"""

    def test_list(self, pe: tuple[PEFile, FakePE, Path], capsys: pytest.CaptureFixture[str]) -> None:
        _wrapper, _fake, path = pe
        code = run_tool("reshack", ["list", str(path)])
        output = capsys.readouterr().out
        assert code == 0
        assert "app.exe" in output
        assert "Icon" in output

    def test_list_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("reshack", ["list", str(tmp_path / "missing.exe")])
        assert code == 0
        assert "错误:" in capsys.readouterr().out

    def test_extract(self, pe: tuple[PEFile, FakePE, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _wrapper, _fake, path = pe
        output = tmp_path / "out" / "icon.ico"
        code = run_tool(
            "reshack",
            ["extract", str(path), "3", "1", "--output", str(output)],
        )
        assert code == 0
        assert output.read_bytes() == b"icon-data"
        assert "已导出" in capsys.readouterr().out

    def test_extract_default_output_name(
        self, pe: tuple[PEFile, FakePE, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _wrapper, _fake, path = pe
        monkeypatch.chdir(tmp_path)
        code = run_tool("reshack", ["extract", str(path), "3", "1"])
        assert code == 0
        assert (tmp_path / "app_resource_3_1_0.ico").read_bytes() == b"icon-data"

    def test_extract_missing(
        self, pe: tuple[PEFile, FakePE, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _wrapper, _fake, path = pe
        code = run_tool(
            "reshack",
            ["extract", str(path), "3", "9", "--output", str(tmp_path / "x.ico")],
        )
        assert code == 0
        assert "未找到资源" in capsys.readouterr().out

    def test_replace(self, pe: tuple[PEFile, FakePE, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _wrapper, fake, path = pe
        data_file = tmp_path / "new.bin"
        data_file.write_bytes(b"new")
        code = run_tool("reshack", ["replace", str(path), "3", "1", str(data_file)])
        assert code == 0
        output = capsys.readouterr().out
        assert "已替换资源" in output
        assert "原文件备份" in output
        assert fake.writes == [(0x500, b"new" + b"\x00" * 6)]

    def test_replace_read_error(
        self, pe: tuple[PEFile, FakePE, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _wrapper, _fake, path = pe
        code = run_tool("reshack", ["replace", str(path), "3", "1", str(tmp_path / "missing.bin")])
        assert code == 0
        assert "读取新资源数据失败" in capsys.readouterr().out

    def test_export_all(
        self, pe: tuple[PEFile, FakePE, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _wrapper, _fake, path = pe
        target = tmp_path / "all"
        code = run_tool("reshack", ["export-all", str(path), "--output-dir", str(target)])
        assert code == 0
        assert "已导出 4/4 个资源" in capsys.readouterr().out
        assert (target / "Icon" / "1_0.ico").exists()

    def test_export_all_default_dir(self, pe: tuple[PEFile, FakePE, Path], capsys: pytest.CaptureFixture[str]) -> None:
        _wrapper, _fake, path = pe
        code = run_tool("reshack", ["export-all", str(path)])
        assert code == 0
        assert (path.parent / "app_resources" / "Icon" / "1_0.ico").exists()
        assert "已导出" in capsys.readouterr().out

    def test_extract_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("reshack", ["extract", str(tmp_path / "missing.exe"), "3", "1"])
        assert code == 0
        assert "错误:" in capsys.readouterr().out

    def test_replace_error(
        self, pe: tuple[PEFile, FakePE, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _wrapper, _fake, path = pe
        data_file = tmp_path / "huge.bin"
        data_file.write_bytes(b"x" * 100)
        code = run_tool("reshack", ["replace", str(path), "3", "1", str(data_file)])
        assert code == 0
        assert "无法安全扩容" in capsys.readouterr().out

    def test_export_all_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("reshack", ["export-all", str(tmp_path / "missing.exe")])
        assert code == 0
        assert "错误:" in capsys.readouterr().out
