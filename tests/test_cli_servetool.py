"""servetool 工具测试。

覆盖：
- 注册 / 别名 / 子命令
- 状态文件读写与损坏处理、名称校验
- 模型定位、显存估算、GPU 探测
- 引擎解析（env / tag / current / 扫描 / PATH）、安装与注册、切换
- 健康检查、日志、启动（假进程 + 假健康检查）、停止、进程存活校验
- bench / client_env / doctor 体检
- CLI 子命令输出（假核心函数，不接触真实 GPU / 网络）
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile
from collections import namedtuple
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.tools.ai import serve_core as core
from fcmd.tools.ai import servetool
from fcmd.tools.ai.serve_core import (
    DEFAULT_API_KEY,
    DEFAULT_PORT,
    PROFILES,
    BenchResult,
    Check,
    CommandResult,
    EngineInfo,
    GpuInfo,
    LaunchPlan,
    ProfileName,
    ServeError,
    ServeState,
    StopResult,
)

DiskUsage = namedtuple("DiskUsage", "total used free")


# ---------------------------------------------------------------------- #
# 测试替身
# ---------------------------------------------------------------------- #
class _FakeResponse:
    """最小 urllib 响应替身。"""

    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._payload = payload
        self._offset = 0
        self.status = status

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk = self._payload[self._offset :]
        else:
            chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _opener(payload: bytes = b"", status: int = 200, seen: list[Request] | None = None) -> Any:
    """返回固定响应的 opener 替身。"""

    def _open(request: Request, timeout: float = 2.0) -> _FakeResponse:
        if seen is not None:
            seen.append(request)
        return _FakeResponse(payload, status)

    return _open


def _zip_payload(entries: dict[str, bytes]) -> bytes:
    """构造 zip 字节流。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in entries.items():
            bundle.writestr(name, data)
    return buffer.getvalue()


class _FakeProc:
    """假 Popen：可指定立即退出的返回码。"""

    def __init__(self, pid: int, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


def _state(  # noqa: PLR0913
    name: str = "qwen",
    *,
    pid: int = 12345,
    create_time: float = 0.0,
    port: int = DEFAULT_PORT,
    host: str = "127.0.0.1",
    log: str = "log.txt",
) -> ServeState:
    """构造状态对象。"""
    return ServeState(
        name=name,
        pid=pid,
        create_time=create_time,
        host=host,
        port=port,
        model="D:/models/m.gguf",
        profile="balanced",
        context=65536,
        engine="llama-server.exe",
        alias="qwen3-coder-30b-a3b",
        api_key=DEFAULT_API_KEY,
        log=log,
        started_at="2026-11-09T12:00:00+08:00",
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """隔离的 FCMD_HOME。"""
    return tmp_path / "home"


@pytest.fixture
def model_file(tmp_path: Path) -> Path:
    """带量化名的假 GGUF。"""
    path = tmp_path / "models" / "repo" / "Qwen3-Coder-30B-A3B-Instruct-Q5_K_M.gguf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x" * 2048)
    return path


def _engine_dir(tmp_path: Path) -> Path:
    """创建含 llama-server 的引擎目录。"""
    directory = tmp_path / "engine"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / core.engine_exe_name()).write_text("", encoding="utf-8")
    return directory


def _plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path) -> LaunchPlan:
    """构造可用启动计划（引擎走 FCMD_LLAMA_SERVER）。"""
    engine = _engine_dir(tmp_path) / core.engine_exe_name()
    monkeypatch.setenv("FCMD_LLAMA_SERVER", str(engine))
    return core.make_plan("qwen", home=home, model=model_file)


# ---------------------------------------------------------------------- #
# 注册与基础辅助
# ---------------------------------------------------------------------- #
class TestRegistration:
    def test_registered(self) -> None:
        assert "servetool" in _TOOL_REGISTRY

    def test_subcommands(self) -> None:
        subs = set(_TOOL_REGISTRY["servetool"])
        assert {
            "doctor",
            "engine",
            "model",
            "plan",
            "start",
            "stop",
            "status",
            "logs",
            "bench",
            "env",
        } <= subs

    def test_alias(self) -> None:
        assert servetool.__tool_aliases__ == ["srv"]


