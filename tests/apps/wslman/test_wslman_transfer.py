"""wslman P3：``_transfer`` 基础设施、export / import / copy、压缩与备份系列。

外部命令全部注入假执行器；导出写入由 fallback 处理器模拟，测试只碰临时目录。
"""

from __future__ import annotations

import dataclasses
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.wslman import _transfer
from fcmd.apps.wslman import wslman as wslman_module
from fcmd.apps.wslman._config import WslmanConfig
from fcmd.apps.wslman._disk import VolumeInfo
from fcmd.apps.wslman._errors import WslmanError
from fcmd.apps.wslman._transfer import SpacePreflight
from fcmd.core.toolkit import _TOOL_REGISTRY, list_subcommands, run_tool
from tests.apps.wslman import _fakes

_TABLE = """\
  NAME            STATE           VERSION
* Ubuntu          Running         2
  Debian          Stopped         2
"""

_AFTER_IMPORT_TABLE = """\
  NAME            STATE           VERSION
* Ubuntu          Running         2
  Debian          Stopped         2
  Ubuntu2         Stopped         2
"""

_EMPTY_TABLE = """\
  NAME            STATE           VERSION
"""

IDEAL = 1024**3


def _env(
    runner: Any,
    *,
    tmp_path: Path,
    config: WslmanConfig | None = None,
) -> wslman_module.Environment:
    return wslman_module.Environment(
        runner=runner,
        capabilities=_fakes.capabilities(),
        registry=_fakes.snapshot(),
        config=config or WslmanConfig(path=tmp_path / "wslman.json"),
        platform_ok=True,
        lock_dir=tmp_path / "locks",
    )


def _use(monkeypatch: pytest.MonkeyPatch, env: wslman_module.Environment) -> None:
    monkeypatch.setattr(wslman_module, "_build_environment", lambda *args, **kwargs: env)


