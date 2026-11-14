"""llmengine 独立命令测试。"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path
from typing import Any, cast

import pytest

from fcmd.apps.locllm import llmengine, llmenv
from fcmd.apps.locllm.llmbase import Check, CommandResult, EngineInfo, ServeError
from fcmd.core.toolkit import _TOOL_REGISTRY


def test_tool_registered() -> None:
    assert set(_TOOL_REGISTRY["llmengine"]) == {"list", "install", "use", "check", "inspect"}


def _make_zip(path: Path, entries: dict[str, bytes]) -> Path:
    """写入一个 zip 离线包并返回路径。"""
    with zipfile.ZipFile(path, "w") as bundle:
        for name, data in entries.items():
            bundle.writestr(name, data)
    return path


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    """构造 zip 字节流。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in entries.items():
            bundle.writestr(name, data)
    return buffer.getvalue()


def test_list_without_engine(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmengine, "list_engines", lambda: [])
    llmengine.list_cmd()
    assert "未安装引擎" in capsys.readouterr().out


def test_list_marks_current(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        llmengine,
        "list_engines",
        lambda: [EngineInfo("b1", Path("llama-server.exe"), "current")],
    )
    llmengine.list_cmd()
    out = capsys.readouterr().out
    assert "b1" in out
    assert "llama-server.exe" in out