class TestHelpers:
    def test_human_size(self) -> None:
        assert core.human_size(0) == "0 B"
        assert core.human_size(2048) == "2.00 KB"
        assert core.human_size(3 * 1024**3) == "3.00 GB"

    def test_format_returncode(self) -> None:
        assert core.format_returncode(0) == "0"
        rendered = core.format_returncode(-1073741819)
        assert "0xC0000005" in rendered

    def test_client_host_and_url(self) -> None:
        assert core.client_host("0.0.0.0") == "127.0.0.1"
        assert core.client_host("127.0.0.1") == "127.0.0.1"
        assert core.http_base_url("0.0.0.0", 8080) == "http://127.0.0.1:8080"

    def test_validate_name(self) -> None:
        assert core.validate_name("qwen-1.0_x") == "qwen-1.0_x"
        for bad in ("../x", "-x", "", "a/b"):
            with pytest.raises(ServeError, match="服务名不合法"):
                core.validate_name(bad)

    def test_format_command(self) -> None:
        assert core.format_command(["a b", "c"]) == "'a b' c"

    def test_run_command(self) -> None:
        result = core.run_command([sys.executable, "-c", "print('ok')"])
        assert result.returncode == 0
        assert "ok" in result.stdout

    def test_run_command_timeout(self) -> None:
        result = core.run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.01)
        assert result.returncode == -1
        assert "超时" in result.stderr

    def test_run_command_missing(self) -> None:
        result = core.run_command(["definitely-not-a-command-fcmd"])
        assert result.returncode == -1

    def test_profile_table(self) -> None:
        assert set(PROFILES) == {"speed", "balanced", "long", "max-context"}
        assert PROFILES["balanced"].quant == "Q5_K_M"
        assert PROFILES["speed"].ubatch == 2048
        assert PROFILES["max-context"].context == 196608


# ---------------------------------------------------------------------- #
# 状态文件
# ---------------------------------------------------------------------- #
class TestState:
    def test_roundtrip(self, home: Path) -> None:
        state = _state()
        path = core.save_state(state, home)
        assert path.is_file()
        assert core.load_state("qwen", home) == state
        assert core.require_state("qwen", home) == state
        assert core.list_states(home) == [state]
        assert core.delete_state("qwen", home) is True
        assert core.delete_state("qwen", home) is False

    def test_missing(self, home: Path) -> None:
        assert core.load_state("qwen", home, missing_ok=True) is None
        with pytest.raises(ServeError, match="未启动"):
            core.load_state("qwen", home)
        with pytest.raises(ServeError, match="未启动"):
            core.require_state("qwen", home)

    def test_corrupted(self, home: Path) -> None:
        path = core.state_path("qwen", home)
        path.parent.mkdir(parents=True)
        path.write_text("{bad", encoding="utf-8")
        with pytest.raises(ServeError, match="状态文件损坏"):
            core.load_state("qwen", home)

    def test_list_skips_corrupted(self, home: Path) -> None:
        core.save_state(_state("good"), home)
        bad = core.state_path("bad", home)
        bad.write_text("{bad", encoding="utf-8")
        assert [s.name for s in core.list_states(home)] == ["good"]

    def test_list_missing_dir(self, home: Path) -> None:
        assert core.list_states(home) == []

    def test_paths(self, home: Path) -> None:
        assert core.serve_dir(home) == home / "serve"
        assert core.log_path("qwen", home) == home / "serve" / "logs" / "qwen.log"
        assert core.state_path("qwen", home) == home / "serve" / "qwen.json"
        assert core.engines_root(home) == home / "engines" / "llama.cpp"

    def test_fcmd_home_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path / "custom"))
        assert core.fcmd_home() == tmp_path / "custom"

    def test_state_url_and_env(self) -> None:
        state = _state(host="0.0.0.0", port=9000)
        assert state.base_url == "http://127.0.0.1:9000"
        env = core.client_env(state)
        assert env == {
            "OPENAI_BASE_URL": "http://127.0.0.1:9000/v1",
            "OPENAI_API_KEY": DEFAULT_API_KEY,
            "OPENAI_MODEL": "qwen3-coder-30b-a3b",
        }