def _run(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str, str]:
    """返回 ``(退出码, stdout, stdout + stderr)``。

    人读错误由 ``fcmd.console.print_error`` 写到 stdout，``--json`` 错误写到 stderr；
    第三个值合并两路，便于断言用户实际能看到的那句话。
    """
    code = run_tool("wslman", argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.out + captured.err


def _list(text: str = _TABLE) -> Any:
    return _fakes.make_result(stdout=_fakes.utf16(text))


def _tar_writer(payload: bytes = b"tar-bytes", *, returncode: int = 0, error: BaseException | None = None) -> Any:
    """模拟 ``wsl --export``：把 payload 写到 argv 的最后一个路径。"""

    def handler(argv: tuple[str, ...]) -> Any:
        target = Path(argv[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        if error is not None:
            raise error
        return _fakes.make_result(returncode=returncode)

    return handler


def _write(path: Path, data: bytes = b"x") -> Path:
    path.write_bytes(data)
    return path


def _make_tar(path: Path, *, gz: bool = False) -> Path:
    mode = "w:gz" if gz else "w"
    with tarfile.open(path, mode) as archive:
        for name, data in (("a.txt", b"hello"), ("b.bin", b"0123456789")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


# ---------------------------------------------------------------------- #
# _transfer：预检、原子改名、锁、归档
# ---------------------------------------------------------------------- #


def test_part_path_and_sha(tmp_path: Path) -> None:
    target = tmp_path / "a.tar"
    assert _transfer.part_path(target) == tmp_path / "a.tar.part"
    payload = _write(tmp_path / "b.bin", b"hello")
    assert _transfer.sha256_file(payload) == hashlib.sha256(b"hello").hexdigest()


def test_atomic_replace_creates_parent(tmp_path: Path) -> None:
    part = _write(tmp_path / "a.part", b"body")
    final = tmp_path / "deep" / "dir" / "a.tar"
    _transfer.atomic_replace(part, final)
    assert final.read_bytes() == b"body"
    assert not part.exists()


def test_preflight_space_enough(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_transfer, "volume_info", lambda path: VolumeInfo(str(path), IDEAL, 2 * IDEAL, None))
    check = _transfer.preflight_space(tmp_path / "a.tar", 100 * 1024 * 1024, margin=1.5, min_free_after_mb=10)
    assert check.ok is True
    assert check.required_bytes == int(100 * 1024 * 1024 * 1.5)
    assert check.reserve_bytes == 10 * 1024 * 1024
    assert "空间不足" not in check.detail
    assert check.to_data()["ok"] is True


def test_preflight_space_insufficient(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_transfer, "volume_info", lambda path: VolumeInfo(str(path), 1024, 2048, None))
    check = _transfer.preflight_space(tmp_path / "a.tar", IDEAL)
    assert check.ok is False
    assert "空间不足" in check.detail


def test_preflight_unknown_size_uses_floor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_transfer, "volume_info", lambda path: VolumeInfo(str(path), IDEAL, 2 * IDEAL, None))
    check = _transfer.preflight_space(tmp_path / "a.tar", None)
    assert check.required_bytes == 2 * IDEAL
    assert check.estimated is True
    assert "估算" in check.detail


def test_preflight_unknown_free(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_transfer, "volume_info", lambda path: VolumeInfo(str(path), None, None, "拒绝访问"))
    check = _transfer.preflight_space(tmp_path / "a.tar", 1024)
    assert check.ok is False
    assert check.detail == "拒绝访问"


def test_volume_problem_rejects_relative_and_unc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_transfer, "_volume_filesystem", lambda path: "NTFS")
    assert "绝对路径" in (_transfer.volume_problem(Path("relative/a.tar")) or "")
    assert "UNC" in (_transfer.volume_problem(Path(r"\\server\share\a.tar")) or "")
    assert _transfer.volume_problem(tmp_path / "a.tar") is None


def test_volume_problem_rejects_fat32(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_transfer, "_volume_filesystem", lambda path: "FAT32")
    problem = _transfer.volume_problem(tmp_path / "a.tar")
    assert problem is not None
    assert "FAT32" in problem


def test_gzip_to_roundtrip(tmp_path: Path) -> None:
    source = _write(tmp_path / "a.tar", b"hello" * 1000)
    target = tmp_path / "a.tar.gz"
    size = _transfer.gzip_to(source, target, level=9)
    assert size == target.stat().st_size
    assert gzip.decompress(target.read_bytes()) == b"hello" * 1000


def test_progress_reporter_reports_and_stops(tmp_path: Path) -> None:
    target = _write(tmp_path / "a.part", b"x" * 2048)
    stream = io.StringIO()
    reporter = _transfer.ProgressReporter(target, 4096, label="导出", stream=stream, interval=0.01)
    reporter.start()
    reporter.stop()
    reporter.report()
    text = stream.getvalue()
    assert "[导出] 已写" in text
    assert "50%" in text

    unknown = _transfer.ProgressReporter(tmp_path / "missing.part", None, label="复制", stream=stream)
    unknown.report()
    assert stream.getvalue().count("[复制]") == 0


def test_transfer_lock_acquire_and_release(tmp_path: Path) -> None:
    lock = _transfer.TransferLock("Ubuntu", directory=tmp_path)
    with lock as handle:
        assert handle.path.exists()
        assert lock.path.exists()
    assert not lock.path.exists()


def test_transfer_lock_busy_and_stale(tmp_path: Path) -> None:
    lock = _transfer.TransferLock("Ubuntu", directory=tmp_path)
    with lock, pytest.raises(WslmanError) as excinfo, _transfer.TransferLock("Ubuntu", directory=tmp_path):
        pass
    assert excinfo.value.code == "RUNTIME_ERROR"
    assert "已有传输" in str(excinfo.value)
    assert "PID" in (excinfo.value.hint or "")

    stale = _transfer.TransferLock("Debian", directory=tmp_path, stale_s=0.0)
    stale.path.write_text('{"pid": 1}', encoding="utf-8")
    with stale:
        assert stale.path.exists()


def test_archive_info_plain_and_gzip(tmp_path: Path) -> None:
    plain = _make_tar(tmp_path / "a.tar")
    info = _transfer.archive_info(plain)
    assert info.members == 2
    assert info.total_bytes == 15
    assert info.compressed is False
    assert info.sha256 == _transfer.sha256_file(plain)
    assert info.to_data()["members"] == 2

    packed = _make_tar(tmp_path / "a.tar.gz", gz=True)
    gz_info = _transfer.archive_info(packed)
    assert gz_info.compressed is True
    assert gz_info.members == 2


def test_archive_info_invalid_and_missing(tmp_path: Path) -> None:
    broken = _write(tmp_path / "broken.tar", b"not a tar")
    with pytest.raises(WslmanError) as excinfo:
        _transfer.archive_info(broken)
    assert excinfo.value.code == "CHECK_FAILED"

    with pytest.raises(WslmanError) as missing:
        _transfer.archive_info(tmp_path / "missing.tar")
    assert missing.value.code == "NOT_FOUND"


def test_backup_files_listing(tmp_path: Path) -> None:
    _make_tar(tmp_path / "a.tar")
    _make_tar(tmp_path / "b.tar.gz", gz=True)
    _make_tar(tmp_path / "c.tgz", gz=True)
    _write(tmp_path / "notes.txt")
    (tmp_path / "sub").mkdir()
    files = _transfer.backup_files(tmp_path)
    assert {item.path.name for item in files} == {"a.tar", "b.tar.gz", "c.tgz"}
    assert {item.compressed for item in files} == {True, False}
    assert files[0].size_bytes > 0

    with pytest.raises(WslmanError) as excinfo:
        _transfer.backup_files(tmp_path / "missing")
    assert excinfo.value.code == "NOT_FOUND"


# ---------------------------------------------------------------------- #
# export
# ---------------------------------------------------------------------- #


def test_export_preview_has_no_side_effects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "Ubuntu.tar"
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["export", "Ubuntu", str(target), "--preview", "--allow-running"])

    assert code == 0
    assert "--export" in out
    assert "[预览]" in out
    assert not target.exists()
    assert not _transfer.part_path(target).exists()
    assert not (tmp_path / "locks").exists()
    assert [call[1:] for call in runner.calls] == [("--list", "--verbose")]


def test_export_running_requires_explicit_choice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "Ubuntu.tar"
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, err = _run(capsys, ["export", "Ubuntu", str(target)])

    assert code == 3
    text = out + err
    assert "--terminate-first" in text
    assert "--allow-running" in text
    assert not target.exists()
    assert [call[1:] for call in runner.calls] == [("--list", "--verbose")]


def test_export_rejects_both_running_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(
        capsys,
        ["export", "Ubuntu", str(tmp_path / "a.tar"), "--terminate-first", "--allow-running"],
    )

    assert code == 3
    assert "二选一" in err


def test_export_terminate_first_then_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "Ubuntu.tar"
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _list(),
            ("--terminate", "Ubuntu"): _fakes.make_result(),
        },
        fallback=_tar_writer(b"hello"),
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["export", "Ubuntu", str(target), "--terminate-first"])

    assert code == 0
    assert "导出完成" in out
    assert target.read_bytes() == b"hello"
    assert not _transfer.part_path(target).exists()
    assert [call[1:3] for call in runner.calls] == [
        ("--list", "--verbose"),
        ("--terminate", "Ubuntu"),
        ("--export", "Ubuntu"),
    ]