def test_install_sets_first_engine_as_current(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    used: list[str] = []

    def _install(tag: str, **kwargs: Any) -> EngineInfo:
        return EngineInfo(tag, Path("llama-server.exe"), "release")

    monkeypatch.setattr(llmengine, "install_engine", _install)
    monkeypatch.setattr(llmengine, "list_engines", lambda: [])
    monkeypatch.setattr(llmengine, "use_engine", lambda tag: used.append(tag) or EngineInfo(tag, Path("x")))
    llmengine.install_cmd("b1")
    assert used == ["b1"]
    assert "引擎就绪" in capsys.readouterr().out


def test_install_keeps_existing_current(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    used: list[str] = []

    def _install(tag: str, **kwargs: Any) -> EngineInfo:
        return EngineInfo(tag, Path("llama-server.exe"), "release")

    monkeypatch.setattr(llmengine, "install_engine", _install)
    monkeypatch.setattr(
        llmengine,
        "list_engines",
        lambda: [EngineInfo("b0", Path("old.exe"), "current")],
    )
    monkeypatch.setattr(llmengine, "use_engine", used.append)
    llmengine.install_cmd("b1")
    assert used == []
    assert "引擎就绪" in capsys.readouterr().out


def test_use_switches_default(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmengine, "use_engine", lambda tag: EngineInfo(tag, Path("llama-server.exe")))
    llmengine.use_cmd("b2")
    assert "默认引擎" in capsys.readouterr().out


def test_check_reports_result(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmengine, "resolve_engine", lambda tag=None: EngineInfo("b1", Path("llama-server.exe")))
    monkeypatch.setattr(
        llmengine,
        "check_engine",
        lambda engine: Check("推理引擎", "ok", "version ok"),
    )
    llmengine.check_cmd()
    assert "version ok" in capsys.readouterr().out


class TestInspectZip:
    """zip 离线包检查。"""

    def test_engine_package(self, tmp_path: Path) -> None:
        exe = llmengine.engine_exe_name()
        quant = llmengine.engine_exe_name("llama-quantize")
        archive = _make_zip(
            tmp_path / "llama-b12345-bin-win-cuda-13.5-x64.zip",
            {f"bin/{exe}": b"exe", f"bin/{quant}": b"quant"},
        )
        info = llmengine.inspect_zip(archive)
        assert info.kind == "engine"
        assert info.usable is True
        assert info.tag == "b12345"
        assert info.variant == "cuda"
        assert info.server == f"bin/{exe}"
        assert info.quantize == f"bin/{quant}"
        assert info.entries == 2
        assert info.total_size == 8
        assert info.problems == ()
        assert info.notes == ()

    def test_marker_tag_overrides_name(self, tmp_path: Path) -> None:
        exe = llmengine.engine_exe_name()
        archive = _make_zip(
            tmp_path / "custom.zip",
            {"bundle/engine.json": json.dumps({"tag": "b999"}).encode(), f"bundle/{exe}": b"exe"},
        )
        info = llmengine.inspect_zip(archive)
        assert info.tag == "b999"

    def test_cudart_package(self, tmp_path: Path) -> None:
        archive = _make_zip(
            tmp_path / "cudart-llama-bin-win-cuda-13.5-x64.zip",
            {"cudart64_13.dll": b"dll"},
        )
        info = llmengine.inspect_zip(archive)
        assert info.kind == "cudart"
        assert info.usable is False
        assert info.variant == "cuda"
        assert info.notes

    def test_source_package(self, tmp_path: Path) -> None:
        archive = _make_zip(tmp_path / "llama.cpp-b9.zip", {"convert_hf_to_gguf.py": b"# script"})
        info = llmengine.inspect_zip(archive)
        assert info.kind == "source"
        assert info.usable is False
        assert info.notes

    def test_unknown_package(self, tmp_path: Path) -> None:
        archive = _make_zip(tmp_path / "random.zip", {"README.md": b"# readme"})
        info = llmengine.inspect_zip(archive)
        assert info.kind == "unknown"
        assert info.problems

    def test_missing_file(self, tmp_path: Path) -> None:
        info = llmengine.inspect_zip(tmp_path / "absent.zip")
        assert info.valid is False
        assert info.kind == "invalid"
        assert "文件不存在" in info.problems[0]

    def test_not_a_zip(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.zip"
        broken.write_bytes(b"not a zip")
        info = llmengine.inspect_zip(broken)
        assert info.valid is False
        assert "不是有效的 zip" in info.problems[0]

    def test_deep_check_detects_crc_error(self, tmp_path: Path) -> None:
        archive = tmp_path / "corrupt.zip"
        payload = b"x" * 64
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            bundle.writestr("llama-server.exe", payload)
        data = bytearray(archive.read_bytes())
        offset = data.find(payload)
        assert offset >= 0
        data[offset] ^= 0xFF
        archive.write_bytes(bytes(data))
        info = llmengine.inspect_zip(archive, deep=True)
        assert info.bad_member == "llama-server.exe"
        assert info.usable is False


def test_inspect_zips_expands_directory(tmp_path: Path) -> None:
    _make_zip(tmp_path / "b.zip", {"README.md": b""})
    _make_zip(tmp_path / "a.zip", {"README.md": b""})
    (tmp_path / "note.txt").write_text("skip", encoding="utf-8")
    infos = llmengine.inspect_zips([tmp_path])
    assert [info.path.name for info in infos] == ["a.zip", "b.zip"]


def test_inspect_cmd_prints_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exe = llmengine.engine_exe_name()
    archive = _make_zip(tmp_path / "llama-b7-bin-win-cpu-x64.zip", {exe: b"exe"})
    llmengine.inspect_cmd([archive])
    out = capsys.readouterr().out
    assert "zip 离线包检查" in out
    assert "b7" in out
    assert "cpu" in out


def test_inspect_cmd_without_archives(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    llmengine.inspect_cmd([tmp_path])
    assert "没有找到 zip 压缩包" in capsys.readouterr().out


def test_install_from_archives(tmp_path: Path) -> None:
    home = tmp_path / "home"
    exe = llmengine.engine_exe_name()
    engine_zip = _make_zip(tmp_path / "engine.zip", {f"bin/{exe}": b"exe"})
    runtime_zip = _make_zip(
        tmp_path / "cudart-llama-bin-win-cuda-13.5-x64.zip",
        {"cudart64_13.dll": b"dll"},
    )
    info = llmengine.install_engine("b7", archives=[engine_zip, runtime_zip], home=home)
    assert info.tag == "b7"
    assert info.exe.is_file()
    assert (llmenv.engines_root(home) / "b7" / "cudart64_13.dll").is_file()
    assert engine_zip.is_file()
    marker = json.loads((llmenv.engines_root(home) / "b7" / "engine.json").read_text(encoding="utf-8"))
    assert "engine.zip" in marker["source"]
    assert "cudart" in marker["source"]


def test_install_from_archives_adds_runtime_to_existing_engine(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = llmenv.engines_root(home) / "b7"
    target.mkdir(parents=True)
    exe = target / llmengine.engine_exe_name()
    exe.write_bytes(b"exe")
    runtime_zip = _make_zip(
        tmp_path / "cudart-llama-bin-win-cuda-13.5-x64.zip",
        {"cudart64_13.dll": b"dll"},
    )
    info = llmengine.install_engine("b7", archives=[runtime_zip], home=home)
    assert info.exe == exe
    assert (target / "cudart64_13.dll").is_file()


def test_install_from_archives_rejects_source_package(tmp_path: Path) -> None:
    source_zip = _make_zip(tmp_path / "source.zip", {"convert_hf_to_gguf.py": b""})
    with pytest.raises(ServeError, match="源码包"):
        llmengine.install_engine("b7", archives=[source_zip], home=tmp_path / "home")


def test_install_from_archives_rejects_invalid_package(tmp_path: Path) -> None:
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip")
    with pytest.raises(ServeError, match="离线包无效"):
        llmengine.install_engine("b7", archives=[broken], home=tmp_path / "home")


def test_install_from_archives_requires_engine_exe(tmp_path: Path) -> None:
    runtime_zip = _make_zip(
        tmp_path / "cudart-llama-bin-win-cuda-13.5-x64.zip",
        {"cudart64_13.dll": b"dll"},
    )
    with pytest.raises(ServeError, match="找不到"):
        llmengine.install_engine("b7", archives=[runtime_zip], home=tmp_path / "home")


def test_install_cmd_rejects_path_and_zips(tmp_path: Path) -> None:
    with pytest.raises(ServeError, match="不能同时使用"):
        llmengine.install_cmd("b7", path=tmp_path, zips=[tmp_path / "a.zip"])


def test_install_rejects_empty_archives(tmp_path: Path) -> None:
    with pytest.raises(ServeError, match="未指定任何离线包"):
        llmengine.install_engine("b7", archives=[], home=tmp_path / "home")


def test_content_length_parsing() -> None:
    class _Missing:
        pass

    class _Broken:
        headers = {"Content-Length": "oops"}

    assert llmengine._content_length(_Missing()) is None
    assert llmengine._content_length(_Broken()) is None


def test_install_reports_download_progress(tmp_path: Path) -> None:
    home = tmp_path / "home"
    payload = _zip_bytes({llmengine.engine_exe_name(): b"exe"})

    class _Response:
        headers = {"Content-Length": str(len(payload))}

        def __init__(self) -> None:
            self._pos = 0

        def read(self, size: int) -> bytes:
            chunk = payload[self._pos : self._pos + size]
            self._pos += len(chunk)
            return chunk

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

    events: list[tuple[str, int, int | None]] = []
    info = llmengine.install_engine(
        "b8",
        url="https://example.com/engine.zip",
        home=home,
        opener=cast(Any, lambda request, timeout: _Response()),
        on_progress=lambda name, done, total: events.append((name, done, total)),
    )
    assert info.exe.is_file()
    assert events[0] == ("engine.zip", 0, len(payload))
    assert events[-1] == ("engine.zip", len(payload), len(payload))


# ---------------------------------------------------------------------- #
# 引擎输出解析
# ---------------------------------------------------------------------- #
class TestProbeVersion:
    """``llama-server --version`` 输出解析。"""

    def test_returns_first_stdout_line(self) -> None:
        engine = EngineInfo("b1", Path("llama-server.exe"))

        def runner(cmd: list[str], **_kwargs: object) -> CommandResult:
            assert cmd == ["llama-server.exe", "--version"]
            return CommandResult(0, "version: 1234 (abc)\nextra\n")

        assert llmengine.probe_version(engine, runner) == "version: 1234 (abc)"

    def test_falls_back_to_stderr(self) -> None:
        engine = EngineInfo("b1", Path("llama-server.exe"))
        assert llmengine.probe_version(engine, lambda *_a, **_k: CommandResult(0, "", "from stderr\n")) == "from stderr"

    def test_reports_first_line_on_failure(self) -> None:
        engine = EngineInfo("b1", Path("llama-server.exe"))
        result = llmengine.probe_version(engine, lambda *_a, **_k: CommandResult(2, "", "boom\ndetail"))
        assert result == "获取失败（boom）"

    def test_reports_exit_code_without_output(self) -> None:
        engine = EngineInfo("b1", Path("llama-server.exe"))
        assert llmengine.probe_version(engine, lambda *_a, **_k: CommandResult(3)) == "获取失败（退出码 3）"

    def test_reports_unknown_when_output_empty(self) -> None:
        engine = EngineInfo("b1", Path("llama-server.exe"))
        assert llmengine.probe_version(engine, lambda *_a, **_k: CommandResult(0)) == "未知"


class TestArchiveMarker:
    def test_oversized_marker_is_ignored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(llmengine, "CHUNK_SIZE", 4)
        archive = _make_zip(
            tmp_path / "llama-b4321-bin-win-cuda-13.5-x64.zip",
            {"engine.json": b'{"tag":"b4321"}', "llama-server.exe": b"x"},
        )
        inspection = llmengine.inspect_zip(archive)
        assert inspection.kind == "engine"
        assert inspection.tag == "b4321"

    def test_invalid_utf8_marker_is_ignored(self, tmp_path: Path) -> None:
        archive = _make_zip(
            tmp_path / "llama-b4321-bin-win-cuda-13.5-x64.zip",
            {"engine.json": b"\xff\xfe", "llama-server.exe": b"x"},
        )
        inspection = llmengine.inspect_zip(archive)
        assert inspection.kind == "engine"
        assert inspection.tag == "b4321"

    def test_unreadable_zip_is_reported(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        archive = _make_zip(tmp_path / "broken.zip", {"llama-server.exe": b"x"})

        def _broken(*_args: object, **_kwargs: object) -> None:
            raise zipfile.BadZipFile("broken")

        monkeypatch.setattr(llmengine.zipfile, "ZipFile", _broken)
        inspection = llmengine.inspect_zip(archive)
        assert inspection.kind == "invalid"
        assert inspection.problems == ("读取失败: broken",)


class TestEngineLookup:
    def test_marker_relative_exe_is_resolved(self, tmp_path: Path) -> None:
        engine_dir = tmp_path / "b1"
        (engine_dir / "bin").mkdir(parents=True)
        exe = engine_dir / "bin" / llmengine.engine_exe_name()
        exe.write_bytes(b"x")
        (engine_dir / llmengine.ENGINE_MARKER).write_text(json.dumps({"exe": "bin/" + exe.name}), encoding="utf-8")

        assert llmengine._engine_exe_in(engine_dir) == exe

    def test_missing_marker_exe_falls_back_to_direct(self, tmp_path: Path) -> None:
        engine_dir = tmp_path / "b1"
        engine_dir.mkdir()
        exe = engine_dir / llmengine.engine_exe_name()
        exe.write_bytes(b"x")
        (engine_dir / llmengine.ENGINE_MARKER).write_text(json.dumps({"exe": "missing.exe"}), encoding="utf-8")

        assert llmengine._engine_exe_in(engine_dir) == exe

    def test_run_with_engine_passes_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", "base-path")
        seen: dict[str, object] = {}

        def runner(cmd: list[str], **kwargs: object) -> CommandResult:
            seen["cmd"] = cmd
            seen["env"] = kwargs.get("env")
            return CommandResult(0)

        engine = EngineInfo("b1", Path("llama-server.exe"), "release", ("D:/vendor",))
        result = llmengine.run_with_engine(runner, ["llama-server.exe"], engine)

        assert result.returncode == 0
        assert seen["cmd"] == ["llama-server.exe"]
        assert cast(dict[str, str], seen["env"])["PATH"] == f"D:/vendor{os.pathsep}base-path"

    def test_resolve_engine_scans_installed_engines(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FCMD_LLAMA_SERVER", raising=False)
        root = tmp_path / "engines"
        (root / "b0").mkdir(parents=True)
        (root / "a0").mkdir()
        (root / "a0" / llmengine.engine_exe_name()).write_bytes(b"x")
        monkeypatch.setattr(llmenv, "engines_root", lambda home=None: root)
        monkeypatch.setattr(llmengine, "current_engine_tag", lambda directory: "gone")

        info = llmengine.resolve_engine()

        assert info.tag == "a0"
        assert info.exe == root / "a0" / llmengine.engine_exe_name()

    def test_resolve_engine_returns_none_when_nothing_installed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FCMD_LLAMA_SERVER", raising=False)
        root = tmp_path / "engines"
        (root / "empty").mkdir(parents=True)
        monkeypatch.setattr(llmenv, "engines_root", lambda home=None: root)
        monkeypatch.setattr(llmengine, "current_engine_tag", lambda directory: None)
        monkeypatch.setattr(llmengine.shutil, "which", lambda name: None)

        with pytest.raises(ServeError, match=r"未找到 llama\.cpp 引擎"):
            llmengine.resolve_engine()

    def test_list_engines_skips_files_and_incomplete_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "engines"
        (root / "b0").mkdir(parents=True)
        (root / "b1").mkdir()
        (root / "b1" / llmengine.engine_exe_name()).write_bytes(b"x")
        (root / "b2").mkdir()
        (root / "b2" / llmengine.engine_exe_name()).write_bytes(b"x")
        (root / "notes.txt").write_text("x", encoding="utf-8")
        monkeypatch.setattr(llmenv, "engines_root", lambda home=None: root)
        monkeypatch.setattr(llmengine, "current_engine_tag", lambda directory: "b1")

        infos = llmengine.list_engines()

        assert [info.tag for info in infos] == ["b2", "b1"]
        assert infos[0].source == str(root / "b2")
        assert infos[1].source == "current"

    def test_install_rejects_path_and_archives(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(llmenv, "engines_root", lambda home=None: tmp_path / "engines")
        with pytest.raises(ServeError, match="不能同时指定"):
            llmengine.install_engine("b1", path=tmp_path, archives=[tmp_path / "a.zip"])

    def test_release_url_points_to_release_asset(self) -> None:
        assert (
            llmengine._release_url("b1", "asset.zip")
            == "https://github.com/ggml-org/llama.cpp/releases/download/b1/asset.zip"
        )


def test_inspect_cmd_prints_notes_and_problems(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inspection = llmengine.ZipInspection(
        Path("cudart.zip"),
        "cudart",
        True,
        notes=("需与引擎包一起安装",),
        problems=("缺少 llama-server",),
    )
    monkeypatch.setattr(llmengine, "inspect_zips", lambda paths, deep=False: [inspection])

    llmengine.inspect_cmd([Path("cudart.zip")])

    out = capsys.readouterr().out
    assert "需与引擎包一起安装" in out
    assert "缺少 llama-server" in out