# ---------------------------------------------------------------------- #
# 模型与显存
# ---------------------------------------------------------------------- #
class TestModel:
    def test_explicit_file(self, model_file: Path) -> None:
        assert core.find_model("Q5_K_M", model=model_file) == model_file.resolve()

    def test_explicit_non_gguf(self, tmp_path: Path) -> None:
        path = tmp_path / "model.bin"
        path.write_text("", encoding="utf-8")
        with pytest.raises(ServeError, match=r"不是 \.gguf"):
            core.find_model("Q5_K_M", model=path)

    def test_explicit_missing(self, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="模型路径不存在"):
            core.find_model("Q5_K_M", model=tmp_path / "nope.gguf")

    def test_explicit_dir(self, model_file: Path) -> None:
        assert core.find_model("Q5_K_M", model=model_file.parent) == model_file.resolve()

    def test_models_root_search_ignores_mmproj(self, model_file: Path) -> None:
        mmproj = model_file.parent / "mmproj-Q5_K_M.gguf"
        mmproj.write_bytes(b"y" * 99999)
        assert core.find_model("Q5_K_M", models_root=model_file.parent.parent) == model_file.resolve()

    def test_no_match_lists_available(self, model_file: Path) -> None:
        with pytest.raises(ServeError, match="未找到量化 Q8_0"):
            core.find_model("Q8_0", models_root=model_file.parent.parent)

    def test_missing_root(self, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="模型目录不存在"):
            core.find_model("Q5_K_M", models_root=tmp_path / "nope")

    def test_default_models_root_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_MODELS", str(tmp_path / "m"))
        assert core.default_models_root() == tmp_path / "m"

    def test_estimate_vram(self) -> None:
        f16 = core.estimate_vram(10, 65536, "f16")
        q8 = core.estimate_vram(10, 65536, "q8_0")
        assert core.estimate_vram(10, 0, "q8_0") == 10 + core.VRAM_OVERHEAD_BYTES
        assert q8 < f16
        assert core.estimate_vram(10, 65536, "unknown") == f16

    def test_plan_vram(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        expected = core.estimate_vram(model_file.stat().st_size, plan.context, plan.profile.kv_type)
        assert core.plan_vram(plan) == expected
        assert plan.base_url == "http://127.0.0.1:8080"


# ---------------------------------------------------------------------- #
# GPU / 引擎
# ---------------------------------------------------------------------- #
class TestGpu:
    def test_detect(self) -> None:
        gpu = core.detect_gpu(lambda cmd: CommandResult(0, "NVIDIA RTX 5090 D, 32607\n"))
        assert gpu == GpuInfo("NVIDIA RTX 5090 D", 32607)

    def test_detect_failures(self) -> None:
        assert core.detect_gpu(lambda cmd: CommandResult(1, "", "err")) is None
        assert core.detect_gpu(lambda cmd: CommandResult(0, "")) is None
        assert core.detect_gpu(lambda cmd: CommandResult(0, "bad line")) is None
        assert core.detect_gpu(lambda cmd: CommandResult(0, "name, not-a-number")) is None


class TestEngine:
    def test_engine_exe_name(self) -> None:
        expected = "llama-server.exe" if os.name == "nt" else "llama-server"
        assert core.engine_exe_name() == expected

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        exe = tmp_path / core.engine_exe_name()
        exe.write_text("", encoding="utf-8")
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(exe))
        info = core.resolve_engine(home=tmp_path / "home")
        assert info.tag == "env"
        assert info.exe == exe

    def test_env_override_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(tmp_path / "nope"))
        with pytest.raises(ServeError, match="FCMD_LLAMA_SERVER"):
            core.resolve_engine(home=tmp_path / "home")

    def test_explicit_tag(self, home: Path) -> None:
        directory = core.engines_root(home) / "b1"
        directory.mkdir(parents=True)
        (directory / core.engine_exe_name()).write_text("", encoding="utf-8")
        info = core.resolve_engine("b1", home)
        assert info.tag == "b1"

    def test_explicit_tag_missing(self, home: Path) -> None:
        with pytest.raises(ServeError, match="未安装"):
            core.resolve_engine("nope", home)

    def test_current_pointer(self, home: Path) -> None:
        directory = core.engines_root(home) / "b2"
        directory.mkdir(parents=True)
        (directory / core.engine_exe_name()).write_text("", encoding="utf-8")
        core.use_engine("b2", home)
        assert core.resolve_engine(home=home).tag == "b2"

    def test_current_pointer_broken_falls_back(self, home: Path) -> None:
        root = core.engines_root(home)
        root.mkdir(parents=True)
        (root / "current.json").write_text(json.dumps({"tag": "gone"}), encoding="utf-8")
        (root / "current.json").write_text("not json", encoding="utf-8")
        directory = root / "b1"
        directory.mkdir()
        (directory / core.engine_exe_name()).write_text("", encoding="utf-8")
        assert core.resolve_engine(home=home).tag == "b1"

    def test_latest_scan(self, home: Path) -> None:
        for tag in ("b1", "b2"):
            directory = core.engines_root(home) / tag
            directory.mkdir(parents=True)
            (directory / core.engine_exe_name()).write_text("", encoding="utf-8")
        assert core.resolve_engine(home=home).tag == "b2"

    def test_path_fallback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        exe = tmp_path / core.engine_exe_name()
        exe.write_text("", encoding="utf-8")
        monkeypatch.delenv("FCMD_LLAMA_SERVER", raising=False)
        monkeypatch.setattr(core.shutil, "which", lambda name: str(exe))
        info = core.resolve_engine(home=tmp_path / "home")
        assert info.tag == "system"

    def test_not_found(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("FCMD_LLAMA_SERVER", raising=False)
        monkeypatch.setattr(core.shutil, "which", lambda name: None)
        with pytest.raises(ServeError, match=r"未找到 llama\.cpp 引擎"):
            core.resolve_engine(home=tmp_path / "home")

    def test_marker_absolute_exe(self, home: Path, tmp_path: Path) -> None:
        target = core.engines_root(home) / "b3"
        target.mkdir(parents=True)
        exe = tmp_path / "elsewhere" / core.engine_exe_name()
        exe.parent.mkdir()
        exe.write_text("", encoding="utf-8")
        (target / "engine.json").write_text(json.dumps({"exe": str(exe)}), encoding="utf-8")
        assert core.resolve_engine("b3", home).exe == exe

    def test_marker_broken_falls_back_to_direct(self, home: Path) -> None:
        target = core.engines_root(home) / "b4"
        target.mkdir(parents=True)
        (target / "engine.json").write_text("not json", encoding="utf-8")
        exe = target / core.engine_exe_name()
        exe.write_text("", encoding="utf-8")
        assert core.resolve_engine("b4", home).exe == exe

    def test_marker_non_dict_and_bad_prepend(self, home: Path) -> None:
        target = core.engines_root(home) / "b5"
        target.mkdir(parents=True)
        exe = target / core.engine_exe_name()
        exe.write_text("", encoding="utf-8")
        (target / "engine.json").write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        assert core.resolve_engine("b5", home).path_prepend == ()
        (target / "engine.json").write_text(json.dumps({"path_prepend": "not-a-list"}), encoding="utf-8")
        assert core.resolve_engine("b5", home).path_prepend == ()

    def test_asset_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(core.os, "name", "nt")
        assert core.engine_asset_names("b1", "cuda") == [
            "llama-b1-bin-win-cuda-13.5-x64.zip",
            "cudart-llama-bin-win-cuda-13.5-x64.zip",
        ]
        assert core.engine_asset_names("b1", "cpu") == ["llama-b1-bin-win-cpu-x64.zip"]

    def test_asset_names_non_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(core.os, "name", "posix")
        with pytest.raises(ServeError, match="非 Windows"):
            core.engine_asset_names("b1")

    def test_install_cached(self, home: Path) -> None:
        directory = core.engines_root(home) / "b1"
        directory.mkdir(parents=True)
        (directory / core.engine_exe_name()).write_text("", encoding="utf-8")
        info = core.install_engine("b1", home=home, opener=cast(Any, lambda *a: pytest.fail("不应下载")))
        assert info.tag == "b1"

    def test_install_from_path(self, home: Path, tmp_path: Path) -> None:
        source = _engine_dir(tmp_path)
        info = core.install_engine("local", path=source, home=home)
        assert info.exe == source / core.engine_exe_name()
        assert (core.engines_root(home) / "local" / "engine.json").is_file()

    def test_install_path_detects_vendor(self, home: Path, tmp_path: Path) -> None:
        source = _engine_dir(tmp_path)
        vendor = tmp_path / "vendor" / "cuda13"
        vendor.mkdir(parents=True)
        (vendor / "cudart64_13.dll").write_text("", encoding="utf-8")
        info = core.install_engine("lmstudio", path=source, home=home)
        assert info.path_prepend == (str(vendor.resolve()),)
        assert core.resolve_engine("lmstudio", home).path_prepend == info.path_prepend

    def test_install_path_without_vendor(self, home: Path, tmp_path: Path) -> None:
        source = _engine_dir(tmp_path)
        assert core.install_engine("plain", path=source, home=home).path_prepend == ()

    def test_install_from_missing_path(self, home: Path, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="引擎目录不存在"):
            core.install_engine("local", path=tmp_path / "nope", home=home)

    def test_install_path_without_exe(self, home: Path, tmp_path: Path) -> None:
        source = tmp_path / "empty"
        source.mkdir()
        with pytest.raises(ServeError, match="找不到"):
            core.install_engine("local", path=source, home=home)

    def test_install_from_url(self, home: Path) -> None:
        payload = _zip_payload({f"bundle/{core.engine_exe_name()}": b"exe"})
        info = core.install_engine("b9", url="https://example.com/engine.zip", home=home, opener=_opener(payload))
        assert info.exe.is_file()
        assert info.source == "https://example.com/engine.zip"

    def test_install_from_url_without_exe(self, home: Path) -> None:
        payload = _zip_payload({"readme.txt": b"nope"})
        with pytest.raises(ServeError, match="找不到"):
            core.install_engine("b9", url="https://example.com/engine.zip", home=home, opener=_opener(payload))

    def test_download_failure(self, home: Path) -> None:
        def _boom(request: Request, timeout: float = 1.0) -> Any:
            raise URLError("offline")

        with pytest.raises(ServeError, match="下载失败"):
            core.install_engine("b9", url="https://example.com/engine.zip", home=home, opener=_boom)

    def test_bad_zip(self, home: Path) -> None:
        with pytest.raises(ServeError, match="解压失败"):
            core.install_engine("b9", url="https://example.com/engine.zip", home=home, opener=_opener(b"not zip"))

    def test_list_and_use(self, home: Path) -> None:
        assert core.list_engines(home) == []
        for tag in ("b1", "b2"):
            directory = core.engines_root(home) / tag
            directory.mkdir(parents=True)
            (directory / core.engine_exe_name()).write_text("", encoding="utf-8")
        assert [info.tag for info in core.list_engines(home)] == ["b2", "b1"]
        core.use_engine("b1", home)
        listed = {info.tag: info.source for info in core.list_engines(home)}
        assert listed["b1"] == "current"

    def test_use_missing(self, home: Path) -> None:
        with pytest.raises(ServeError, match="未安装"):
            core.use_engine("nope", home)

    def test_check_engine(self) -> None:
        info = EngineInfo("b1", Path("llama-server.exe"))
        ok = core.check_engine(info, lambda cmd: CommandResult(0, "b12211\n"))
        assert ok.status == "ok"
        bad = core.check_engine(info, lambda cmd: CommandResult(-1073741819, "", "crash"))
        assert bad.status == "fail"
        assert "0xC0000005" in bad.detail


# ---------------------------------------------------------------------- #
# 计划 / 健康检查 / 启动 / 停止
# ---------------------------------------------------------------------- #
class TestPlan:
    def test_make_plan(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        assert plan.model == model_file.resolve()
        assert plan.profile.name == "balanced"
        assert plan.context == 65536
        assert plan.log == core.log_path("qwen", home)
        argv = plan.argv()
        assert argv[0] == str(plan.engine.exe)
        for flag, value in (("-c", "65536"), ("-ctk", "q8_0"), ("-ub", "1024"), ("--port", "8080")):
            index = argv.index(flag)
            assert argv[index + 1] == value
        assert "--jinja" in argv
        assert "--api-key" in argv

    def test_ctx_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path) -> None:
        engine = _engine_dir(tmp_path) / core.engine_exe_name()
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(engine))
        plan = core.make_plan("qwen", "speed", home=home, model=model_file, ctx=4096, port=9001)
        assert plan.context == 4096
        assert plan.port == 9001
        assert plan.profile.ubatch == 2048

    def test_unknown_profile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path
    ) -> None:
        engine = _engine_dir(tmp_path) / core.engine_exe_name()
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(engine))
        with pytest.raises(ServeError, match="未知档位"):
            core.make_plan(profile=cast(ProfileName, "turbo"), home=home, model=model_file)

    def test_invalid_port(self, home: Path, model_file: Path) -> None:
        with pytest.raises(ServeError, match="端口不合法"):
            core.make_plan(home=home, model=model_file, port=70000)

    def test_validation_order(self, home: Path) -> None:
        with pytest.raises(ServeError, match="服务名不合法"):
            core.make_plan("../bad", home=home)