def test_export_allow_running_skips_terminate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "Ubuntu.tar"
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()}, fallback=_tar_writer())
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code = run_tool("wslman", ["export", "Ubuntu", str(target), "--allow-running", "--json"])

    assert code == 0
    assert ("--terminate", "Ubuntu") not in [call[1:] for call in runner.calls]


def test_export_existing_file_requires_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _write(tmp_path / "Ubuntu.tar", b"old")
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()}, fallback=_tar_writer(b"new"))
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["export", "Ubuntu", str(target), "--allow-running"])
    assert code == 3
    assert "已存在" in err
    assert target.read_bytes() == b"old"

    code = run_tool("wslman", ["export", "Ubuntu", str(target), "--allow-running", "--overwrite"])
    assert code == 0
    assert target.read_bytes() == b"new"


def test_export_failure_cleans_part(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "Ubuntu.tar"
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()}, fallback=_tar_writer(returncode=2))
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["export", "Ubuntu", str(target), "--allow-running"])

    assert code == 1
    assert "失败" in err
    assert not _transfer.part_path(target).exists()
    assert not target.exists()


def test_export_keep_partial(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "Ubuntu.tar"
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()}, fallback=_tar_writer(returncode=2))
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code = run_tool("wslman", ["export", "Ubuntu", str(target), "--allow-running", "--keep-partial"])

    assert code == 1
    assert _transfer.part_path(target).exists()
    assert not target.exists()


