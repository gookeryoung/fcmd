"""llmengine 独立命令测试。"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any, cast

import pytest

from fcmd.apps.locllm import llmengine, llmenginew, llmenv
from fcmd.apps.locllm.llmbase import Check, CommandResult, EngineInfo, ServeError
from fcmd.core.toolkit import _TOOL_REGISTRY


def test_tool_registered() -> None:
    assert set(_TOOL_REGISTRY["llmengine"]) == {"list", "install", "use", "check", "inspect"}


def test_engine_window_registered() -> None:
    assert set(_TOOL_REGISTRY["llmenginew"]) == {None}


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
# llmenginew GUI 入口
# ---------------------------------------------------------------------- #
class TestEnginewGuiEntry:
    def test_run_gui_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_window() -> None:
            raise ImportError("no tkinter")

        monkeypatch.setattr(llmenginew, "EngineWindow", fake_window)
        llmenginew.run_gui()  # 不应抛出

    def test_run_gui_starts_mainloop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        class _Root:
            def mainloop(self) -> None:
                calls.append("mainloop")

        class _Window:
            root = _Root()

        monkeypatch.setattr(llmenginew, "EngineWindow", _Window)
        llmenginew.run_gui()
        assert calls == ["mainloop"]

    def test_launch_cmd_calls_run_gui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []
        monkeypatch.setattr(llmenginew, "run_gui", lambda: calls.append(True))
        llmenginew.launch_cmd()
        assert calls == [True]


class TestEnginewSettings:
    """llmenginew 窗口位置/大小持久化（纯逻辑，不创建 Tk）。"""

    def test_config_path_uses_fcmd_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert llmenginew.config_path() == tmp_path / "ui" / "llmenginew.json"

    def test_load_missing_file_returns_default(self, tmp_path: Path) -> None:
        assert llmenginew.load_settings(tmp_path / "none.json") == llmenginew.EngineSettings()

    def test_load_ignores_invalid_geometry(self, tmp_path: Path) -> None:
        path = tmp_path / "ui.json"
        path.write_text('{"geometry": "bad"}', encoding="utf-8")
        assert llmenginew.load_settings(path).geometry is None
        path.write_text('{"geometry": 123}', encoding="utf-8")
        assert llmenginew.load_settings(path).geometry is None

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "ui.json"
        llmenginew.save_settings(llmenginew.EngineSettings(geometry="900x600+10+20"), path)
        assert llmenginew.load_settings(path).geometry == "900x600+10+20"

    def test_save_ignores_write_error(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        llmenginew.save_settings(llmenginew.EngineSettings(geometry="900x600"), blocker / "ui.json")

    def test_resolve_geometry_keeps_visible_position(self) -> None:
        assert llmenginew.resolve_geometry("900x600+100+80", 1920, 1080) == "900x600+100+80"

    def test_resolve_geometry_pulls_back_offscreen(self) -> None:
        assert llmenginew.resolve_geometry("900x600+5000+5000", 1920, 1080) == "900x600+1020+480"

    def test_resolve_geometry_enforces_minimum_size(self) -> None:
        assert llmenginew.resolve_geometry("200x100+0+0", 1920, 1080) == "780x520+0+0"

    def test_resolve_geometry_falls_back_to_default_size(self) -> None:
        assert llmenginew.resolve_geometry(None, 1920, 1080) == "880x640"
        assert llmenginew.resolve_geometry("bad", 1920, 1080) == "880x640"

    def test_resolve_geometry_clamps_default_to_small_screen(self) -> None:
        assert llmenginew.resolve_geometry(None, 800, 600) == "800x600"


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