class TestHealth:
    def test_ok(self) -> None:
        assert core.check_health("127.0.0.1", 8080, "k", opener=_opener(b'{"status":"ok"}')) is True

    def test_http_error(self) -> None:
        def _boom(request: Request, timeout: float = 1.0) -> Any:
            raise HTTPError(request.full_url, 500, "err", cast(Any, None), None)

        assert core.check_health("127.0.0.1", 8080, None, opener=_boom) is False

    def test_network_error(self) -> None:
        def _boom(request: Request, timeout: float = 1.0) -> Any:
            raise URLError("offline")

        assert core.check_health("127.0.0.1", 8080, "k", opener=_boom) is False

    def test_non_200(self) -> None:
        assert core.check_health("127.0.0.1", 8080, None, opener=_opener(b"", status=503)) is False


class TestProcess:
    def test_alive_current_process(self) -> None:
        pid = os.getpid()
        assert core.process_alive(pid) is True
        assert core.process_alive(pid, core.process_create_time(pid)) is True

    def test_create_time_mismatch(self) -> None:
        pid = os.getpid()
        assert core.process_alive(pid, core.process_create_time(pid) + 100) is False

    def test_missing_process(self) -> None:
        assert core.process_alive(2**31 - 1) is False
        assert core.process_create_time(2**31 - 1) == 0.0

    def test_terminate_real_child(self) -> None:
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            core._terminate(proc.pid, core.process_create_time(proc.pid), 5.0)
            assert proc.poll() is not None
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(5)

    def test_terminate_reused_pid(self) -> None:
        pid = os.getpid()
        with pytest.raises(ServeError, match="复用"):
            core._terminate(pid, core.process_create_time(pid) + 100, 1.0)

    def test_terminate_missing_process(self) -> None:
        core._terminate(2**31 - 1, 0.0, 1.0)