def test_export_interrupt_cleans_part_and_returns_130(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "Ubuntu.tar"
    runner = _fakes.FakeRunner(
        {("--list", "--verbose"): _list()},
        fallback=_tar_writer(error=KeyboardInterrupt()),
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code = run_tool("wslman", ["export", "Ubuntu", str(target), "--allow-running"])

    assert code == 130
    assert not _transfer.part_path(target).exists()
    assert not target.exists()


def test_export_interrupt_keep_partial(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "Ubuntu.tar"
    runner = _fakes.FakeRunner(
        {("--list", "--verbose"): _list()},
        fallback=_tar_writer(error=KeyboardInterrupt()),
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code = run_tool("wslman", ["export", "Ubuntu", str(target), "--allow-running", "--keep-partial", "--json"])

    assert code == 130
    assert _transfer.part_path(target).exists()


def test_export_gzip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "Ubuntu.tar"
    runner = _fakes.FakeRunner(
        {("--list", "--verbose"): _list()},
        fallback=_tar_writer(b"payload" * 100),
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code = run_tool(
        "wslman",
        ["export", "Ubuntu", str(target), "--allow-running", "--compress", "gzip", "--level", "9", "--json"],
    )

    assert code == 0
    final = tmp_path / "Ubuntu.tar.gz"
    assert final.exists()
    assert gzip.decompress(final.read_bytes()) == b"payload" * 100
    assert not _transfer.part_path(final).exists()


def test_export_removes_stale_part(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "Ubuntu.tar"
    part = _transfer.part_path(target)
    part.write_bytes(b"stale")
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()}, fallback=_tar_writer(b"fresh"))
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code = run_tool("wslman", ["export", "Ubuntu", str(target), "--allow-running"])

    assert code == 0
    assert target.read_bytes() == b"fresh"


def test_export_preflight_failure_blocks_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "Ubuntu.tar"
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    def fake_preflight(*args: Any, **kwargs: Any) -> SpacePreflight:
        return SpacePreflight(
            target=target,
            free_bytes=1,
            required_bytes=2,
            estimated=False,
            reserve_bytes=0,
            ok=False,
            detail="空间不足（测试）",
        )

    monkeypatch.setattr(wslman_module, "preflight_space", fake_preflight)

    code, _out, err = _run(capsys, ["export", "Ubuntu", str(target), "--allow-running"])

    assert code == 1
    assert "空间不足" in err
    assert ("--export", "Ubuntu") not in [call[1:3] for call in runner.calls]
    assert not target.exists()


def test_export_json_contains_commands_and_hash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "Ubuntu.tar"
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()}, fallback=_tar_writer(b"abc"))
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["export", "Ubuntu", str(target), "--allow-running", "--json"])

    assert code == 0
    data = json.loads(out)["data"]
    assert data["schema"] == "wslman/export/1"
    assert data["sha256"] == hashlib.sha256(b"abc").hexdigest()
    assert data["commands"] == [[_fakes.WSL, "--export", "Ubuntu", str(_transfer.part_path(target))]]
    assert data["preflight"]["ok"] is True
    assert data["artifacts"][0]["kind"] == "backup"


# ---------------------------------------------------------------------- #
# import
# ---------------------------------------------------------------------- #


def test_import_preview_has_no_side_effects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _write(tmp_path / "Ubuntu.tar", b"tar")
    install = tmp_path / "inst"
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list(_EMPTY_TABLE)})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["import", "Ubuntu", str(archive), "--dir", str(install), "--preview"])

    assert code == 0
    assert "--import" in out
    assert not install.exists()
    assert not (tmp_path / "locks").exists()
    assert [call[1:] for call in runner.calls] == [("--list", "--verbose")]


def test_import_requires_dir_and_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["import", "Ubuntu", str(tmp_path / "missing.tar")])
    assert code == 3
    assert "--dir" in err

    code, _out, err = _run(capsys, ["import", "Ubuntu", str(tmp_path / "missing.tar"), "--dir", str(tmp_path / "inst")])
    assert code == 4
    assert "不存在" in err


def test_import_rejects_non_empty_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _write(tmp_path / "a.tar", b"tar")
    install = tmp_path / "inst"
    install.mkdir()
    _write(install / "occupied.txt")
    runner = _fakes.FakeRunner({})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["import", "Ubuntu", str(archive), "--dir", str(install)])

    assert code == 3
    assert "必须为空" in err


def test_import_name_conflict_needs_replace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _write(tmp_path / "a.tar", b"tar")
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["import", "Ubuntu", str(archive), "--dir", str(tmp_path / "inst")])

    assert code == 3
    assert "已存在" in err


def test_import_replace_needs_yes_and_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _write(tmp_path / "a.tar", b"tar")
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))
    base = ["import", "Ubuntu", str(archive), "--dir", str(tmp_path / "inst"), "--replace"]

    code, _out, err = _run(capsys, base)
    assert code == 2
    assert "--yes" in err

    code, _out, err = _run(capsys, [*base, "--yes"])
    assert code == 2
    assert "--backup" in err


def test_import_replace_success_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _write(tmp_path / "a.tar", b"tar")
    backup = tmp_path / "old.tar"
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): [_list(), _list(_AFTER_IMPORT_TABLE)],
            ("--terminate", "Ubuntu"): _fakes.make_result(),
            ("--unregister", "Ubuntu"): _fakes.make_result(),
            ("--import", "Ubuntu", str(tmp_path / "inst"), str(archive)): _fakes.make_result(),
        },
        fallback=_tar_writer(b"backup"),
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(
        capsys,
        [
            "import",
            "Ubuntu",
            str(archive),
            "--dir",
            str(tmp_path / "inst"),
            "--replace",
            "--yes",
            "--backup",
            str(backup),
            "--terminate-first",
        ],
    )

    assert code == 0
    assert "导入完成" in out
    assert backup.read_bytes() == b"backup"
    assert (tmp_path / "inst").is_dir()
    assert [call[1] for call in runner.calls] == [
        "--list",
        "--terminate",
        "--export",
        "--unregister",
        "--import",
        "--list",
    ]


def test_import_backup_failure_aborts_before_unregister(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _write(tmp_path / "a.tar", b"tar")
    backup = tmp_path / "old.tar"
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _list(),
            ("--terminate", "Ubuntu"): _fakes.make_result(),
        },
        fallback=_tar_writer(returncode=2),
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(
        capsys,
        [
            "import",
            "Ubuntu",
            str(archive),
            "--dir",
            str(tmp_path / "inst"),
            "--replace",
            "--yes",
            "--backup",
            str(backup),
            "--terminate-first",
        ],
    )

    assert code == 1
    assert "备份" in err
    assert "--unregister" not in [call[1] for call in runner.calls]
    assert not (tmp_path / "inst").exists()