class TestLaunch:
    def test_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        state = core.launch(
            plan,
            home=home,
            popen=lambda argv, **kwargs: _FakeProc(os.getpid()),
            health=lambda host, port, key: True,
            sleep=lambda _: None,
        )
        assert state.pid == os.getpid()
        assert state.model == str(model_file.resolve())
        assert core.load_state("qwen", home) == state
        assert plan.log.is_file()

    def test_early_exit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        with pytest.raises(ServeError, match="立即退出"):
            core.launch(
                plan,
                home=home,
                popen=lambda argv, **kwargs: _FakeProc(os.getpid(), 2),
                health=lambda host, port, key: False,
                sleep=lambda _: None,
            )

    def test_timeout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        terminated: list[int] = []
        monkeypatch.setattr(core, "_terminate", lambda pid, ct, t: terminated.append(pid))
        with pytest.raises(ServeError, match="健康检查超时"):
            core.launch(
                plan,
                home=home,
                wait_s=0,
                popen=lambda argv, **kwargs: _FakeProc(os.getpid()),
                health=lambda host, port, key: False,
            )
        assert terminated == [os.getpid()]

    def test_popen_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)

        def _boom(argv: list[str], **kwargs: Any) -> Any:
            raise OSError("no such file")

        with pytest.raises(ServeError, match="启动引擎失败"):
            core.launch(plan, home=home, popen=_boom, health=lambda host, port, key: True)

    def test_launch_prepends_engine_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path
    ) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        injected = replace(plan, engine=replace(plan.engine, path_prepend=("C:/vendor/cuda13",)))
        captured: dict[str, Any] = {}

        def _popen(argv: list[str], **kwargs: Any) -> Any:
            captured.update(kwargs)
            return _FakeProc(os.getpid())

        core.launch(
            injected,
            home=home,
            popen=_popen,
            health=lambda host, port, key: True,
            sleep=lambda _: None,
        )
        path_value = captured["env"]["PATH"]
        assert path_value.startswith("C:/vendor/cuda13" + os.pathsep)

    def test_start_rejects_running(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path
    ) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        pid = os.getpid()
        core.save_state(
            _state(pid=pid, create_time=core.process_create_time(pid)),
            home,
        )
        with pytest.raises(ServeError, match="已在运行"):
            core.start_server(plan, home=home)

    def test_start_cleans_stale_and_checks_ports(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path
    ) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        core.save_state(_state(pid=2**31 - 1), home)
        monkeypatch.setattr("fcmd.tools.net.portcheck.is_port_in_use", lambda port, host: False)
        state = core.start_server(
            plan,
            home=home,
            popen=lambda argv, **kwargs: _FakeProc(os.getpid()),
            health=lambda host, port, key: True,
            sleep=lambda _: None,
        )
        assert state.name == "qwen"

    def test_start_port_busy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path
    ) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        with pytest.raises(ServeError, match="端口"):
            core.start_server(plan, home=home, port_in_use=lambda port, host: True)