def test_import_failure_reports_cleanup_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _write(tmp_path / "a.tar", b"tar")
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _list(_EMPTY_TABLE),
            ("--import", "Ubuntu", str(tmp_path / "inst"), str(archive)): _fakes.make_result(
                returncode=1, stderr=b"disk full"
            ),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["import", "Ubuntu", str(archive), "--dir", str(tmp_path / "inst")])

    assert code == 1
    assert "disk full" in err
    assert "wsl --unregister" in err


def test_import_replace_running_without_terminate_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _write(tmp_path / "a.tar", b"tar")
    backup = tmp_path / "old.tar"
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(
        capsys,
        [
            "import",
            "Ubuntu",
            str(archive),
            "--dir",
            str(tmp_path / "inst"),
            "--replace",
            "--yes",
            "--backup",
            str(backup),
        ],
    )

    assert code == 3
    assert "--terminate-first" in err


# ---------------------------------------------------------------------- #
# copy
# ---------------------------------------------------------------------- #


def test_copy_preview_has_no_side_effects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install = tmp_path / "inst"
    temp = tmp_path / "temp" / "copy.tar"
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(
        capsys,
        [
            "copy",
            "Ubuntu",
            "Ubuntu2",
            "--dir",
            str(install),
            "--temp",
            str(temp),
            "--terminate-first",
            "--preview",
        ],
    )

    assert code == 0
    assert "--import" in out
    assert not install.exists()
    assert not temp.exists()
    assert not (tmp_path / "locks").exists()


def test_copy_running_source_needs_terminate_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["copy", "Ubuntu", "Ubuntu2", "--dir", str(tmp_path / "inst")])

    assert code == 3
    assert "--terminate-first" in err


def test_copy_success_cleans_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    temp = tmp_path / "copy.tar"
    install = tmp_path / "inst"
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): [_list(), _list(_AFTER_IMPORT_TABLE)],
            ("--terminate", "Ubuntu"): _fakes.make_result(),
            ("--import", "Ubuntu2", str(install), str(temp), "--version", "2"): _fakes.make_result(),
        },
        fallback=_tar_writer(b"tar"),
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(
        capsys,
        ["copy", "Ubuntu", "Ubuntu2", "--dir", str(install), "--temp", str(temp), "--terminate-first"],
    )

    assert code == 0
    assert "复制完成" in out
    assert not temp.exists()
    assert not _transfer.part_path(temp).exists()
    assert [call[1] for call in runner.calls] == [
        "--list",
        "--terminate",
        "--export",
        "--import",
        "--list",
    ]


def test_copy_keep_temp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    temp = tmp_path / "copy.tar"
    install = tmp_path / "inst"
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): [_list(), _list(_AFTER_IMPORT_TABLE)],
            ("--terminate", "Ubuntu"): _fakes.make_result(),
            ("--import", "Ubuntu2", str(install), str(temp), "--version", "2"): _fakes.make_result(),
        },
        fallback=_tar_writer(b"tar"),
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code = run_tool(
        "wslman",
        [
            "copy",
            "Ubuntu",
            "Ubuntu2",
            "--dir",
            str(install),
            "--temp",
            str(temp),
            "--terminate-first",
            "--keep-temp",
        ],
    )

    assert code == 0
    assert temp.exists()


def test_copy_rejects_same_name_and_existing_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["copy", "Ubuntu", "ubuntu", "--dir", str(tmp_path / "inst")])
    assert code == 3
    assert "不能相同" in err

    code, _out, err = _run(capsys, ["copy", "Ubuntu", "Debian", "--dir", str(tmp_path / "inst"), "--terminate-first"])
    assert code == 3
    assert "已存在" in err


def test_copy_export_failure_cleans_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    temp = tmp_path / "copy.tar"
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _list(),
            ("--terminate", "Ubuntu"): _fakes.make_result(),
        },
        fallback=_tar_writer(returncode=2),
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(
        capsys,
        ["copy", "Ubuntu", "Ubuntu2", "--dir", str(tmp_path / "inst"), "--temp", str(temp), "--terminate-first"],
    )

    assert code == 1
    assert "导出" in err
    assert not temp.exists()
    assert not _transfer.part_path(temp).exists()


# ---------------------------------------------------------------------- #
# compress / backups / backup-verify / backup-remove
# ---------------------------------------------------------------------- #


def _compress_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path))


def test_compress_preview_and_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _write(tmp_path / "a.tar", b"hello" * 1000)
    _compress_env(monkeypatch, tmp_path)

    code, out, _err = _run(capsys, ["compress", str(source), "--preview"])
    assert code == 0
    assert "[预览]" in out
    assert not (tmp_path / "a.tar.gz").exists()

    code, out, _err = _run(capsys, ["compress", str(source)])
    target = tmp_path / "a.tar.gz"
    assert code == 0
    assert "压缩完成" in out
    assert gzip.decompress(target.read_bytes()) == b"hello" * 1000
    assert not _transfer.part_path(target).exists()
    assert source.exists()


def test_compress_output_exists_and_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _write(tmp_path / "a.tar", b"hello")
    target = _write(tmp_path / "a.tar.gz", b"old")
    _compress_env(monkeypatch, tmp_path)

    code, _out, err = _run(capsys, ["compress", str(source)])
    assert code == 3
    assert "已存在" in err
    assert target.read_bytes() == b"old"

    code = run_tool("wslman", ["compress", str(source), "--overwrite"])
    assert code == 0
    assert gzip.decompress(target.read_bytes()) == b"hello"


def test_compress_remove_source_requires_yes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _write(tmp_path / "a.tar", b"hello")
    _compress_env(monkeypatch, tmp_path)

    code, _out, err = _run(capsys, ["compress", str(source), "--remove-source"])
    assert code == 2
    assert "--yes" in err
    assert source.exists()

    code = run_tool("wslman", ["compress", str(source), "--remove-source", "--yes"])
    assert code == 0
    assert not source.exists()


def test_compress_rejects_missing_and_gzip_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _compress_env(monkeypatch, tmp_path)

    code, _out, err = _run(capsys, ["compress", str(tmp_path / "missing.tar")])
    assert code == 4

    packed = _write(tmp_path / "a.tar.gz", b"x")
    code, _out, err = _run(capsys, ["compress", str(packed)])
    assert code == 3
    assert "已是 gzip" in err

    source = _write(tmp_path / "b.tar", b"x")
    code, _out, err = _run(capsys, ["compress", str(source), "--output", str(source)])
    assert code == 3


def test_backups_lists_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_tar(tmp_path / "a.tar")
    _make_tar(tmp_path / "b.tar.gz", gz=True)
    _write(tmp_path / "note.txt")
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["backups", "--dir", str(tmp_path)])

    assert code == 0
    assert "a.tar" in out
    assert "b.tar.gz" in out
    assert "note.txt" not in out


def test_backups_dir_required_when_config_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["backups"])

    assert code == 3
    assert "未指定备份目录" in err


def test_backups_uses_config_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "store"
    folder.mkdir()
    _make_tar(folder / "a.tar")
    config = WslmanConfig(path=tmp_path / "wslman.json", backups_dir=str(folder))
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path, config=config))

    code, out, _err = _run(capsys, ["backups"])

    assert code == 0
    assert "a.tar" in out


def test_backups_missing_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["backups", "--dir", str(tmp_path / "missing")])

    assert code == 4
    assert "不存在" in err


def test_backup_verify_plain_and_gzip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = _make_tar(tmp_path / "a.tar")
    packed = _make_tar(tmp_path / "b.tar.gz", gz=True)
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["backup-verify", str(plain)])
    assert code == 0
    assert "备份完整" in out
    assert "条目: 2" in out

    code, out, _err = _run(capsys, ["backup-verify", str(packed), "--json"])
    assert code == 0
    data = json.loads(out)["data"]
    assert data["compressed"] is True
    assert data["members"] == 2
    assert data["sha256"] == _transfer.sha256_file(packed)


def test_backup_verify_invalid_and_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = _write(tmp_path / "broken.tar", b"not a tar")
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["backup-verify", str(broken)])
    assert code == 5
    assert "无法解析" in err

    code, _out, err = _run(capsys, ["backup-verify", str(tmp_path / "missing.tar")])
    assert code == 4


def test_backup_remove_preview_yes_and_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _make_tar(tmp_path / "a.tar")
    runner = _fakes.FakeRunner({})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["backup-remove", str(target), "--preview"])
    assert code == 0
    assert "[预览]" in out
    assert target.exists()
    assert runner.calls == []

    code, _out, err = _run(capsys, ["backup-remove", str(target)])
    assert code == 2
    assert "--yes" in err
    assert target.exists()

    code, out, _err = _run(capsys, ["backup-remove", str(target), "--yes"])
    assert code == 0
    assert "已删除" in out
    assert not target.exists()

    code, _out, err = _run(capsys, ["backup-remove", str(target), "--yes"])
    assert code == 4


def test_backup_remove_unlink_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _make_tar(tmp_path / "a.tar")
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path))
    real_unlink = Path.unlink

    def fake_unlink(self: Path, *args: Any, **kwargs: Any) -> None:
        if self == target:
            raise OSError("文件被占用")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    code, _out, err = _run(capsys, ["backup-remove", str(target), "--yes"])

    assert code == 1
    assert "删除备份失败" in err
    assert target.exists()