class TestStop:
    def test_not_running(self, home: Path) -> None:
        core.save_state(_state(pid=2**31 - 1), home)
        result = core.stop_server("qwen", home, alive=lambda pid, ct: False)
        assert result.was_running is False
        assert core.load_state("qwen", home, missing_ok=True) is None

    def test_running(self, home: Path) -> None:
        core.save_state(_state(), home)
        killed: list[int] = []
        result = core.stop_server(
            "qwen", home, alive=lambda pid, ct: True, terminator=lambda pid, ct, timeout: killed.append(pid)
        )
        assert result.was_running is True
        assert killed == [12345]
        assert core.load_state("qwen", home, missing_ok=True) is None

    def test_missing(self, home: Path) -> None:
        with pytest.raises(ServeError, match="未启动"):
            core.stop_server("qwen", home)


class TestLogAndBench:
    def test_read_log_tail(self, home: Path) -> None:
        path = core.log_path("qwen", home)
        path.parent.mkdir(parents=True)
        path.write_text("\n".join(f"line{i}" for i in range(5)), encoding="utf-8")
        assert core.read_log("qwen", 2, home) == "line3\nline4"

    def test_read_log_missing(self, home: Path) -> None:
        with pytest.raises(ServeError, match="日志不存在"):
            core.read_log("qwen", home=home)

    def test_bench_usage(self) -> None:
        payload = json.dumps(
            {"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 20}}
        ).encode()
        seen: list[Request] = []
        result = core.bench(_state(), opener=_opener(payload, seen=seen))
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 20
        assert result.tokens_per_s > 0
        assert seen[0].full_url.endswith("/v1/chat/completions")

    def test_bench_without_usage(self) -> None:
        payload = json.dumps({"choices": [{"message": {"content": "abcdef"}}]}).encode()
        result = core.bench(_state(), opener=_opener(payload))
        assert result.completion_tokens == 3
        assert result.prompt_tokens == 0

    def test_bench_empty_choice(self) -> None:
        result = core.bench(_state(), opener=_opener(b"{}"))
        assert result.completion_tokens == 0

    def test_bench_http_error(self) -> None:
        def _boom(request: Request, timeout: float = 1.0) -> Any:
            raise HTTPError(request.full_url, 500, "err", cast(Any, None), None)

        with pytest.raises(ServeError, match="请求失败"):
            core.bench(_state(), opener=_boom)

    def test_bench_bad_json(self) -> None:
        with pytest.raises(ServeError, match="合法 JSON"):
            core.bench(_state(), opener=_opener(b"not json"))


# ---------------------------------------------------------------------- #
# 体检
# ---------------------------------------------------------------------- #
def _doctor_runner(cmd: list[str] | Any) -> CommandResult:
    """体检用假 runner：GPU / nvcc / 引擎版本均成功。"""
    if cmd[0] == "nvidia-smi":
        return CommandResult(0, "NVIDIA RTX 5090 D, 32607\n")
    if cmd[0] == "nvcc":
        return CommandResult(0, "Cuda compilation tools\nBuild cuda_13.0\n")
    return CommandResult(0, "llama-server b12211\n")


class TestDoctor:
    def test_all_ok(self, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(_engine_dir(tmp_path) / core.engine_exe_name()))
        monkeypatch.setattr("fcmd.tools.net.portcheck.is_port_in_use", lambda port, host: False)
        monkeypatch.setattr(core.shutil, "disk_usage", lambda path: DiskUsage(100, 10, 90 * 1024**3))
        checks = core.doctor(home=home, models_root=model_file.parent.parent, runner=_doctor_runner)
        statuses = {check.name: check.status for check in checks}
        assert all(status == "ok" for status in statuses.values())
        assert "显存预算" in statuses

    def test_problems(self, monkeypatch: pytest.MonkeyPatch, home: Path, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(tmp_path / "nope" / core.engine_exe_name()))
        monkeypatch.setattr("fcmd.tools.net.portcheck.is_port_in_use", lambda port, host: True)
        monkeypatch.setattr(core.shutil, "disk_usage", lambda path: DiskUsage(100, 99, 1024))
        checks = core.doctor(home=home, models_root=tmp_path / "empty", runner=lambda cmd: CommandResult(1, "", "err"))
        statuses = {check.name: check.status for check in checks}
        assert statuses["GPU"] == "warn"
        assert statuses["CUDA Toolkit"] == "warn"
        assert statuses["推理引擎"] == "fail"
        assert statuses["模型"] == "warn"
        assert statuses["端口"] == "warn"
        assert statuses["磁盘"] == "warn"

    def test_engine_crash(self, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(_engine_dir(tmp_path) / core.engine_exe_name()))

        def _runner(cmd: list[str] | Any) -> CommandResult:
            if cmd[0] == "nvidia-smi":
                return CommandResult(1, "", "no gpu")
            if cmd[0] == "nvcc":
                return CommandResult(1, "", "no nvcc")
            return CommandResult(-1073741819, "", "crash")

        checks = core.doctor(home=home, models_root=tmp_path / "empty", port=9999, runner=_runner)
        engine = next(check for check in checks if check.name == "推理引擎")
        assert engine.status == "fail"
        assert "0xC0000005" in engine.detail

    def test_writable_failure(self, tmp_path: Path) -> None:
        occupied = tmp_path / "occupied"
        occupied.write_text("x", encoding="utf-8")
        check = core._check_writable(occupied)
        assert check.status == "fail"

    def test_port_owned_by_service(self, monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
        pid = os.getpid()
        core.save_state(_state(pid=pid, create_time=core.process_create_time(pid), port=9188), home)
        monkeypatch.setattr("fcmd.tools.net.portcheck.is_port_in_use", lambda port, host: True)
        check = core._check_port(9188, home)
        assert check.status == "ok"
        assert "qwen" in check.detail

    def test_disk_error(self, monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
        def _boom(path: Path) -> Any:
            raise OSError("no disk")

        monkeypatch.setattr(core.shutil, "disk_usage", _boom)
        check = core._check_disk(None, home)
        assert check.status == "warn"


# ---------------------------------------------------------------------- #
# CLI 子命令
# ---------------------------------------------------------------------- #
class TestCli:
    def test_doctor_ok(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(core, "doctor", lambda **kwargs: [Check("GPU", "ok", "RTX")])
        assert run_tool("servetool", ["doctor"]) == 0
        out = capsys.readouterr().out
        assert "体检" in out
        assert "RTX" in out

    def test_doctor_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(core, "doctor", lambda **kwargs: [Check("引擎", "fail", "crash")])
        with pytest.raises(ServeError, match="1 项检查失败"):
            servetool.doctor_cmd()

    def test_engine_list_empty(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(core, "list_engines", lambda home=None: [])
        assert run_tool("servetool", ["engine", "list"]) == 0
        assert "未安装任何引擎" in capsys.readouterr().out

    def test_engine_list(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        infos = [EngineInfo("b2", Path("b2/llama-server.exe"), "current"), EngineInfo("b1", Path("b1/x"))]
        monkeypatch.setattr(core, "list_engines", lambda home=None: infos)
        servetool.engine_cmd("list")
        out = capsys.readouterr().out
        assert "当前" in out
        assert "b1" in out

    def test_engine_use(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(core, "use_engine", lambda tag, home=None: EngineInfo(tag, Path("x")))
        servetool.engine_cmd("use", tag="b1")
        assert "已切换默认引擎" in capsys.readouterr().out

    def test_engine_install_sets_default(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        calls: list[str] = []
        monkeypatch.setattr(core, "install_engine", lambda tag, **kwargs: EngineInfo(tag, Path("x")))
        monkeypatch.setattr(core, "list_engines", lambda home=None: [])
        monkeypatch.setattr(core, "use_engine", lambda tag, home=None: calls.append(tag) or EngineInfo(tag, Path("x")))
        servetool.engine_cmd("install", tag="b1")
        assert calls == ["b1"]
        assert "已设为默认引擎" in capsys.readouterr().out

    def test_engine_install_keeps_existing_default(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(core, "install_engine", lambda tag, **kwargs: EngineInfo(tag, Path("x")))
        monkeypatch.setattr(core, "list_engines", lambda home=None: [EngineInfo("b0", Path("y"), "current")])
        monkeypatch.setattr(core, "use_engine", lambda *a, **k: pytest.fail("不应切换"))
        servetool.engine_cmd("install", tag="b1")
        assert "引擎就绪" in capsys.readouterr().out

    def test_engine_requires_tag(self) -> None:
        with pytest.raises(ServeError, match="--tag"):
            servetool.engine_cmd("install")

    def test_model_list(self, model_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
        servetool.model_cmd(models_root=model_file.parent.parent)
        out = capsys.readouterr().out
        assert "Q5_K_M.gguf" in out
        assert "balanced" in out

    def test_model_list_extras(self, model_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
        (model_file.parent / "other.gguf").write_bytes(b"y")
        servetool.model_cmd(models_root=model_file.parent.parent)
        assert "other.gguf" in capsys.readouterr().out

    def test_model_missing_dir(self, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="模型目录不存在"):
            servetool.model_cmd(models_root=tmp_path / "nope")

    def test_model_empty_dir(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(ServeError, match=r"没有 \.gguf"):
            servetool.model_cmd(models_root=empty)

    def test_plan_output(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        home: Path,
        model_file: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(_engine_dir(tmp_path) / core.engine_exe_name()))
        monkeypatch.setattr(core, "detect_gpu", lambda runner=core.run_command: None)
        servetool.plan_cmd(model=model_file, models_root=model_file.parent.parent)
        out = capsys.readouterr().out
        assert "启动计划" in out
        assert "--flash-attn" in out

    def test_plan_warns_when_vram_exceeded(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        home: Path,
        model_file: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(_engine_dir(tmp_path) / core.engine_exe_name()))
        monkeypatch.setattr(core, "detect_gpu", lambda runner=core.run_command: GpuInfo("tiny", 1024))
        servetool.plan_cmd(model=model_file, models_root=model_file.parent.parent)
        assert "超过显存" in capsys.readouterr().out

    def test_start_output(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        home: Path,
        model_file: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("FCMD_HOME", str(home))
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(_engine_dir(tmp_path) / core.engine_exe_name()))
        monkeypatch.setattr(core, "detect_gpu", lambda runner=core.run_command: None)
        state = _state(log=str(home / "serve" / "logs" / "qwen.log"))
        monkeypatch.setattr(core, "start_server", lambda plan, wait_s=180.0: state)
        servetool.start_cmd(model=model_file, models_root=model_file.parent.parent)
        out = capsys.readouterr().out
        assert "运行中" in out
        assert "fcmd srv env" in out

    def test_stop_messages(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(core, "stop_server", lambda name, home=None: StopResult(_state(), True))
        servetool.stop_cmd()
        assert "已停止服务" in capsys.readouterr().out
        monkeypatch.setattr(core, "stop_server", lambda name, home=None: StopResult(_state(), False))
        servetool.stop_cmd()
        assert "未在运行" in capsys.readouterr().out

    def test_status_empty(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(core, "list_states", lambda home=None: [])
        assert run_tool("servetool", ["status"]) == 0
        assert "没有已登记的服务" in capsys.readouterr().out

    def test_status_list(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(core, "list_states", lambda home=None: [_state()])
        monkeypatch.setattr(core, "process_alive", lambda pid, ct=0.0: True)
        monkeypatch.setattr(core, "check_health", lambda *a, **k: True)
        servetool.status_cmd()
        assert "运行中" in capsys.readouterr().out

    def test_status_single_states(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(core, "require_state", lambda name, home=None: _state())
        monkeypatch.setattr(core, "process_alive", lambda pid, ct=0.0: False)
        servetool.status_cmd("qwen")
        assert "已退出" in capsys.readouterr().out

        monkeypatch.setattr(core, "process_alive", lambda pid, ct=0.0: True)
        monkeypatch.setattr(core, "check_health", lambda *a, **k: False)
        servetool.status_cmd("qwen")
        assert "无响应" in capsys.readouterr().out

    def test_logs(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path / "home"))
        path = core.log_path("qwen", tmp_path / "home")
        path.parent.mkdir(parents=True)
        path.write_text("a\nb\nc", encoding="utf-8")
        servetool.logs_cmd("qwen", lines=2)
        out = capsys.readouterr().out
        assert "b" in out
        assert "a" not in out

    def test_bench_output(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(core, "require_state", lambda name, home=None: _state())
        monkeypatch.setattr(core, "process_alive", lambda pid, ct=0.0: True)
        monkeypatch.setattr(core, "bench", lambda *a, **k: BenchResult(1.5, 10, 30, 20.0))
        servetool.bench_cmd()
        out = capsys.readouterr().out
        assert "20.00 tok/s" in out
        assert "30" in out

    def test_bench_not_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(core, "require_state", lambda name, home=None: _state())
        monkeypatch.setattr(core, "process_alive", lambda pid, ct=0.0: False)
        with pytest.raises(ServeError, match="未在运行"):
            servetool.bench_cmd()

    def test_env_output(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        state = _state()
        core.save_state(state, tmp_path)
        assert run_tool("servetool", ["env", "--shell", "powershell"]) == 0
        out = capsys.readouterr().out
        assert '$env:OPENAI_BASE_URL="http://127.0.0.1:8080/v1"' in out

        servetool.env_cmd(shell="bash")
        out = capsys.readouterr().out
        assert 'export OPENAI_MODEL="qwen3-coder-30b-a3b"' in out

        servetool.env_cmd(shell="plain")
        out = capsys.readouterr().out
        assert f"OPENAI_API_KEY={DEFAULT_API_KEY}" in out

    def test_run_tool_env_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path / "home"))
        with pytest.raises(ServeError, match="未启动"):
            servetool.env_cmd()