def test_backups_json_and_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_tar(tmp_path / "a.tar")
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["backups", "--dir", str(tmp_path), "--json"])
    assert code == 0
    data = json.loads(out)["data"]
    assert data["schema"] == "wslman/backups/1"
    assert data["count"] == 1

    empty = tmp_path / "empty"
    empty.mkdir()
    code, out, _err = _run(capsys, ["backups", "--dir", str(empty)])
    assert code == 0
    assert "没有 tar" in out


def test_compress_stage_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _write(tmp_path / "a.tar", b"hello")
    _compress_env(monkeypatch, tmp_path)

    code, _out, err = _run(capsys, ["compress", str(source), "--level", "0"])
    assert code == 3
    assert "--level" in err

    monkeypatch.setattr(wslman_module, "volume_problem", lambda path: "目标卷文件系统为 FAT32，不支持大文件")
    code, _out, err = _run(capsys, ["compress", str(source)])
    assert code == 3
    assert "FAT32" in err
    monkeypatch.setattr(wslman_module, "volume_problem", _transfer.volume_problem)

    folder = tmp_path / "out.gz"
    folder.mkdir()
    code, _out, err = _run(capsys, ["compress", str(source), "--output", str(folder)])
    assert code == 3
    assert "目录" in err


def test_compress_preflight_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _write(tmp_path / "a.tar", b"hello")
    _compress_env(monkeypatch, tmp_path)

    def fake_preflight(*args: Any, **kwargs: Any) -> SpacePreflight:
        return SpacePreflight(
            target=tmp_path / "a.tar.gz",
            free_bytes=1,
            required_bytes=2,
            estimated=False,
            reserve_bytes=0,
            ok=False,
            detail="空间不足（测试）",
        )

    monkeypatch.setattr(wslman_module, "preflight_space", fake_preflight)

    code, _out, err = _run(capsys, ["compress", str(source)])

    assert code == 1
    assert "空间不足" in err
    assert not (tmp_path / "a.tar.gz").exists()


def test_compress_interrupt_and_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _write(tmp_path / "a.tar", b"hello")
    _compress_env(monkeypatch, tmp_path)

    def interrupt(*args: Any, **kwargs: Any) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(wslman_module, "gzip_to", interrupt)
    code, _out, _err = _run(capsys, ["compress", str(source)])
    assert code == 130
    assert not _transfer.part_path(tmp_path / "a.tar.gz").exists()

    def fail(*args: Any, **kwargs: Any) -> int:
        raise OSError("磁盘错误")

    monkeypatch.setattr(wslman_module, "gzip_to", fail)
    code, _out, err = _run(capsys, ["compress", str(source)])
    assert code == 1
    assert "磁盘错误" in err


def test_export_stage_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))
    target = tmp_path / "a.tar"

    code, _out, err = _run(capsys, ["export", "Ubuntu", str(target), "--allow-running", "--level", "0"])
    assert code == 3
    assert "--level" in err

    monkeypatch.setattr(wslman_module, "volume_problem", lambda path: "目标卷文件系统为 FAT32，不支持大文件")
    code, _out, err = _run(capsys, ["export", "Ubuntu", str(target), "--allow-running"])
    assert code == 3
    assert "FAT32" in err
    monkeypatch.setattr(wslman_module, "volume_problem", _transfer.volume_problem)

    folder = tmp_path / "dir.tar"
    folder.mkdir()
    code, _out, err = _run(capsys, ["export", "Ubuntu", str(folder), "--allow-running"])
    assert code == 3
    assert "目录" in err


def test_export_success_without_file_and_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "a.tar"
    runner = _fakes.FakeRunner(
        {("--list", "--verbose"): _list()},
        fallback=lambda argv: _fakes.make_result(),
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["export", "Ubuntu", str(target), "--allow-running"])
    assert code == 1
    assert "未生成文件" in err

    writer = _fakes.FakeRunner({("--list", "--verbose"): _list()}, fallback=_tar_writer(b"abc"))
    _use(monkeypatch, _env(writer, tmp_path=tmp_path))

    def fail(path: Path, *args: Any, **kwargs: Any) -> str:
        raise OSError("读取失败")

    monkeypatch.setattr(wslman_module, "sha256_file", fail)
    code, _out, err = _run(capsys, ["export", "Ubuntu", str(target), "--allow-running"])
    assert code == 1
    assert "导出文件操作失败" in err


def test_import_interrupt_and_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _write(tmp_path / "a.tar", b"tar")
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list(_EMPTY_TABLE)})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))
    args = ["import", "Ubuntu", str(archive), "--dir", str(tmp_path / "inst")]

    def interrupt(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(wslman_module, "_run_step", interrupt)
    code, _out, _err = _run(capsys, args)
    assert code == 130

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise OSError("磁盘错误")

    monkeypatch.setattr(wslman_module, "_run_step", fail)
    code, _out, err = _run(capsys, args)
    assert code == 1
    assert "磁盘错误" in err


def test_copy_source_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, err = _run(capsys, ["copy", "openSUSE", "NewName", "--dir", str(tmp_path / "inst")])

    assert code == 4
    assert "未找到发行版" in err


def test_copy_replace_success_and_unverified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    temp = tmp_path / "copy.tar"
    install = tmp_path / "inst"
    backup = tmp_path / "debian.tar"
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): [_list(), _list(_EMPTY_TABLE)],
            ("--terminate", "Ubuntu"): _fakes.make_result(),
            ("--terminate", "Debian"): _fakes.make_result(),
            ("--unregister", "Debian"): _fakes.make_result(),
            ("--import", "Debian", str(install), str(temp), "--version", "2"): _fakes.make_result(),
        },
        fallback=_tar_writer(b"payload"),
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(
        capsys,
        [
            "copy",
            "Ubuntu",
            "Debian",
            "--dir",
            str(install),
            "--temp",
            str(temp),
            "--terminate-first",
            "--replace",
            "--yes",
            "--backup",
            str(backup),
        ],
    )

    assert code == 0
    assert "复制完成" in out
    assert "尚未" in out
    assert backup.read_bytes() == b"payload"
    assert [call[1] for call in runner.calls] == [
        "--list",
        "--terminate",
        "--export",
        "--terminate",
        "--export",
        "--unregister",
        "--import",
        "--list",
    ]


def test_copy_interrupt_and_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    temp = tmp_path / "copy.tar"
    install = tmp_path / "inst"
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()}, fallback=_tar_writer(b"tar"))
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))
    args = [
        "copy",
        "Ubuntu",
        "Ubuntu2",
        "--dir",
        str(install),
        "--temp",
        str(temp),
        "--terminate-first",
    ]

    def interrupt(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(wslman_module, "_run_step", interrupt)
    code, _out, _err = _run(capsys, args)
    assert code == 130
    assert not _transfer.part_path(temp).exists()

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise OSError("磁盘错误")

    monkeypatch.setattr(wslman_module, "_run_step", fail)
    code, _out, err = _run(capsys, args)
    assert code == 1
    assert "磁盘错误" in err


def test_transfer_helpers_edge_cases(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path / "home"))
    assert _transfer.default_lock_dir() == tmp_path / "home" / "locks"

    present = _write(tmp_path / "a.part", b"x")
    stream = io.StringIO()
    reporter = _transfer.ProgressReporter(present, None, label="复制", stream=stream)
    reporter.report()
    assert "[复制] 已写" in stream.getvalue()

    lock = _transfer.TransferLock("Ubuntu", directory=tmp_path)
    assert lock._stale() is False

    lock.path.write_text("{broken", encoding="utf-8")
    with pytest.raises(WslmanError) as excinfo, lock:
        pass
    assert "PID" not in (excinfo.value.hint or "")
    assert str(lock.path) in (excinfo.value.hint or "")

    monkeypatch.setattr(_transfer.os, "name", "posix")
    assert _transfer._volume_filesystem(tmp_path / "a.tar") is None


def test_doctor_prints_config_warnings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = WslmanConfig(path=tmp_path / "wslman.json", warnings=("测试警告",))
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path, config=config))
    monkeypatch.setattr(wslman_module, "_wslconfig_check", lambda: wslman_module._Check("配置", "ok", "ok"))
    monkeypatch.setattr(wslman_module, "recommended_backup_dir", lambda: tmp_path / "wsl-backups")

    code, out, _err = _run(capsys, ["doctor"])

    assert code in {0, 5}
    assert "[配置警告] 测试警告" in out


def test_doctor_check_branches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env = _env(_fakes.FakeRunner({}), tmp_path=tmp_path)
    missing_version = dataclasses.replace(env, capabilities=_fakes.capabilities(version=""))
    assert wslman_module._version_check(missing_version).status == "warn"
    no_wsl = dataclasses.replace(env, capabilities=_fakes.capabilities(version="", wsl_path=None))
    assert wslman_module._version_check(no_wsl).status == "warn"

    settings = _write(tmp_path / ".wslconfig", b"[wsl2]\nmemory=4GB\n")
    monkeypatch.setattr(wslman_module, "wslconfig_default_path", lambda: settings)
    assert wslman_module._wslconfig_check().status == "ok"


def test_registers_p3_subcommands() -> None:
    expected = {
        "backup-remove",
        "backup-verify",
        "backups",
        "compress",
        "config",
        "copy",
        "doctor",
        "export",
        "import",
        "info",
        "list",
        "open",
        "set-default",
        "status",
        "terminate",
    }
    assert set(_TOOL_REGISTRY["wslman"]) == expected
    assert set(list_subcommands("wslman")) == expected
