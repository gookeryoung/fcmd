"""llm 子模块核心逻辑测试。

覆盖：
- 状态文件读写与损坏处理、名称校验
- 模型定位、显存估算、GPU 探测
- 引擎解析（env / tag / current / 扫描 / PATH）、安装与注册、切换
- 健康检查、日志、启动（假进程 + 假健康检查）、停止、进程存活校验
- bench / client_env / doctor 体检
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import zipfile
from collections import namedtuple
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from fcmd.apps.locllm import (
    _locllmw_bench,
    llmbase,
    llmbench,
    llmconvert,
    llmdoctor,
    llmengine,
    llmenv,
    llmmodel,
    llmserve,
    locllmw,
)
from fcmd.apps.locllm.llmbase import (
    DEFAULT_API_KEY,
    DEFAULT_PORT,
    PROFILES,
    CommandResult,
    EngineInfo,
    GpuInfo,
    LaunchPlan,
    ProfileName,
    ServeError,
    ServeState,
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


def _fail_opener(request: Request, timeout: float = 2.0) -> Any:
    """模拟网络不可用。"""
    raise URLError("offline")


@pytest.fixture
def fast_source_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """源码包下载在测试中不等待重试间隔。"""
    monkeypatch.setattr(llmconvert, "_SOURCE_ATTEMPTS", 1)
    monkeypatch.setattr(llmconvert, "_SOURCE_RETRY_DELAY", 0.0)


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
    (directory / llmengine.engine_exe_name()).write_text("", encoding="utf-8")
    return directory


def _plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path) -> LaunchPlan:
    """构造可用启动计划（引擎走 FCMD_LLAMA_SERVER）。"""
    engine = _engine_dir(tmp_path) / llmengine.engine_exe_name()
    monkeypatch.setenv("FCMD_LLAMA_SERVER", str(engine))
    return llmserve.make_plan("qwen", home=home, model=model_file)


class TestHelpers:
    def test_human_size(self) -> None:
        assert llmbase.human_size(0) == "0 B"
        assert llmbase.human_size(2048) == "2.00 KB"
        assert llmbase.human_size(3 * 1024**3) == "3.00 GB"

    def test_format_returncode(self) -> None:
        assert llmbase.format_returncode(0) == "0"
        rendered = llmbase.format_returncode(-1073741819)
        assert "0xC0000005" in rendered

    def test_client_host_and_url(self) -> None:
        assert llmbase.client_host("0.0.0.0") == "127.0.0.1"
        assert llmbase.client_host("127.0.0.1") == "127.0.0.1"
        assert llmbase.http_base_url("0.0.0.0", 8080) == "http://127.0.0.1:8080"

    def test_validate_name(self) -> None:
        assert llmbase.validate_name("qwen-1.0_x") == "qwen-1.0_x"
        for bad in ("../x", "-x", "", "a/b"):
            with pytest.raises(ServeError, match="服务名不合法"):
                llmbase.validate_name(bad)

    def test_format_command(self) -> None:
        assert llmbase.format_command(["a b", "c"]) == "'a b' c"

    def test_run_command(self) -> None:
        result = llmbase.run_command([sys.executable, "-c", "print('ok')"])
        assert result.returncode == 0
        assert "ok" in result.stdout

    def test_run_command_timeout(self) -> None:
        result = llmbase.run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.01)
        assert result.returncode == -1
        assert "超时" in result.stderr

    def test_run_command_missing(self) -> None:
        result = llmbase.run_command(["definitely-not-a-command-fcmd"])
        assert result.returncode == -1

    def test_profile_table(self) -> None:
        assert set(PROFILES) == {"speed", "balanced", "long", "max-context"}
        assert PROFILES["balanced"].quant == "Q5_K_M"
        assert PROFILES["balanced"].kv_type == "f16"
        assert PROFILES["speed"].ubatch == 2048
        assert PROFILES["long"].kv_type == "q8_0"
        assert PROFILES["max-context"].context == 196608


# ---------------------------------------------------------------------- #
# 状态文件
# ---------------------------------------------------------------------- #
class TestState:
    def test_roundtrip(self, home: Path) -> None:
        state = _state()
        path = llmenv.save_state(state, home)
        assert path.is_file()
        assert llmenv.load_state("qwen", home) == state
        assert llmenv.require_state("qwen", home) == state
        assert llmenv.list_states(home) == [state]
        assert llmenv.delete_state("qwen", home) is True
        assert llmenv.delete_state("qwen", home) is False

    def test_missing(self, home: Path) -> None:
        assert llmenv.load_state("qwen", home, missing_ok=True) is None
        with pytest.raises(ServeError, match="未启动"):
            llmenv.load_state("qwen", home)
        with pytest.raises(ServeError, match="未启动"):
            llmenv.require_state("qwen", home)

    def test_corrupted(self, home: Path) -> None:
        path = llmenv.state_path("qwen", home)
        path.parent.mkdir(parents=True)
        path.write_text("{bad", encoding="utf-8")
        with pytest.raises(ServeError, match="状态文件损坏"):
            llmenv.load_state("qwen", home)

    def test_list_skips_corrupted(self, home: Path) -> None:
        llmenv.save_state(_state("good"), home)
        bad = llmenv.state_path("bad", home)
        bad.write_text("{bad", encoding="utf-8")
        assert [s.name for s in llmenv.list_states(home)] == ["good"]

    def test_list_missing_dir(self, home: Path) -> None:
        assert llmenv.list_states(home) == []

    def test_paths(self, home: Path) -> None:
        assert llmenv.serve_dir(home) == home / "serve"
        assert llmenv.log_path("qwen", home) == home / "serve" / "logs" / "qwen.log"
        assert llmenv.state_path("qwen", home) == home / "serve" / "qwen.json"
        assert llmenv.engines_root(home) == home / "engines" / "llama.cpp"

    def test_fcmd_home_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path / "custom"))
        assert llmenv.fcmd_home() == tmp_path / "custom"

    def test_state_url_and_env(self) -> None:
        state = _state(host="0.0.0.0", port=9000)
        assert state.base_url == "http://127.0.0.1:9000"
        env = llmenv.client_env(state)
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
        assert llmmodel.find_model("Q5_K_M", model=model_file) == model_file.resolve()

    def test_explicit_non_gguf(self, tmp_path: Path) -> None:
        path = tmp_path / "model.bin"
        path.write_text("", encoding="utf-8")
        with pytest.raises(ServeError, match=r"不是 \.gguf"):
            llmmodel.find_model("Q5_K_M", model=path)

    def test_explicit_missing(self, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="模型路径不存在"):
            llmmodel.find_model("Q5_K_M", model=tmp_path / "nope.gguf")

    def test_explicit_dir(self, model_file: Path) -> None:
        assert llmmodel.find_model("Q5_K_M", model=model_file.parent) == model_file.resolve()

    def test_models_root_search_ignores_mmproj(self, model_file: Path) -> None:
        mmproj = model_file.parent / "mmproj-Q5_K_M.gguf"
        mmproj.write_bytes(b"y" * 99999)
        assert llmmodel.find_model("Q5_K_M", models_root=model_file.parent.parent) == model_file.resolve()

    def test_no_match_lists_available(self, model_file: Path) -> None:
        with pytest.raises(ServeError, match="未找到量化 Q8_0"):
            llmmodel.find_model("Q8_0", models_root=model_file.parent.parent)

    def test_missing_root(self, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="模型目录不存在"):
            llmmodel.find_model("Q5_K_M", models_root=tmp_path / "nope")

    def test_default_models_dir_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_MODELS", str(tmp_path / "m"))
        assert llmenv.models_dir() == tmp_path / "m"

    def test_estimate_vram(self) -> None:
        f16 = llmmodel.estimate_vram(10, 65536, "f16")
        q8 = llmmodel.estimate_vram(10, 65536, "q8_0")
        assert llmmodel.estimate_vram(10, 0, "q8_0") == 10 + llmbase.VRAM_OVERHEAD_BYTES
        assert q8 < f16
        assert llmmodel.estimate_vram(10, 65536, "unknown") == f16

    def test_plan_vram(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        expected = llmmodel.estimate_vram(model_file.stat().st_size, plan.context, plan.effective_kv_type)
        assert llmmodel.plan_vram(plan) == expected
        assert plan.base_url == "http://127.0.0.1:8080"


# ---------------------------------------------------------------------- #
# 模型目录配置与扫描
# ---------------------------------------------------------------------- #
class TestModelsConfig:
    def test_config_path(self, home: Path) -> None:
        assert llmenv.config_path(home) == home / "serve" / "config.json"

    def test_load_missing(self, home: Path) -> None:
        assert llmenv.load_config(home) == {}

    def test_load_corrupt(self, home: Path) -> None:
        path = llmenv.config_path(home)
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        assert llmenv.load_config(home) == {}
        path.write_text("[]", encoding="utf-8")
        assert llmenv.load_config(home) == {}

    def test_save_and_load(self, home: Path) -> None:
        written = llmenv.save_config({"models_dir": "D:/models"}, home)
        assert written == llmenv.config_path(home)
        assert llmenv.load_config(home) == {"models_dir": "D:/models"}

    def test_configured_models_dir(self, home: Path, tmp_path: Path) -> None:
        target = tmp_path / "models"
        llmenv.set_models_dir(target, home)
        assert llmenv.configured_models_dir(home) == target

    def test_configured_models_dir_ignores_invalid(self, home: Path) -> None:
        llmenv.save_config({"models_dir": "   "}, home)
        assert llmenv.configured_models_dir(home) is None

    def test_legacy_multi_roots_keep_first_and_record_extras(self, home: Path, tmp_path: Path) -> None:
        first, second = tmp_path / "a", tmp_path / "b"
        llmenv.save_config({"models_roots": [str(first), str(second)]}, home)
        assert llmenv.models_dir(home=home) == first
        assert llmenv.models_dir_extra(home) == [str(second)]

    def test_models_dir_precedence(self, monkeypatch: pytest.MonkeyPatch, home: Path, tmp_path: Path) -> None:
        configured = tmp_path / "configured"
        llmenv.set_models_dir(configured, home)
        assert llmenv.models_dir(home=home) == configured
        assert llmenv.models_dir(tmp_path / "explicit", home) == tmp_path / "explicit"
        monkeypatch.setenv("FCMD_MODELS", str(tmp_path / "env"))
        assert llmenv.models_dir(home=home) == tmp_path / "env"
        assert llmenv.models_dir(tmp_path / "explicit", home) == tmp_path / "explicit"

    def test_models_dir_default(self, monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
        monkeypatch.delenv("FCMD_MODELS", raising=False)
        assert llmenv.models_dir(home=home) == llmenv.default_models_dir()

    def test_set_models_dir_replaces(self, home: Path) -> None:
        a = home.parent / "a"
        b = home.parent / "b"
        assert llmenv.set_models_dir(a, home) == a
        assert llmenv.set_models_dir(b, home) == b
        assert llmenv.configured_models_dir(home) == b

    def test_set_models_dir_none_clears(self, home: Path) -> None:
        llmenv.set_models_dir(home.parent / "a", home)
        assert llmenv.set_models_dir(None, home) == llmenv.default_models_dir()
        assert llmenv.configured_models_dir(home) is None


class TestScanModels:
    def test_scans_single_root_and_profiles(self, tmp_path: Path) -> None:
        root = tmp_path / "models"
        small = root / "repo" / "Model-Q4_K_M.gguf"
        large = root / "Model-Q5_K_M.gguf"
        for path, payload in ((small, b"a" * 10), (large, b"b" * 100)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

        entries = llmmodel.scan_models(root)
        assert [entry.path for entry in entries] == [large.resolve(), small.resolve()]
        assert entries[0].profiles == ("balanced", "long")
        assert entries[1].profiles == ("speed", "max-context")
        assert entries[0].relative == "Model-Q5_K_M.gguf"
        assert entries[1].relative == str(Path("repo") / "Model-Q4_K_M.gguf")

    def test_ignores_mmproj_and_missing_root(self, tmp_path: Path) -> None:
        root = tmp_path / "models"
        root.mkdir()
        (root / "mmproj-Q5_K_M.gguf").write_bytes(b"x")
        assert llmmodel.scan_models(root) == []
        assert llmmodel.scan_models(tmp_path / "missing") == []

    def test_uses_configured_models_dir(self, home: Path, tmp_path: Path) -> None:
        root = tmp_path / "models"
        root.mkdir()
        (root / "m-Q5_K_M.gguf").write_bytes(b"x")
        llmenv.set_models_dir(root, home)
        assert [entry.path for entry in llmmodel.scan_models(home=home)] == [(root / "m-Q5_K_M.gguf").resolve()]

    def test_find_model_uses_configured_dir(self, tmp_path: Path, home: Path) -> None:
        root = tmp_path / "models"
        root.mkdir()
        target = root / "m-Q5_K_M.gguf"
        target.write_bytes(b"b" * 100)
        llmenv.set_models_dir(root, home)
        assert llmmodel.find_model("Q5_K_M", home=home) == target.resolve()


# ---------------------------------------------------------------------- #
# safetensors → GGUF 转换
# ---------------------------------------------------------------------- #
def _hf_source(tmp_path: Path) -> Path:
    """构造最小可转换 HF safetensors 目录（含架构声明与令牌器）。"""
    source = tmp_path / "hf" / "Qwen3-Coder-30B-A3B-Instruct"
    source.mkdir(parents=True)
    (source / "config.json").write_text('{"architectures": ["Qwen3MoeForCausalLM"]}', encoding="utf-8")
    (source / "tokenizer.json").write_text("{}", encoding="utf-8")
    (source / "model-00001-of-00001.safetensors").write_bytes(b"x" * 32)
    return source


def _conv_runner(calls: list[list[str]], code: int = 0, stderr: str = "") -> Any:
    """假 Runner：按命令行产出的路径写文件。"""

    def _run(cmd: list[str], timeout: float = 1.0, env: dict[str, str] | None = None) -> CommandResult:
        calls.append(list(cmd))
        if code == 0:
            target = Path(cmd[cmd.index("--outfile") + 1]) if "--outfile" in cmd else Path(cmd[2])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"gguf")
        return CommandResult(code, "", stderr)

    return _run


class TestConvert:
    def test_source_root(self, home: Path) -> None:
        root = llmconvert.convert_source_root("b1", home)
        assert root == home / "engines" / "llama.cpp" / "b1" / "llama.cpp"

    def test_ensure_script_explicit(self, tmp_path: Path) -> None:
        script = tmp_path / "convert.py"
        script.write_text("", encoding="utf-8")
        assert llmconvert.ensure_convert_script(script=script) == script

    def test_ensure_script_explicit_missing(self, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="转换脚本不存在"):
            llmconvert.ensure_convert_script(script=tmp_path / "nope.py")

    def test_ensure_script_accepts_local_zip(self, tmp_path: Path) -> None:
        """--script 指向本地源码 zip 时解压使用，压缩包保留。"""
        archive = tmp_path / "llama.cpp.zip"
        archive.write_bytes(_zip_payload({"convert_hf_to_gguf.py": b"# from zip"}))
        script = llmconvert.ensure_convert_script(tag="b9", script=archive, home=tmp_path / "home")
        assert script.read_text(encoding="utf-8") == "# from zip"
        assert archive.is_file()
        assert script.parent.name == "llama.cpp"

    def test_ensure_script_rejects_bad_zip(self, tmp_path: Path) -> None:
        archive = tmp_path / "bad.zip"
        archive.write_bytes(b"broken")
        with pytest.raises(ServeError, match="不是可用的"):
            llmconvert.ensure_convert_script(tag="b9", script=archive, home=tmp_path / "home")

    def test_ensure_script_missing_zip(self, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="源码压缩包不存在"):
            llmconvert.ensure_convert_script(tag="b9", script=tmp_path / "nope.zip", home=tmp_path / "home")

    def test_ensure_script_from_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        script = tmp_path / "convert.py"
        script.write_text("", encoding="utf-8")
        monkeypatch.setenv("FCMD_CONVERT_SCRIPT", str(script))
        assert llmconvert.ensure_convert_script(home=tmp_path / "home") == script

    def test_ensure_script_uses_checkout(self, monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
        monkeypatch.delenv("FCMD_CONVERT_SCRIPT", raising=False)
        cached = llmconvert.convert_source_root("b7", home) / llmconvert.CONVERT_SCRIPT
        cached.parent.mkdir(parents=True)
        cached.write_text("# cached", encoding="utf-8")
        assert llmconvert.ensure_convert_script(tag="b7", home=home) == cached

    def test_ensure_script_clones(self, monkeypatch: pytest.MonkeyPatch, home: Path, fast_source_retry: None) -> None:
        monkeypatch.delenv("FCMD_CONVERT_SCRIPT", raising=False)
        calls: list[list[str]] = []

        def _run(cmd: list[str], timeout: float = 1.0, env: dict[str, str] | None = None) -> CommandResult:
            calls.append(list(cmd))
            script = Path(cmd[-1]) / llmconvert.CONVERT_SCRIPT
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("# cloned", encoding="utf-8")
            return CommandResult(0)

        script = llmconvert.ensure_convert_script(tag="b9", home=home, runner=_run, opener=cast(Any, _fail_opener))
        assert script.read_text(encoding="utf-8") == "# cloned"
        assert calls[0][:3] == ["git", "clone", "--depth"]
        assert calls[0][calls[0].index("--branch") + 1] == "b9"
        assert calls[0][-2] == llmconvert.CONVERT_REPO_URL

    def test_ensure_script_clone_master_for_custom_tag(
        self, monkeypatch: pytest.MonkeyPatch, home: Path, fast_source_retry: None
    ) -> None:
        monkeypatch.delenv("FCMD_CONVERT_SCRIPT", raising=False)
        calls: list[list[str]] = []

        def _run(cmd: list[str], timeout: float = 1.0, env: dict[str, str] | None = None) -> CommandResult:
            calls.append(list(cmd))
            script = Path(cmd[-1]) / llmconvert.CONVERT_SCRIPT
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("# cloned", encoding="utf-8")
            return CommandResult(0)

        llmconvert.ensure_convert_script(tag="lmstudio-2.71", home=home, runner=_run, opener=cast(Any, _fail_opener))
        assert calls[0][calls[0].index("--branch") + 1] == "master"

    def test_ensure_script_clone_failure(
        self, monkeypatch: pytest.MonkeyPatch, home: Path, fast_source_retry: None
    ) -> None:
        monkeypatch.delenv("FCMD_CONVERT_SCRIPT", raising=False)
        with pytest.raises(ServeError, match="--script"):
            llmconvert.ensure_convert_script(
                tag="b9",
                home=home,
                runner=lambda cmd, timeout=1.0, env=None: CommandResult(128, "", "boom"),
                opener=cast(Any, _fail_opener),
            )

    def test_source_archive_urls(self) -> None:
        tag_urls = llmconvert._source_archive_urls("b12212")
        assert tag_urls[0] == "https://codeload.github.com/ggml-org/llama.cpp/zip/refs/tags/b12212"
        assert tag_urls[1] == "https://github.com/ggml-org/llama.cpp/archive/refs/tags/b12212.zip"
        master_urls = llmconvert._source_archive_urls("lmstudio-2.71")
        assert all("refs/heads/master" in url for url in master_urls)

    def test_source_archive_ready(self, tmp_path: Path) -> None:
        nested = tmp_path / "nested.zip"
        nested.write_bytes(_zip_payload({"llama.cpp-b9/convert_hf_to_gguf.py": b""}))
        flat = tmp_path / "flat.zip"
        flat.write_bytes(_zip_payload({"convert_hf_to_gguf.py": b"", "gguf-py/gguf/__init__.py": b""}))
        missing = tmp_path / "missing.zip"
        missing.write_bytes(_zip_payload({"llama.cpp-b9/README.md": b""}))
        broken = tmp_path / "broken.zip"
        broken.write_bytes(b"not a zip")
        assert llmconvert._source_archive_ready(nested)
        assert llmconvert._source_archive_ready(flat)
        assert not llmconvert._source_archive_ready(missing)
        assert not llmconvert._source_archive_ready(broken)

    def test_extract_source_archive_flat(self, tmp_path: Path) -> None:
        """扁平打包（无顶层目录）的源码包也能解压到 repo。"""
        archive = tmp_path / "flat.zip"
        archive.write_bytes(_zip_payload({"convert_hf_to_gguf.py": b"# flat", "conversion/__init__.py": b""}))
        repo = tmp_path / "llama.cpp"
        assert llmconvert._extract_source_archive(archive, repo)
        assert (repo / llmconvert.CONVERT_SCRIPT).read_text(encoding="utf-8") == "# flat"
        assert (repo / "conversion" / "__init__.py").is_file()

    def test_local_source_archives_prefers_tag(self, home: Path) -> None:
        tag_dir = llmenv.engines_root(home) / "b9"
        tag_dir.mkdir(parents=True)
        other = tag_dir / "llama.cpp-b8.zip"
        exact = tag_dir / "llama.cpp-b9.zip"
        plain = tag_dir / "llama.cpp.zip"
        for item in (other, exact, plain):
            item.write_bytes(b"x")
        found = llmconvert._local_source_archives("b9", tag_dir / "llama.cpp")
        assert found[:3] == [exact, plain, other]

    def test_ensure_script_uses_local_archive(
        self, monkeypatch: pytest.MonkeyPatch, home: Path, fast_source_retry: None
    ) -> None:
        """引擎目录已有可用 llama.cpp*.zip 时直接解压，不下载也不 clone。"""
        monkeypatch.delenv("FCMD_CONVERT_SCRIPT", raising=False)
        tag_dir = llmenv.engines_root(home) / "b9"
        tag_dir.mkdir(parents=True)
        archive = tag_dir / "llama.cpp.zip"
        archive.write_bytes(_zip_payload({"convert_hf_to_gguf.py": b"# local", "gguf-py/gguf/__init__.py": b""}))
        script = llmconvert.ensure_convert_script(
            tag="b9",
            home=home,
            runner=cast(Any, lambda *args, **kwargs: pytest.fail("不应 clone")),
            opener=cast(Any, lambda *args, **kwargs: pytest.fail("不应下载")),
        )
        assert script.read_text(encoding="utf-8") == "# local"
        assert archive.is_file()

    def test_ensure_script_ignores_broken_local_archive(
        self, monkeypatch: pytest.MonkeyPatch, home: Path, fast_source_retry: None
    ) -> None:
        """损坏的本地 zip 不阻止回退下载。"""
        monkeypatch.delenv("FCMD_CONVERT_SCRIPT", raising=False)
        tag_dir = llmenv.engines_root(home) / "b9"
        tag_dir.mkdir(parents=True)
        (tag_dir / "llama.cpp.zip").write_bytes(b"broken")
        payload = _zip_payload({"convert_hf_to_gguf.py": b"# downloaded"})
        script = llmconvert.ensure_convert_script(tag="b9", home=home, opener=_opener(payload))
        assert script.read_text(encoding="utf-8") == "# downloaded"

    def test_download_resumable_retries_with_range(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """首个连接被重置后从 .part 断点续传，而不是整包重下。"""
        monkeypatch.setattr(llmconvert, "_SOURCE_RETRY_DELAY", 0.0)
        dest = tmp_path / "src.zip"
        dest.with_name("src.zip.part").write_bytes(b"head")
        seen: list[Request] = []
        calls = {"count": 0}

        def _open(request: Request, timeout: float = 30.0) -> Any:
            calls["count"] += 1
            if calls["count"] == 1:
                raise URLError("Recv failure")
            seen.append(request)
            return _FakeResponse(b"tail", 206)

        size = llmconvert._download_resumable("https://example.com/src.zip", dest, cast(Any, _open))
        assert size == len(b"headtail")
        assert dest.read_bytes() == b"headtail"
        assert seen[0].get_header("Range") == "bytes=4-"
        assert not dest.with_name("src.zip.part").exists()

    def test_download_resumable_gives_up(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(llmconvert, "_SOURCE_ATTEMPTS", 2)
        monkeypatch.setattr(llmconvert, "_SOURCE_RETRY_DELAY", 0.0)
        dest = tmp_path / "src.zip"
        with pytest.raises(ServeError, match="重试 2 次"):
            llmconvert._download_resumable("https://example.com/src.zip", dest, cast(Any, _fail_opener))
        assert not dest.exists()
        assert not dest.with_name("src.zip.part").exists()

    def test_ensure_script_downloads_archive(self, monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
        """无本地源码时优先下载源码包，不执行 git clone。"""
        monkeypatch.delenv("FCMD_CONVERT_SCRIPT", raising=False)
        payload = _zip_payload(
            {
                "llama.cpp-b9/convert_hf_to_gguf.py": b"# downloaded",
                "llama.cpp-b9/gguf-py/gguf/__init__.py": b"",
            }
        )
        script = llmconvert.ensure_convert_script(
            tag="b9",
            home=home,
            runner=cast(Any, lambda *args, **kwargs: pytest.fail("不应 clone")),
            opener=_opener(payload),
        )
        assert script == llmconvert.convert_source_root("b9", home) / llmconvert.CONVERT_SCRIPT
        assert script.read_text(encoding="utf-8") == "# downloaded"
        assert not (script.parent.parent / "llama.cpp-b9.zip").exists()

    def test_ensure_script_archive_without_script_falls_back(
        self, monkeypatch: pytest.MonkeyPatch, home: Path, fast_source_retry: None
    ) -> None:
        """源码包缺少转换脚本时回退 git clone。"""
        monkeypatch.delenv("FCMD_CONVERT_SCRIPT", raising=False)
        calls: list[list[str]] = []

        def _run(cmd: list[str], timeout: float = 1.0, env: dict[str, str] | None = None) -> CommandResult:
            calls.append(list(cmd))
            script = Path(cmd[-1]) / llmconvert.CONVERT_SCRIPT
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("# cloned", encoding="utf-8")
            return CommandResult(0)

        payload = _zip_payload({"llama.cpp-b9/README.md": b"nope"})
        script = llmconvert.ensure_convert_script(tag="b9", home=home, runner=_run, opener=_opener(payload))
        assert script.read_text(encoding="utf-8") == "# cloned"
        assert calls

    def test_ensure_script_clears_partial_checkout(
        self, monkeypatch: pytest.MonkeyPatch, home: Path, fast_source_retry: None
    ) -> None:
        """残留的不完整源码目录在 clone 前清理，避免 git 报目录已存在。"""
        monkeypatch.delenv("FCMD_CONVERT_SCRIPT", raising=False)
        repo = llmconvert.convert_source_root("b9", home)
        repo.mkdir(parents=True)
        (repo / "partial.txt").write_text("x", encoding="utf-8")
        seen: list[bool] = []

        def _run(cmd: list[str], timeout: float = 1.0, env: dict[str, str] | None = None) -> CommandResult:
            seen.append(repo.exists())
            script = Path(cmd[-1]) / llmconvert.CONVERT_SCRIPT
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("# cloned", encoding="utf-8")
            return CommandResult(0)

        llmconvert.ensure_convert_script(tag="b9", home=home, runner=_run, opener=cast(Any, _fail_opener))
        assert seen == [False]

    def test_is_hf_dir(self, tmp_path: Path) -> None:
        source = _hf_source(tmp_path)
        assert llmconvert.is_hf_safetensors_dir(source)
        assert not llmconvert.is_hf_safetensors_dir(tmp_path / "absent")
        no_safetensors = tmp_path / "plain"
        no_safetensors.mkdir()
        (no_safetensors / "config.json").write_text("{}", encoding="utf-8")
        assert not llmconvert.is_hf_safetensors_dir(no_safetensors)

    def test_quantize_exe_from_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        binary = tmp_path / "llama-quantize"
        binary.write_text("", encoding="utf-8")
        monkeypatch.setenv("FCMD_LLAMA_QUANTIZE", str(binary))
        assert llmengine.quantize_exe(EngineInfo("t", tmp_path / "llama-server")) == binary

    def test_quantize_exe_env_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_LLAMA_QUANTIZE", str(tmp_path / "absent"))
        with pytest.raises(ServeError, match="FCMD_LLAMA_QUANTIZE"):
            llmengine.quantize_exe(EngineInfo("t", tmp_path / "llama-server"))

    def test_quantize_exe_beside_engine(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("FCMD_LLAMA_QUANTIZE", raising=False)
        binary = tmp_path / llmengine.engine_exe_name("llama-quantize")
        binary.write_text("", encoding="utf-8")
        assert llmengine.quantize_exe(EngineInfo("t", tmp_path / llmengine.engine_exe_name())) == binary
        binary.unlink()
        assert llmengine.quantize_exe(EngineInfo("t", tmp_path / llmengine.engine_exe_name())) is None

    def test_plan_defaults(self, tmp_path: Path) -> None:
        source = _hf_source(tmp_path)
        script = tmp_path / "convert.py"
        script.write_text("", encoding="utf-8")
        plan = llmconvert.plan_convert(source, script=script, python="py-test", models_root=tmp_path / "models")
        assert plan.output == (tmp_path / "models" / "converted" / source.name / f"{source.name}.gguf").resolve()
        assert plan.quant is None
        assert plan.outtype == "f16"
        assert plan.intermediate == str(plan.output)
        assert plan.convert_argv() == [
            "py-test",
            str(script),
            str(source.resolve()),
            "--outfile",
            str(plan.output),
            "--outtype",
            "f16",
        ]

    def test_plan_custom_output(self, tmp_path: Path) -> None:
        source = _hf_source(tmp_path)
        script = tmp_path / "convert.py"
        script.write_text("", encoding="utf-8")
        plan = llmconvert.plan_convert(source, output=tmp_path / "x" / "m.GGUF", outtype="q8_0", script=script)
        assert plan.output.name == "m.GGUF"
        assert plan.outtype == "q8_0"

    def test_plan_rejects_bad_inputs(self, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="不是 HF safetensors 目录"):
            llmconvert.plan_convert(tmp_path / "absent")
        source = _hf_source(tmp_path)
        script = tmp_path / "convert.py"
        script.write_text("", encoding="utf-8")
        with pytest.raises(ServeError, match="不支持的 outtype"):
            llmconvert.plan_convert(source, outtype="f64", script=script)
        with pytest.raises(ServeError, match=r"必须是 \.gguf"):
            llmconvert.plan_convert(source, output=tmp_path / "m.bin", script=script)

    def test_plan_quant_needs_quantize(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = _hf_source(tmp_path)
        script = tmp_path / "convert.py"
        script.write_text("", encoding="utf-8")
        engine = _engine_dir(tmp_path) / llmengine.engine_exe_name()
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(engine))
        monkeypatch.delenv("FCMD_LLAMA_QUANTIZE", raising=False)
        with pytest.raises(ServeError, match="llama-quantize"):
            llmconvert.plan_convert(source, quant="Q4_K_M", script=script, models_root=tmp_path / "models")

    def test_plan_quant(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = _hf_source(tmp_path)
        script = tmp_path / "convert.py"
        script.write_text("", encoding="utf-8")
        engine_dir = _engine_dir(tmp_path)
        binary = engine_dir / llmengine.engine_exe_name("llama-quantize")
        binary.write_text("", encoding="utf-8")
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(engine_dir / llmengine.engine_exe_name()))
        monkeypatch.delenv("FCMD_LLAMA_QUANTIZE", raising=False)
        plan = llmconvert.plan_convert(source, quant="Q4_K_M", script=script, models_root=tmp_path / "models")
        assert plan.output.name == f"{source.name}-Q4_K_M.gguf"
        assert plan.quantize_exe == binary
        assert plan.intermediate == str(plan.output.with_name(f"{plan.output.stem}-f16.gguf"))
        assert plan.quantize_argv() == [str(binary), plan.intermediate, str(plan.output), "Q4_K_M"]

    def test_find_convert_python_ok(self) -> None:
        calls: list[list[str]] = []

        def _run(cmd: list[str], timeout: float = 1.0, env: dict[str, str] | None = None) -> CommandResult:
            calls.append(list(cmd))
            return CommandResult(0)

        assert llmconvert.find_convert_python(_run) == calls[0][0]

    def test_find_convert_python_missing(self) -> None:
        with pytest.raises(ServeError, match="--python"):
            llmconvert.find_convert_python(lambda cmd, timeout=1.0, env=None: CommandResult(1, "", "no module"))

    def test_quantize_argv_requires_quant(self, tmp_path: Path) -> None:
        plan = llmconvert.ConvertPlan(
            source=tmp_path,
            output=tmp_path / "m.gguf",
            outtype="f16",
            script=tmp_path / "c.py",
            python="py",
        )
        with pytest.raises(ServeError, match="未指定量化类型"):
            plan.quantize_argv()

    def test_convert_model_success(self, tmp_path: Path) -> None:
        plan = llmconvert.ConvertPlan(
            source=_hf_source(tmp_path),
            output=tmp_path / "out" / "m.gguf",
            outtype="f16",
            script=tmp_path / "c.py",
            python="py",
        )
        calls: list[list[str]] = []
        assert llmconvert.convert_model(plan, _conv_runner(calls)) == [plan.output]
        assert plan.output.is_file()
        assert len(calls) == 1

    def test_convert_model_failure(self, tmp_path: Path) -> None:
        plan = llmconvert.ConvertPlan(
            source=_hf_source(tmp_path),
            output=tmp_path / "out" / "m.gguf",
            outtype="f16",
            script=tmp_path / "c.py",
            python="py",
        )
        calls: list[list[str]] = []
        with pytest.raises(ServeError, match="safetensors 转 GGUF 失败"):
            llmconvert.convert_model(plan, _conv_runner(calls, code=1, stderr="line1\nline2"))

    def test_convert_model_missing_dependency_hint(self, tmp_path: Path) -> None:
        plan = llmconvert.ConvertPlan(
            source=_hf_source(tmp_path),
            output=tmp_path / "out" / "m.gguf",
            outtype="f16",
            script=tmp_path / "c.py",
            python="py",
        )
        runner = _conv_runner([], code=1, stderr="ModuleNotFoundError: No module named 'torch'")
        with pytest.raises(ServeError, match="--python"):
            llmconvert.convert_model(plan, runner)

    def test_convert_model_quantizes(self, tmp_path: Path) -> None:
        plan = llmconvert.ConvertPlan(
            source=_hf_source(tmp_path),
            output=tmp_path / "out" / "m-Q4_K_M.gguf",
            outtype="f16",
            script=tmp_path / "c.py",
            python="py",
            quant="Q4_K_M",
            quantize_exe=tmp_path / "llama-quantize",
        )
        calls: list[list[str]] = []
        assert llmconvert.convert_model(plan, _conv_runner(calls)) == [plan.output]
        assert plan.output.is_file()
        assert not Path(plan.intermediate).exists()
        assert len(calls) == 2

    def test_convert_model_missing_intermediate(self, tmp_path: Path) -> None:
        plan = llmconvert.ConvertPlan(
            source=_hf_source(tmp_path),
            output=tmp_path / "out" / "m-Q4_K_M.gguf",
            outtype="f16",
            script=tmp_path / "c.py",
            python="py",
            quant="Q4_K_M",
            quantize_exe=tmp_path / "llama-quantize",
        )
        with pytest.raises(ServeError, match="未生成中间文件"):
            llmconvert.convert_model(plan, lambda cmd, timeout=1.0, env=None: CommandResult(0))

    def test_convert_model_quantize_failure(self, tmp_path: Path) -> None:
        plan = llmconvert.ConvertPlan(
            source=_hf_source(tmp_path),
            output=tmp_path / "out" / "m-Q4_K_M.gguf",
            outtype="f16",
            script=tmp_path / "c.py",
            python="py",
            quant="Q4_K_M",
            quantize_exe=tmp_path / "llama-quantize",
        )
        calls: list[list[str]] = []
        inner = _conv_runner(calls)
        state = {"n": 0}

        def _run(cmd: list[str], timeout: float = 1.0, env: dict[str, str] | None = None) -> CommandResult:
            state["n"] += 1
            return inner(cmd, timeout) if state["n"] == 1 else CommandResult(2, "", "quant boom")

        with pytest.raises(ServeError, match="量化失败"):
            llmconvert.convert_model(plan, _run)

    def test_convert_model_quantize_crash_hint(self, tmp_path: Path) -> None:
        """0xC0000005 且无输出时提示 VC++ 运行库问题。"""
        plan = llmconvert.ConvertPlan(
            source=_hf_source(tmp_path),
            output=tmp_path / "out" / "m-Q4_K_M.gguf",
            outtype="f16",
            script=tmp_path / "c.py",
            python="py",
            quant="Q4_K_M",
            quantize_exe=tmp_path / "engine" / "llama-quantize.exe",
        )
        calls: list[list[str]] = []
        inner = _conv_runner(calls)
        state = {"n": 0}

        def _run(cmd: list[str], timeout: float = 1.0, env: dict[str, str] | None = None) -> CommandResult:
            state["n"] += 1
            return inner(cmd, timeout) if state["n"] == 1 else CommandResult(0xC0000005)

        with pytest.raises(ServeError, match="MSVCP140"):
            llmconvert.convert_model(plan, _run)

    def test_convert_model_missing_output(self, tmp_path: Path) -> None:
        plan = llmconvert.ConvertPlan(
            source=_hf_source(tmp_path),
            output=tmp_path / "out" / "m-Q4_K_M.gguf",
            outtype="f16",
            script=tmp_path / "c.py",
            python="py",
            quant="Q4_K_M",
            quantize_exe=tmp_path / "llama-quantize",
        )
        intermediate = Path(plan.intermediate)
        intermediate.parent.mkdir(parents=True, exist_ok=True)
        intermediate.write_bytes(b"x")
        with pytest.raises(ServeError, match="未生成"):
            llmconvert.convert_model(plan, lambda cmd, timeout=1.0, env=None: CommandResult(0))


# ---------------------------------------------------------------------- #
# GPU / 引擎
# ---------------------------------------------------------------------- #
class TestGpu:
    def test_detect(self) -> None:
        gpu = llmengine.detect_gpu(lambda cmd: CommandResult(0, "NVIDIA RTX 5090 D, 32607\n"))
        assert gpu == GpuInfo("NVIDIA RTX 5090 D", 32607)

    def test_detect_failures(self) -> None:
        assert llmengine.detect_gpu(lambda cmd: CommandResult(1, "", "err")) is None
        assert llmengine.detect_gpu(lambda cmd: CommandResult(0, "")) is None
        assert llmengine.detect_gpu(lambda cmd: CommandResult(0, "bad line")) is None
        assert llmengine.detect_gpu(lambda cmd: CommandResult(0, "name, not-a-number")) is None

    def test_detect_caches_default_runner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake(cmd: list[str]) -> CommandResult:
            calls.append(cmd)
            return CommandResult(0, "NVIDIA RTX 5090 D, 32607\n")

        monkeypatch.setattr(llmengine, "run_command", fake)
        monkeypatch.setattr(llmengine, "_gpu_cache", llmengine._GpuCache())
        first = llmengine.detect_gpu(fake)
        second = llmengine.detect_gpu(fake)
        assert first == second == GpuInfo("NVIDIA RTX 5090 D", 32607)
        assert len(calls) == 1

    def test_detect_cache_expires(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake(cmd: list[str]) -> CommandResult:
            calls.append(cmd)
            return CommandResult(0, "NVIDIA RTX 5090 D, 32607\n")

        monkeypatch.setattr(llmengine, "run_command", fake)
        monkeypatch.setattr(llmengine, "_gpu_cache", llmengine._GpuCache())
        monkeypatch.setattr(llmengine, "_GPU_CACHE_TTL_SECONDS", 0.0)
        assert llmengine.detect_gpu(fake) == GpuInfo("NVIDIA RTX 5090 D", 32607)
        assert llmengine.detect_gpu(fake) == GpuInfo("NVIDIA RTX 5090 D", 32607)
        assert len(calls) == 2


class TestEngine:
    def test_engine_exe_name(self) -> None:
        expected = "llama-server.exe" if os.name == "nt" else "llama-server"
        assert llmengine.engine_exe_name() == expected

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        exe = tmp_path / llmengine.engine_exe_name()
        exe.write_text("", encoding="utf-8")
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(exe))
        info = llmengine.resolve_engine(home=tmp_path / "home")
        assert info.tag == "env"
        assert info.exe == exe

    def test_env_override_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(tmp_path / "nope"))
        with pytest.raises(ServeError, match="FCMD_LLAMA_SERVER"):
            llmengine.resolve_engine(home=tmp_path / "home")

    def test_explicit_tag(self, home: Path) -> None:
        directory = llmenv.engines_root(home) / "b1"
        directory.mkdir(parents=True)
        (directory / llmengine.engine_exe_name()).write_text("", encoding="utf-8")
        info = llmengine.resolve_engine("b1", home)
        assert info.tag == "b1"

    def test_explicit_tag_missing(self, home: Path) -> None:
        with pytest.raises(ServeError, match="未安装"):
            llmengine.resolve_engine("nope", home)

    def test_current_pointer(self, home: Path) -> None:
        directory = llmenv.engines_root(home) / "b2"
        directory.mkdir(parents=True)
        (directory / llmengine.engine_exe_name()).write_text("", encoding="utf-8")
        llmengine.use_engine("b2", home)
        assert llmengine.resolve_engine(home=home).tag == "b2"

    def test_current_pointer_broken_falls_back(self, home: Path) -> None:
        root = llmenv.engines_root(home)
        root.mkdir(parents=True)
        (root / "current.json").write_text(json.dumps({"tag": "gone"}), encoding="utf-8")
        (root / "current.json").write_text("not json", encoding="utf-8")
        directory = root / "b1"
        directory.mkdir()
        (directory / llmengine.engine_exe_name()).write_text("", encoding="utf-8")
        assert llmengine.resolve_engine(home=home).tag == "b1"

    def test_latest_scan(self, home: Path) -> None:
        for tag in ("b1", "b2"):
            directory = llmenv.engines_root(home) / tag
            directory.mkdir(parents=True)
            (directory / llmengine.engine_exe_name()).write_text("", encoding="utf-8")
        assert llmengine.resolve_engine(home=home).tag == "b2"

    def test_path_fallback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        exe = tmp_path / llmengine.engine_exe_name()
        exe.write_text("", encoding="utf-8")
        monkeypatch.delenv("FCMD_LLAMA_SERVER", raising=False)
        monkeypatch.setattr(llmengine.shutil, "which", lambda name: str(exe))
        info = llmengine.resolve_engine(home=tmp_path / "home")
        assert info.tag == "system"

    def test_not_found(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("FCMD_LLAMA_SERVER", raising=False)
        monkeypatch.setattr(llmengine.shutil, "which", lambda name: None)
        with pytest.raises(ServeError, match=r"未找到 llama\.cpp 引擎"):
            llmengine.resolve_engine(home=tmp_path / "home")

    def test_marker_absolute_exe(self, home: Path, tmp_path: Path) -> None:
        target = llmenv.engines_root(home) / "b3"
        target.mkdir(parents=True)
        exe = tmp_path / "elsewhere" / llmengine.engine_exe_name()
        exe.parent.mkdir()
        exe.write_text("", encoding="utf-8")
        (target / "engine.json").write_text(json.dumps({"exe": str(exe)}), encoding="utf-8")
        assert llmengine.resolve_engine("b3", home).exe == exe

    def test_marker_broken_falls_back_to_direct(self, home: Path) -> None:
        target = llmenv.engines_root(home) / "b4"
        target.mkdir(parents=True)
        (target / "engine.json").write_text("not json", encoding="utf-8")
        exe = target / llmengine.engine_exe_name()
        exe.write_text("", encoding="utf-8")
        assert llmengine.resolve_engine("b4", home).exe == exe

    def test_marker_non_dict_and_bad_prepend(self, home: Path) -> None:
        target = llmenv.engines_root(home) / "b5"
        target.mkdir(parents=True)
        exe = target / llmengine.engine_exe_name()
        exe.write_text("", encoding="utf-8")
        (target / "engine.json").write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        assert llmengine.resolve_engine("b5", home).path_prepend == ()
        (target / "engine.json").write_text(json.dumps({"path_prepend": "not-a-list"}), encoding="utf-8")
        assert llmengine.resolve_engine("b5", home).path_prepend == ()

    def test_asset_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(llmengine.os, "name", "nt")
        assert llmengine.engine_asset_names("b1", "cuda") == [
            "llama-b1-bin-win-cuda-13.5-x64.zip",
            "cudart-llama-bin-win-cuda-13.5-x64.zip",
        ]
        assert llmengine.engine_asset_names("b1", "cpu") == ["llama-b1-bin-win-cpu-x64.zip"]

    def test_asset_names_non_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(llmengine.os, "name", "posix")
        with pytest.raises(ServeError, match="非 Windows"):
            llmengine.engine_asset_names("b1")

    def test_install_cached(self, home: Path) -> None:
        directory = llmenv.engines_root(home) / "b1"
        directory.mkdir(parents=True)
        (directory / llmengine.engine_exe_name()).write_text("", encoding="utf-8")
        info = llmengine.install_engine("b1", home=home, opener=cast(Any, lambda *a: pytest.fail("不应下载")))
        assert info.tag == "b1"

    def test_install_from_path(self, home: Path, tmp_path: Path) -> None:
        source = _engine_dir(tmp_path)
        info = llmengine.install_engine("local", path=source, home=home)
        assert info.exe == source / llmengine.engine_exe_name()
        assert (llmenv.engines_root(home) / "local" / "engine.json").is_file()

    def test_install_path_detects_vendor(self, home: Path, tmp_path: Path) -> None:
        source = _engine_dir(tmp_path)
        vendor = tmp_path / "vendor" / "cuda13"
        vendor.mkdir(parents=True)
        (vendor / "cudart64_13.dll").write_text("", encoding="utf-8")
        info = llmengine.install_engine("lmstudio", path=source, home=home)
        assert info.path_prepend == (str(vendor.resolve()),)
        assert llmengine.resolve_engine("lmstudio", home).path_prepend == info.path_prepend

    def test_install_path_without_vendor(self, home: Path, tmp_path: Path) -> None:
        source = _engine_dir(tmp_path)
        assert llmengine.install_engine("plain", path=source, home=home).path_prepend == ()

    def test_install_from_missing_path(self, home: Path, tmp_path: Path) -> None:
        with pytest.raises(ServeError, match="引擎目录不存在"):
            llmengine.install_engine("local", path=tmp_path / "nope", home=home)

    def test_install_path_without_exe(self, home: Path, tmp_path: Path) -> None:
        source = tmp_path / "empty"
        source.mkdir()
        with pytest.raises(ServeError, match="找不到"):
            llmengine.install_engine("local", path=source, home=home)

    def test_install_from_local_zip(self, home: Path, tmp_path: Path) -> None:
        """--path 指向本地 zip 时直接解压，不联网且保留压缩包。"""
        archive = tmp_path / "engine.zip"
        archive.write_bytes(_zip_payload({f"bundle/{llmengine.engine_exe_name()}": b"exe"}))
        info = llmengine.install_engine(
            "local", path=archive, home=home, opener=cast(Any, lambda *a: pytest.fail("不应下载"))
        )
        assert info.exe.is_file()
        assert info.source == str(archive)
        assert archive.is_file()

    def test_install_local_zip_without_exe(self, home: Path, tmp_path: Path) -> None:
        archive = tmp_path / "empty.zip"
        archive.write_bytes(_zip_payload({"readme.txt": b"nope"}))
        with pytest.raises(ServeError, match="压缩包内找不到"):
            llmengine.install_engine("local", path=archive, home=home)

    def test_install_rejects_non_zip_file(self, home: Path, tmp_path: Path) -> None:
        archive = tmp_path / "engine.7z"
        archive.write_bytes(b"x")
        with pytest.raises(ServeError, match="仅支持"):
            llmengine.install_engine("local", path=archive, home=home)

    def test_install_reuses_existing_archive(self, home: Path) -> None:
        """目标目录内已有完整 zip 时不再重新下载。"""
        target = llmenv.engines_root(home) / "b9"
        target.mkdir(parents=True)
        (target / "engine.zip").write_bytes(_zip_payload({llmengine.engine_exe_name(): b"exe"}))
        info = llmengine.install_engine(
            "b9", url="https://example.com/engine.zip", home=home, opener=cast(Any, lambda *a: pytest.fail("不应下载"))
        )
        assert info.exe.is_file()

    def test_install_redownloads_invalid_archive(self, home: Path) -> None:
        """残留的不完整 zip 会被重新下载覆盖。"""
        target = llmenv.engines_root(home) / "b9"
        target.mkdir(parents=True)
        (target / "engine.zip").write_bytes(b"partial")
        payload = _zip_payload({llmengine.engine_exe_name(): b"exe"})
        info = llmengine.install_engine("b9", url="https://example.com/engine.zip", home=home, opener=_opener(payload))
        assert info.exe.is_file()

    def test_install_from_url(self, home: Path) -> None:
        payload = _zip_payload({f"bundle/{llmengine.engine_exe_name()}": b"exe"})
        info = llmengine.install_engine("b9", url="https://example.com/engine.zip", home=home, opener=_opener(payload))
        assert info.exe.is_file()
        assert info.source == "https://example.com/engine.zip"

    def test_install_from_url_without_exe(self, home: Path) -> None:
        payload = _zip_payload({"readme.txt": b"nope"})
        with pytest.raises(ServeError, match="找不到"):
            llmengine.install_engine("b9", url="https://example.com/engine.zip", home=home, opener=_opener(payload))

    def test_download_failure(self, home: Path) -> None:
        def _boom(request: Request, timeout: float = 1.0) -> Any:
            raise URLError("offline")

        with pytest.raises(ServeError, match="下载失败"):
            llmengine.install_engine("b9", url="https://example.com/engine.zip", home=home, opener=_boom)

    def test_bad_zip(self, home: Path) -> None:
        with pytest.raises(ServeError, match="解压失败"):
            llmengine.install_engine("b9", url="https://example.com/engine.zip", home=home, opener=_opener(b"not zip"))

    def test_list_and_use(self, home: Path) -> None:
        assert llmengine.list_engines(home) == []
        for tag in ("b1", "b2"):
            directory = llmenv.engines_root(home) / tag
            directory.mkdir(parents=True)
            (directory / llmengine.engine_exe_name()).write_text("", encoding="utf-8")
        assert [info.tag for info in llmengine.list_engines(home)] == ["b2", "b1"]
        llmengine.use_engine("b1", home)
        listed = {info.tag: info.source for info in llmengine.list_engines(home)}
        assert listed["b1"] == "current"

    def test_use_missing(self, home: Path) -> None:
        with pytest.raises(ServeError, match="未安装"):
            llmengine.use_engine("nope", home)

    def test_check_engine(self) -> None:
        info = EngineInfo("b1", Path("llama-server.exe"))
        ok = llmengine.check_engine(info, lambda cmd: CommandResult(0, "b12211\n"))
        assert ok.status == "ok"
        bad = llmengine.check_engine(info, lambda cmd: CommandResult(-1073741819, "", "crash"))
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
        assert plan.log == llmenv.log_path("qwen", home)
        argv = plan.argv()
        assert argv[0] == str(plan.engine.exe)
        for flag, value in (("-c", "65536"), ("-ctk", "f16"), ("-ub", "2048"), ("--port", "8080")):
            index = argv.index(flag)
            assert argv[index + 1] == value
        assert "--jinja" in argv
        assert "--api-key" in argv

    def test_alias_defaults_to_model_stem(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path
    ) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        assert plan.alias == model_file.stem
        argv = plan.argv()
        assert argv[argv.index("--alias") + 1] == model_file.stem

    def test_alias_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path
    ) -> None:
        engine = _engine_dir(tmp_path) / llmengine.engine_exe_name()
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(engine))
        plan = llmserve.make_plan("qwen", home=home, model=model_file, alias="gemma-4-e4b")
        assert plan.alias == "gemma-4-e4b"
        argv = plan.argv()
        assert argv[argv.index("--alias") + 1] == "gemma-4-e4b"

    def test_ctx_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path) -> None:
        engine = _engine_dir(tmp_path) / llmengine.engine_exe_name()
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(engine))
        plan = llmserve.make_plan("qwen", "speed", home=home, model=model_file, ctx=4096, port=9001)
        assert plan.context == 4096
        assert plan.port == 9001
        assert plan.profile.ubatch == 2048

    def test_kv_and_ubatch_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path
    ) -> None:
        engine = _engine_dir(tmp_path) / llmengine.engine_exe_name()
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(engine))
        plan = llmserve.make_plan("qwen", "balanced", home=home, model=model_file, kv_type="q8_0", ubatch=512)
        assert plan.effective_kv_type == "q8_0"
        assert plan.effective_ubatch == 512
        argv = plan.argv()
        assert argv[argv.index("-ctk") + 1] == "q8_0"
        assert argv[argv.index("-ctv") + 1] == "q8_0"
        assert argv[argv.index("-ub") + 1] == "512"
        assert llmmodel.plan_vram(plan) == llmmodel.estimate_vram(model_file.stat().st_size, plan.context, "q8_0")

    def test_invalid_kv_type(self, home: Path, model_file: Path) -> None:
        with pytest.raises(ServeError, match="不支持的 KV"):
            llmserve.make_plan(home=home, model=model_file, kv_type="q8")

    def test_invalid_ubatch(self, home: Path, model_file: Path) -> None:
        with pytest.raises(ServeError, match="ubatch"):
            llmserve.make_plan(home=home, model=model_file, ubatch=0)

    def test_unknown_profile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path
    ) -> None:
        engine = _engine_dir(tmp_path) / llmengine.engine_exe_name()
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(engine))
        with pytest.raises(ServeError, match="未知档位"):
            llmserve.make_plan(profile=cast(ProfileName, "turbo"), home=home, model=model_file)

    def test_invalid_port(self, home: Path, model_file: Path) -> None:
        with pytest.raises(ServeError, match="端口不合法"):
            llmserve.make_plan(home=home, model=model_file, port=70000)

    def test_validation_order(self, home: Path) -> None:
        with pytest.raises(ServeError, match="服务名不合法"):
            llmserve.make_plan("../bad", home=home)


class TestHealth:
    def test_ok(self) -> None:
        assert llmserve.check_health("127.0.0.1", 8080, "k", opener=_opener(b'{"status":"ok"}')) is True

    def test_http_error(self) -> None:
        def _boom(request: Request, timeout: float = 1.0) -> Any:
            raise HTTPError(request.full_url, 500, "err", cast(Any, None), None)

        assert llmserve.check_health("127.0.0.1", 8080, None, opener=_boom) is False

    def test_network_error(self) -> None:
        def _boom(request: Request, timeout: float = 1.0) -> Any:
            raise URLError("offline")

        assert llmserve.check_health("127.0.0.1", 8080, "k", opener=_boom) is False

    def test_non_200(self) -> None:
        assert llmserve.check_health("127.0.0.1", 8080, None, opener=_opener(b"", status=503)) is False


_METRICS_TEXT = """# HELP llamacpp:prompt_tokens_total Number of prompt tokens processed.
# TYPE llamacpp:prompt_tokens_total counter
llamacpp:prompt_tokens_total 120
llamacpp:tokens_predicted_total{model="m"} 340
llamacpp:requests_processing 1
llamacpp:requests_deferred 2
llamacpp:kv_cache_usage_ratio 0.25
llamacpp:other_metric not-a-number
"""


class TestMetrics:
    def test_parse_prometheus(self) -> None:
        values = llmserve.parse_prometheus(_METRICS_TEXT)
        assert values["llamacpp:prompt_tokens_total"] == 120
        assert values["llamacpp:tokens_predicted_total"] == 340
        assert "llamacpp:other_metric" not in values

    def test_parse_metrics(self) -> None:
        metrics = llmserve.parse_metrics(_METRICS_TEXT)
        assert metrics == llmserve.ServeMetrics(
            prompt_tokens=120.0,
            predicted_tokens=340.0,
            processing=1.0,
            deferred=2.0,
            kv_usage=0.25,
        )

    def test_parse_metrics_missing(self) -> None:
        assert llmserve.parse_metrics("") == llmserve.ServeMetrics()

    def test_fetch_metrics(self) -> None:
        seen: list[Request] = []
        metrics = llmserve.fetch_metrics(
            "127.0.0.1", 8080, "sk-test", opener=_opener(_METRICS_TEXT.encode(), seen=seen)
        )
        assert metrics.predicted_tokens == 340.0
        assert seen[0].full_url.endswith("/metrics")
        assert seen[0].get_header("Authorization") == "Bearer sk-test"

    def test_fetch_network_error(self) -> None:
        with pytest.raises(ServeError, match="读取性能指标失败"):
            llmserve.fetch_metrics("127.0.0.1", 8080, None, opener=_fail_opener)

    def test_fetch_non_200(self) -> None:
        with pytest.raises(ServeError, match="HTTP 503"):
            llmserve.fetch_metrics("127.0.0.1", 8080, None, opener=_opener(b"", status=503))


class TestProcess:
    def test_alive_current_process(self) -> None:
        pid = os.getpid()
        assert llmserve.process_alive(pid) is True
        assert llmserve.process_alive(pid, llmserve.process_create_time(pid)) is True

    def test_create_time_mismatch(self) -> None:
        pid = os.getpid()
        assert llmserve.process_alive(pid, llmserve.process_create_time(pid) + 100) is False

    def test_missing_process(self) -> None:
        assert llmserve.process_alive(2**31 - 1) is False
        assert llmserve.process_create_time(2**31 - 1) == 0.0

    def test_terminate_real_child(self) -> None:
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            llmserve._terminate(proc.pid, llmserve.process_create_time(proc.pid), 5.0)
            assert proc.poll() is not None
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(5)

    def test_terminate_reused_pid(self) -> None:
        pid = os.getpid()
        with pytest.raises(ServeError, match="复用"):
            llmserve._terminate(pid, llmserve.process_create_time(pid) + 100, 1.0)

    def test_terminate_missing_process(self) -> None:
        llmserve._terminate(2**31 - 1, 0.0, 1.0)


class TestLaunch:
    def test_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        state = llmserve.launch(
            plan,
            home=home,
            popen=lambda argv, **kwargs: _FakeProc(os.getpid()),
            health=lambda host, port, key: True,
            sleep=lambda _: None,
        )
        assert state.pid == os.getpid()
        assert state.model == str(model_file.resolve())
        assert llmenv.load_state("qwen", home) == state
        assert plan.log.is_file()

    def test_early_exit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        with pytest.raises(ServeError, match="立即退出"):
            llmserve.launch(
                plan,
                home=home,
                popen=lambda argv, **kwargs: _FakeProc(os.getpid(), 2),
                health=lambda host, port, key: False,
                sleep=lambda _: None,
            )

    def test_timeout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        terminated: list[int] = []
        monkeypatch.setattr(llmserve, "_terminate", lambda pid, ct, t: terminated.append(pid))
        with pytest.raises(ServeError, match="健康检查超时"):
            llmserve.launch(
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
            llmserve.launch(plan, home=home, popen=_boom, health=lambda host, port, key: True)

    def test_launch_prepends_engine_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path
    ) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        injected = replace(plan, engine=replace(plan.engine, path_prepend=("C:/vendor/cuda13",)))
        captured: dict[str, Any] = {}

        def _popen(argv: list[str], **kwargs: Any) -> Any:
            captured.update(kwargs)
            return _FakeProc(os.getpid())

        llmserve.launch(
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
        llmenv.save_state(
            _state(pid=pid, create_time=llmserve.process_create_time(pid)),
            home,
        )
        with pytest.raises(ServeError, match="已在运行"):
            llmserve.start_server(plan, home=home)

    def test_start_cleans_stale_and_checks_ports(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path
    ) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        llmenv.save_state(_state(pid=2**31 - 1), home)
        monkeypatch.setattr("fcmd.infras.net.is_port_in_use", lambda port, host: False)
        state = llmserve.start_server(
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
            llmserve.start_server(plan, home=home, port_in_use=lambda port, host: True)


class TestStop:
    def test_not_running(self, home: Path) -> None:
        llmenv.save_state(_state(pid=2**31 - 1), home)
        result = llmserve.stop_server("qwen", home, alive=lambda pid, ct: False)
        assert result.was_running is False
        assert llmenv.load_state("qwen", home, missing_ok=True) is None

    def test_running(self, home: Path) -> None:
        llmenv.save_state(_state(), home)
        killed: list[int] = []
        result = llmserve.stop_server(
            "qwen", home, alive=lambda pid, ct: True, terminator=lambda pid, ct, timeout: killed.append(pid)
        )
        assert result.was_running is True
        assert killed == [12345]
        assert llmenv.load_state("qwen", home, missing_ok=True) is None

    def test_missing(self, home: Path) -> None:
        with pytest.raises(ServeError, match="未启动"):
            llmserve.stop_server("qwen", home)


class TestLogAndBench:
    def test_read_log_tail(self, home: Path) -> None:
        path = llmenv.log_path("qwen", home)
        path.parent.mkdir(parents=True)
        path.write_text("\n".join(f"line{i}" for i in range(5)), encoding="utf-8")
        assert llmserve.read_log("qwen", 2, home) == "line3\nline4"

    def test_read_log_missing(self, home: Path) -> None:
        with pytest.raises(ServeError, match="日志不存在"):
            llmserve.read_log("qwen", home=home)

    def test_bench_usage(self) -> None:
        payload = json.dumps(
            {"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 20}}
        ).encode()
        seen: list[Request] = []
        result = llmbench.bench(_state().endpoint, opener=_opener(payload, seen=seen))
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 20
        assert result.tokens_per_s > 0
        assert seen[0].full_url.endswith("/v1/chat/completions")

    def test_bench_without_usage(self) -> None:
        payload = json.dumps({"choices": [{"message": {"content": "abcdef"}}]}).encode()
        result = llmbench.bench(_state().endpoint, opener=_opener(payload))
        assert result.completion_tokens == 3
        assert result.prompt_tokens == 0

    def test_bench_empty_choice(self) -> None:
        result = llmbench.bench(_state().endpoint, opener=_opener(b"{}"))
        assert result.completion_tokens == 0

    def test_bench_prefers_decode_timing(self) -> None:
        payload = json.dumps(
            {
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 30000, "completion_tokens": 20},
                "timings": {"predicted_per_second": 152.5},
            }
        ).encode()
        result = llmbench.bench(_state().endpoint, opener=_opener(payload))
        assert result.tokens_per_s == 152.5

    def test_bench_prompt_tokens(self) -> None:
        text = llmbench.make_bench_prompt(30000)
        assert len(text) > 100000
        assert llmbench.make_bench_prompt(0) == llmbench.DEFAULT_PROMPT

        payload = json.dumps({"usage": {"prompt_tokens": 30000, "completion_tokens": 10}}).encode()
        seen: list[Request] = []
        result = llmbench.bench(_state().endpoint, prompt_tokens=30000, opener=_opener(payload, seen=seen))
        body = json.loads(cast(bytes, seen[0].data))
        assert len(body["messages"][0]["content"]) > 100000
        assert result.prompt_tokens == 30000

    def test_bench_http_error(self) -> None:
        def _boom(request: Request, timeout: float = 1.0) -> Any:
            raise HTTPError(request.full_url, 500, "err", cast(Any, None), None)

        with pytest.raises(ServeError, match="请求失败"):
            llmbench.bench(_state().endpoint, opener=_boom)

    def test_bench_bad_json(self) -> None:
        with pytest.raises(ServeError, match="合法 JSON"):
            llmbench.bench(_state().endpoint, opener=_opener(b"not json"))


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
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(_engine_dir(tmp_path) / llmengine.engine_exe_name()))
        monkeypatch.setattr("fcmd.infras.net.is_port_in_use", lambda port, host: False)
        monkeypatch.setattr(llmengine.shutil, "disk_usage", lambda path: DiskUsage(100, 10, 90 * 1024**3))
        monkeypatch.setattr(llmdoctor, "_file_version", lambda dll: (14, 51, 0, 0))
        checks = llmdoctor.doctor(home=home, models_root=model_file.parent.parent, runner=_doctor_runner)
        statuses = {check.name: check.status for check in checks}
        assert all(status == "ok" for status in statuses.values())
        assert "显存预算" in statuses

    def test_problems(self, monkeypatch: pytest.MonkeyPatch, home: Path, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(tmp_path / "nope" / llmengine.engine_exe_name()))
        monkeypatch.setattr("fcmd.infras.net.is_port_in_use", lambda port, host: True)
        monkeypatch.setattr(llmengine.shutil, "disk_usage", lambda path: DiskUsage(100, 99, 1024))
        checks = llmdoctor.doctor(
            home=home, models_root=tmp_path / "empty", runner=lambda cmd: CommandResult(1, "", "err")
        )
        statuses = {check.name: check.status for check in checks}
        assert statuses["GPU"] == "warn"
        assert statuses["CUDA Toolkit"] == "warn"
        assert statuses["推理引擎"] == "fail"
        assert statuses["模型"] == "warn"
        assert statuses["端口"] == "warn"
        assert statuses["磁盘"] == "warn"

    def test_engine_crash(self, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(_engine_dir(tmp_path) / llmengine.engine_exe_name()))

        def _runner(cmd: list[str] | Any) -> CommandResult:
            if cmd[0] == "nvidia-smi":
                return CommandResult(1, "", "no gpu")
            if cmd[0] == "nvcc":
                return CommandResult(1, "", "no nvcc")
            return CommandResult(-1073741819, "", "crash")

        checks = llmdoctor.doctor(home=home, models_root=tmp_path / "empty", port=9999, runner=_runner)
        engine = next(check for check in checks if check.name == "推理引擎")
        assert engine.status == "fail"
        assert "0xC0000005" in engine.detail

    def test_writable_failure(self, tmp_path: Path) -> None:
        occupied = tmp_path / "occupied"
        occupied.write_text("x", encoding="utf-8")
        check = llmdoctor._check_writable(occupied)
        assert check.status == "fail"

    def test_port_owned_by_service(self, monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
        pid = os.getpid()
        llmenv.save_state(_state(pid=pid, create_time=llmserve.process_create_time(pid), port=9188), home)
        monkeypatch.setattr("fcmd.infras.net.is_port_in_use", lambda port, host: True)
        check = llmdoctor._check_port(9188, home)
        assert check.status == "ok"
        assert "qwen" in check.detail

    def test_disk_error(self, monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
        def _boom(path: Path) -> Any:
            raise OSError("no disk")

        monkeypatch.setattr(llmengine.shutil, "disk_usage", _boom)
        check = llmdoctor._check_disk(None, home)
        assert check.status == "warn"


# ---------------------------------------------------------------------- #
# 拆分后子模块的分支补测
# ---------------------------------------------------------------------- #
class TestServeBranches:
    def test_process_alive_not_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import psutil

        class Proc:
            def is_running(self) -> bool:
                return False

        monkeypatch.setattr(psutil, "Process", lambda pid: Proc())
        assert llmserve.process_alive(123) is False

    def test_process_alive_zombie(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import psutil

        class Proc:
            def is_running(self) -> bool:
                return True

            def status(self) -> str:
                return psutil.STATUS_ZOMBIE

        monkeypatch.setattr(psutil, "Process", lambda pid: Proc())
        assert llmserve.process_alive(123) is False

    def test_process_alive_access_denied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import psutil

        def _raise(pid: int) -> None:
            raise psutil.AccessDenied(pid)

        monkeypatch.setattr(psutil, "Process", _raise)
        assert llmserve.process_alive(123) is True

    def test_terminate_access_denied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import psutil

        def _raise(pid: int) -> None:
            raise psutil.AccessDenied(pid)

        monkeypatch.setattr(psutil, "Process", _raise)
        with pytest.raises(ServeError, match="权限不足"):
            llmserve._terminate(123, 0.0, 0.1)

    def test_terminate_ignores_vanished_children(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import psutil

        class Child:
            def terminate(self) -> None:
                raise psutil.NoSuchProcess(2)

        class Proc:
            def create_time(self) -> float:
                return 0.0

            def children(self, recursive: bool) -> list[Child]:
                return [Child()]

            def terminate(self) -> None:
                raise psutil.NoSuchProcess(1)

        monkeypatch.setattr(psutil, "Process", lambda pid: Proc())
        llmserve._terminate(1, 0.0, 0.1)

    def test_terminate_ignores_killed_survivors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import psutil

        class Proc:
            def create_time(self) -> float:
                return 0.0

            def children(self, recursive: bool) -> list[Any]:
                return []

            def terminate(self) -> None:
                return None

        class Survivor:
            def kill(self) -> None:
                raise psutil.NoSuchProcess(1)

        monkeypatch.setattr(psutil, "Process", lambda pid: Proc())
        monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout: ([], [Survivor()]))
        llmserve._terminate(1, 0.0, 0.1)

    def test_tail_text_missing_file(self, tmp_path: Path) -> None:
        assert llmserve._tail_text(tmp_path / "missing.log") == ""

    def test_creation_flags_on_posix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace

        monkeypatch.setattr(llmserve, "os", SimpleNamespace(name="posix"))
        assert llmserve._creation_flags() == 0

    def test_default_popen_delegates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _popen(argv: list[str], **kwargs: Any) -> str:
            captured.update({"argv": argv, **kwargs})
            return "proc"

        monkeypatch.setattr(llmserve.subprocess, "Popen", _popen)
        assert llmserve._default_popen(["llama-server"], cwd=".") == "proc"
        assert captured["argv"] == ["llama-server"]

    def test_launch_polls_until_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path
    ) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        clock = iter([0.0, 0.0, 5.0])
        monkeypatch.setattr(llmserve.time, "monotonic", lambda: next(clock))
        monkeypatch.setattr(llmserve, "_terminate", lambda pid, ct, timeout: None)
        slept: list[float] = []
        with pytest.raises(ServeError, match="健康检查超时"):
            llmserve.launch(
                plan,
                home=home,
                wait_s=1,
                popen=lambda argv, **kwargs: _FakeProc(os.getpid()),
                health=lambda host, port, key: False,
                sleep=slept.append,
            )
        assert slept == [1.0]

    def test_launch_exits_immediately(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path
    ) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        with pytest.raises(ServeError, match="立即退出"):
            llmserve.launch(
                plan,
                home=home,
                wait_s=1,
                popen=lambda argv, **kwargs: _FakeProc(os.getpid(), returncode=1),
                health=lambda host, port, key: True,
                sleep=lambda seconds: None,
            )


class TestDoctorBranches:
    def test_port_occupied_by_other_process(self, monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
        llmenv.save_state(_state(pid=1, port=9999), home)
        monkeypatch.setattr("fcmd.infras.net.is_port_in_use", lambda port, host: True)
        check = llmdoctor._check_port(DEFAULT_PORT, home)
        assert check.status == "warn"

    def test_file_version_non_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace

        monkeypatch.setattr(llmdoctor, "os", SimpleNamespace(name="posix"))
        assert llmdoctor._file_version(Path("x.dll")) is None

    def test_file_version_without_windll(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import ctypes

        monkeypatch.delattr(ctypes, "windll", raising=False)
        assert llmdoctor._file_version(Path("x.dll")) is None

    def test_file_version_query_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import ctypes

        class Version:
            def __init__(self, size: int = 0, info: bool = False, query: bool = False, boom: bool = False) -> None:
                self.size = size
                self.info = info
                self.query = query
                self.boom = boom

            def GetFileVersionInfoSizeW(self, name: str, handle: Any) -> int:
                if self.boom:
                    raise AttributeError("no version api")
                return self.size

            def GetFileVersionInfoW(self, name: str, handle: int, size: int, buffer: Any) -> bool:
                return self.info

            def VerQueryValueW(self, buffer: Any, sub: str, value: Any, length: Any) -> bool:
                return self.query

        class Windll:
            def __init__(self, version: Version) -> None:
                self.version = version

        for version in (Version(), Version(size=8), Version(size=8, info=True), Version(boom=True)):
            monkeypatch.setattr(ctypes, "windll", Windll(version), raising=False)
            assert llmdoctor._file_version(Path("x.dll")) is None

    def test_msvc_runtime_skips_unknown_versions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        directory = _engine_dir(tmp_path)
        (directory / "msvcp140.dll").write_text("", encoding="utf-8")
        monkeypatch.setattr(llmdoctor, "_file_version", lambda dll: None)
        check = llmdoctor._check_msvc_runtime(EngineInfo("b1", directory / llmengine.engine_exe_name()))
        assert check.status == "warn"

    def test_doctor_without_gpu_but_with_model(
        self, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(_engine_dir(tmp_path) / llmengine.engine_exe_name()))
        monkeypatch.setattr("fcmd.infras.net.is_port_in_use", lambda port, host: False)
        monkeypatch.setattr(llmengine.shutil, "disk_usage", lambda path: DiskUsage(100, 10, 90 * 1024**3))
        checks = llmdoctor.doctor(
            home=home, models_root=model_file.parent.parent, runner=lambda cmd: CommandResult(1, "", "no gpu")
        )
        assert "显存预算" not in {check.name for check in checks}

    def test_doctor_skips_msvc_on_posix(
        self, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(_engine_dir(tmp_path) / llmengine.engine_exe_name()))
        monkeypatch.setattr("fcmd.infras.net.is_port_in_use", lambda port, host: False)
        monkeypatch.setattr(llmengine.shutil, "disk_usage", lambda path: DiskUsage(100, 10, 90 * 1024**3))
        from types import SimpleNamespace

        monkeypatch.setattr(llmdoctor, "os", SimpleNamespace(name="posix"))
        checks = llmdoctor.doctor(home=home, models_root=model_file.parent.parent, runner=_doctor_runner)
        assert "VC++ 运行库" not in {check.name for check in checks}


class TestConvertBranches:
    def test_local_source_archives_skip_missing_folder(self, tmp_path: Path) -> None:
        assert llmconvert._local_source_archives("b1", tmp_path / "missing" / "llama.cpp") == []

    def test_extract_invalid_archive(self, tmp_path: Path) -> None:
        archive = tmp_path / "bad.zip"
        archive.write_text("not a zip", encoding="utf-8")
        assert llmconvert._extract_source_archive(archive, tmp_path / "repo") is False

    def test_plan_convert_quant_needs_engine(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = tmp_path / "hf"
        source.mkdir()
        (source / "config.json").write_text('{"architectures": ["LlamaForCausalLM"]}', encoding="utf-8")
        (source / "tokenizer.json").write_text("{}", encoding="utf-8")
        (source / "model.safetensors").write_bytes(b"x")
        monkeypatch.delenv("FCMD_LLAMA_SERVER", raising=False)
        monkeypatch.setattr(
            llmengine, "resolve_engine", lambda tag=None, home=None: (_ for _ in ()).throw(ServeError("no engine"))
        )
        with pytest.raises(ServeError, match="无法解析引擎"):
            llmconvert.plan_convert(source, quant="Q4_K_M", script=tmp_path / "convert.py", home=tmp_path / "home")

    def test_plan_convert_quant_without_script_reraises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = tmp_path / "hf"
        source.mkdir()
        (source / "config.json").write_text('{"architectures": ["LlamaForCausalLM"]}', encoding="utf-8")
        (source / "tokenizer.json").write_text("{}", encoding="utf-8")
        (source / "model.safetensors").write_bytes(b"x")
        monkeypatch.delenv("FCMD_LLAMA_SERVER", raising=False)
        monkeypatch.setattr(
            llmengine, "resolve_engine", lambda tag=None, home=None: (_ for _ in ()).throw(ServeError("no engine"))
        )
        with pytest.raises(ServeError, match="no engine"):
            llmconvert.plan_convert(source, quant="Q4_K_M", home=tmp_path / "home")

    def test_python_candidates_skips_empty_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", os.pathsep + os.environ.get("PATH", ""))
        assert llmconvert._python_candidates()

    def test_python_candidates_skips_unknown_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", "")
        monkeypatch.setattr(llmconvert.shutil, "which", lambda name: None)
        assert llmconvert._python_candidates() == [sys.executable]

    def test_registered_architectures_rejects_syntax_error(self) -> None:
        assert llmconvert._registered_architectures("def (") == set()

    def test_registered_architectures_ignores_non_register_decorators(self) -> None:
        text = (
            "@register\n"
            "class A:\n"
            "    pass\n"
            "@register('Named')\n"
            "class B:\n"
            "    pass\n"
            "@other.wrap('Ignored')\n"
            "class C:\n"
            "    pass\n"
            "@ModelBase.register('GPT2LMHeadModel')\n"
            "class D:\n"
            "    pass\n"
        )
        assert llmconvert._registered_architectures(text) == {"GPT2LMHeadModel"}

    def test_engine_architectures_skips_unreadable_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = tmp_path / "llama.cpp"
        conversion = repo / "conversion"
        conversion.mkdir(parents=True)
        (conversion / "good.py").write_text('@ModelBase.register("GoodArch")\nclass G:\n    pass\n', encoding="utf-8")
        (conversion / "bad.py").write_text('@ModelBase.register("BadArch")\n', encoding="utf-8")
        original = Path.read_text

        def _read(self: Path, *args: Any, **kwargs: Any) -> str:
            if self.name == "bad.py":
                raise OSError("denied")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", _read)
        registered, mapping = llmconvert._engine_architectures(repo)
        assert registered == {"GoodArch"}
        assert mapping == {}

    def test_check_convert_source_rejects_non_object_config(self, tmp_path: Path) -> None:
        source = tmp_path / "hf"
        source.mkdir()
        (source / "config.json").write_text("[]", encoding="utf-8")
        (source / "tokenizer.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ServeError, match="不是 JSON 对象"):
            llmconvert.check_convert_source(source)

    def test_download_resumable_tolerates_bad_content_length(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Response:
            status = 200
            headers = {"Content-Length": "oops"}

            def __init__(self, payload: bytes) -> None:
                self._payload = payload

            def read(self, size: int = -1) -> bytes:
                payload, self._payload = self._payload, b""
                return payload

            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *args: object) -> bool:
                return False

        monkeypatch.setattr(llmconvert, "_SOURCE_ATTEMPTS", 1)
        monkeypatch.setattr(llmconvert, "_SOURCE_RETRY_DELAY", 0.0)
        dest = tmp_path / "src.zip"
        size = llmconvert._download_resumable(
            "https://example.com/src.zip", dest, lambda request, timeout: _Response(b"data")
        )
        assert size == 4
        assert dest.read_bytes() == b"data"

    def test_download_resumable_retries_truncated_body(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Response:
            status = 200
            headers = {"Content-Length": "100"}

            def __init__(self) -> None:
                self._sent = False

            def read(self, size: int = -1) -> bytes:
                if self._sent:
                    return b""
                self._sent = True
                return b"x" * 10

            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *args: object) -> bool:
                return False

        monkeypatch.setattr(llmconvert, "_SOURCE_ATTEMPTS", 2)
        monkeypatch.setattr(llmconvert, "_SOURCE_RETRY_DELAY", 0.0)
        dest = tmp_path / "src.zip"
        with pytest.raises(ServeError, match="传输中断"):
            llmconvert._download_resumable("https://example.com/src.zip", dest, lambda request, timeout: _Response())
        assert not dest.exists()
        assert not dest.with_name("src.zip.part").exists()

    def test_convert_failure_streamed_points_to_log(self) -> None:
        message = llmconvert._convert_failure(CommandResult(1, "", "ValueError: boom"), streamed=True)
        assert "ValueError: boom" in message
        assert "完整调用栈见上方日志" in message

    def test_convert_model_stream_start_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        plan = llmconvert.ConvertPlan(
            source=_hf_source(tmp_path),
            output=tmp_path / "out" / "m.gguf",
            outtype="f16",
            script=tmp_path / "c.py",
            python="py",
        )

        def _raise(*args: object, **kwargs: object) -> None:
            raise OSError("python missing")

        monkeypatch.setattr(llmbase, "stream_command", _raise)
        with pytest.raises(ServeError, match="无法启动转换脚本"):
            llmconvert.convert_model(plan, on_output=lambda line: None)


class TestConvertPrecheck:
    """转换前置检查与错误呈现：元数据 / 文件 / 架构问题在启动脚本前给出可执行提示。"""

    @staticmethod
    def _source(tmp_path: Path, config: dict[str, Any], *, tokenizer: bool = True) -> Path:
        source = tmp_path / "hf"
        source.mkdir()
        (source / "config.json").write_text(json.dumps(config), encoding="utf-8")
        (source / "model.safetensors").write_bytes(b"x")
        if tokenizer:
            (source / "tokenizer.json").write_text("{}", encoding="utf-8")
        return source

    @staticmethod
    def _repo(tmp_path: Path, registered: tuple[str, ...] = ("GPT2LMHeadModel",)) -> Path:
        repo = tmp_path / "llama.cpp"
        conversion = repo / "conversion"
        conversion.mkdir(parents=True)
        body = "@ModelBase.register(\n" + "".join(f'    "{name}",\n' for name in registered) + ")\nclass M:\n    pass\n"
        (conversion / "gpt2.py").write_text(body, encoding="utf-8")
        (conversion / "__init__.py").write_text('ARCH_NAMES = {\n    "GPT2LMHeadModel": "gpt2",\n}\n', encoding="utf-8")
        return repo

    def test_missing_architectures_suggests_registered_arch(self, tmp_path: Path) -> None:
        source = self._source(tmp_path, {"model_type": "gpt2"})
        with pytest.raises(ServeError) as info:
            llmconvert.check_convert_source(source, self._repo(tmp_path))
        message = str(info.value)
        assert "缺少 architectures" in message
        assert "GPT2LMHeadModel" in message

    def test_unsupported_arch_points_to_engine_upgrade(self, tmp_path: Path) -> None:
        source = self._source(tmp_path, {"architectures": ["MysteryForCausalLM"], "model_type": "mystery"})
        with pytest.raises(ServeError, match="不支持架构 MysteryForCausalLM"):
            llmconvert.check_convert_source(source, self._repo(tmp_path))

    def test_missing_tokenizer_is_reported(self, tmp_path: Path) -> None:
        source = self._source(tmp_path, {"architectures": ["GPT2LMHeadModel"]}, tokenizer=False)
        with pytest.raises(ServeError, match="令牌器"):
            llmconvert.check_convert_source(source, self._repo(tmp_path))

    def test_invalid_config_json(self, tmp_path: Path) -> None:
        source = self._source(tmp_path, {"architectures": ["GPT2LMHeadModel"]})
        (source / "config.json").write_text("{", encoding="utf-8")
        with pytest.raises(ServeError, match="无法读取模型配置"):
            llmconvert.check_convert_source(source)

    def test_plan_convert_blocks_before_script_download(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = self._source(tmp_path, {"model_type": "gpt2"})

        def _unexpected(**kwargs: Any) -> Path:
            raise AssertionError("前置检查失败时不应下载转换脚本")

        monkeypatch.setattr(llmconvert, "ensure_convert_script", _unexpected)
        with pytest.raises(ServeError, match="缺少 architectures"):
            llmconvert.plan_convert(source, home=tmp_path / "home")

    def test_convert_model_leads_with_real_reason(self, tmp_path: Path) -> None:
        source = self._source(tmp_path, {"architectures": ["GPT2LMHeadModel"]})
        plan = llmconvert.ConvertPlan(
            source=source,
            output=tmp_path / "out" / "m.gguf",
            outtype="f16",
            script=tmp_path / "c.py",
            python="py",
        )
        stderr = (
            "Traceback (most recent call last):\n"
            '  File "convert_hf_to_gguf.py", line 247, in main\n'
            "    model_architecture = get_model_architecture(hparams, model_type)\n"
            "    ~~~~^^\n"
            '  File "conversion/base.py", line 3419, in get_model_architecture\n'
            '    raise ValueError("Failed to detect model architecture")\n'
            "ValueError: Failed to detect model architecture"
        )
        with pytest.raises(ServeError) as info:
            llmconvert.convert_model(plan, _conv_runner([], code=1, stderr=stderr))
        message = str(info.value)
        assert message.startswith("safetensors 转 GGUF 失败")
        assert "ValueError: Failed to detect model architecture" in message
        assert "~~~" not in message
        assert message.index("ValueError") < message.index("调用栈")

    def test_root_error_line_skips_caret_noise(self) -> None:
        text = "Traceback (most recent call last):\n  ~~^^\nValueError: boom\n"
        assert llmconvert.root_error_line(text) == "ValueError: boom"
        assert llmconvert.root_error_line("no error here") is None


# ---------------------------------------------------------------------- #
# locllmw 运行监控采样
# ---------------------------------------------------------------------- #
_GPU_CSV = (
    "GPU-aaaa,0,NVIDIA RTX 5090 D,45,20480,32768,60,250.5,575.0,40,2500\n"
    "GPU-bbbb,1,NVIDIA RTX 4090,5,1024,24576,45,30.0,450.0,[N/A],2100\n"
)


def _gpu_runner(gpu_csv: str, apps_csv: str = "", code: int = 0) -> llmbase.Runner:
    """构造 nvidia-smi 替身：按查询类型返回 GPU / 进程显存 CSV。"""

    def run(cmd: list[str], *args: Any, **kwargs: Any) -> CommandResult:
        del args, kwargs
        if any("query-compute-apps" in part for part in cmd):
            return CommandResult(0, apps_csv, "")
        return CommandResult(code, gpu_csv, "")

    return run


class TestServewSamples:
    def test_parse_number(self) -> None:
        assert locllmw._parse_number("12.5") == 12.5
        assert locllmw._parse_number(" [N/A] ") is None
        assert locllmw._parse_number("") is None
        assert locllmw._parse_number("abc") is None

    def test_sample_gpu_matches_process(self) -> None:
        sample = locllmw._sample_gpu(1234, _gpu_runner(_GPU_CSV, "1234,GPU-bbbb,512\n"))
        assert sample is not None
        assert sample.name == "NVIDIA RTX 4090"
        assert sample.index == "1"
        assert sample.process_memory == 512
        assert sample.fan is None

    def test_sample_gpu_falls_back_to_largest_memory(self) -> None:
        sample = locllmw._sample_gpu(999, _gpu_runner(_GPU_CSV))
        assert sample is not None
        assert sample.index == "0"
        assert sample.process_memory is None

    def test_sample_gpu_unavailable(self) -> None:
        assert locllmw._sample_gpu(1, _gpu_runner("", code=1)) is None
        assert locllmw._sample_gpu(1, _gpu_runner("")) is None
        assert locllmw._parse_gpu_rows("bad,row\n") == []

    def test_format_load(self) -> None:
        sample = locllmw.LoadSample(
            cpu=12.34,
            cpu_freq=3200.0,
            memory_used=8 * 1024**3,
            memory_total=32 * 1024**3,
            memory_percent=25.0,
            swap_used=1024**3,
            swap_total=4 * 1024**3,
            process_cpu=5.5,
            process_rss=2 * 1024**3,
            process_threads=12,
            process_uptime=3661.0,
        )
        values = locllmw._format_load(sample)
        assert values["cpu"] == "12.3 %"
        assert values["cpu_freq"] == "3200 MHz"
        assert values["memory"] == "8.00 GB / 32.00 GB（25.0 %）"
        assert values["swap"] == "1.00 GB / 4.00 GB（25.0 %）"
        assert values["proc_cpu"] == "5.5 %"
        assert values["proc_mem"] == "2.00 GB"
        assert values["proc_threads"] == "12"
        assert values["uptime"] == "1h 01m 01s"

    def test_format_load_empty(self) -> None:
        values = locllmw._format_load(locllmw.LoadSample())
        assert values["cpu"] == "-"
        assert values["memory"] == "-"
        assert values["swap"] == "-"

    def test_format_gpu(self) -> None:
        gpu = locllmw.GpuSample(
            name="NVIDIA RTX 5090 D",
            index="0",
            utilization=45.0,
            memory_used=20480.0,
            memory_total=32768.0,
            temperature=60.0,
            power_draw=250.0,
            power_limit=575.0,
            fan=40.0,
            clock_mhz=2500.0,
            process_memory=18432.0,
        )
        values = locllmw._format_gpu(gpu)
        assert values["gpu_name"] == "NVIDIA RTX 5090 D（GPU 0）"
        assert values["gpu_util"] == "45 %"
        assert values["gpu_vram"] == "20.00 GB / 32.00 GB（62.5 %）"
        assert values["gpu_proc_vram"] == "18.00 GB"
        assert values["gpu_temp"] == "60 °C"
        assert values["gpu_power"] == "250 / 575 W"
        assert values["gpu_fan"] == "40 %"
        assert values["gpu_clock"] == "2500 MHz"

    def test_format_gpu_missing(self) -> None:
        values = locllmw._format_gpu(None)
        assert "未检测到" in values["gpu_name"]
        assert values["gpu_util"] == "-"

    def test_format_gpu_partial(self) -> None:
        values = locllmw._format_gpu(locllmw.GpuSample(name="GPU", power_draw=100.0))
        assert values["gpu_vram"] == "-"
        assert values["gpu_power"] == "100 W"
        assert values["gpu_fan"] == "-"

    def test_sample_load_current_process(self) -> None:
        import psutil

        process = psutil.Process(os.getpid())
        process.cpu_percent()
        sample = locllmw._sample_load(os.getpid(), process)
        assert sample.cpu is not None
        assert sample.memory_total > 0
        assert sample.process_rss is not None
        assert sample.process_threads is not None
        assert sample.process_uptime is not None

    def test_sample_load_without_process(self) -> None:
        sample = locllmw._sample_load(2**31 - 1, None)
        assert sample.process_rss is None
        assert sample.process_cpu is None


class TestRateWindow:
    def test_smooths_steady_generation(self) -> None:
        window = locllmw.RateWindow(window=10.0)
        assert window.update(0.0, 0.0, 0.0) == (0.0, 0.0)
        rates = [window.update(float(second), 100.0 * second, 0.0)[0] for second in range(1, 6)]
        assert rates == pytest.approx([100.0] * 5)

    def test_window_decays_after_idle(self) -> None:
        window = locllmw.RateWindow(window=4.0)
        window.update(0.0, 0.0, 0.0)
        window.update(1.0, 100.0, 0.0)
        assert window.update(2.0, 150.0, 0.0)[0] == pytest.approx(75.0)
        assert window.update(3.0, 150.0, 0.0)[0] == pytest.approx(50.0)
        assert window.update(4.0, 150.0, 0.0)[0] == pytest.approx(37.5)
        assert window.update(5.0, 150.0, 0.0)[0] == pytest.approx(12.5)
        assert window.update(6.0, 150.0, 0.0)[0] == 0.0

    def test_counter_reset(self) -> None:
        window = locllmw.RateWindow(window=10.0)
        window.update(0.0, 100.0, 10.0)
        assert window.update(1.0, 200.0, 20.0) == pytest.approx((100.0, 10.0))
        assert window.update(2.0, 5.0, 1.0) == (0.0, 0.0)

    def test_prompt_rate(self) -> None:
        window = locllmw.RateWindow(window=10.0)
        window.update(0.0, 0.0, 0.0)
        gen_rate, prompt_rate = window.update(2.0, 100.0, 400.0)
        assert gen_rate == pytest.approx(50.0)
        assert prompt_rate == pytest.approx(200.0)


class TestServewFitAdvice:
    def test_suggest_smaller_context(self) -> None:
        fit = llmmodel.VramFit(needed=40, available=32, max_context=32 * 1024)
        advice = locllmw._fit_advice(fit, 65536, "f16")
        assert "降到 32K" in advice
        assert "q8_0" in advice

    def test_suggest_smaller_quant_when_weight_too_large(self) -> None:
        fit = llmmodel.VramFit(needed=40, available=32, max_context=0)
        advice = locllmw._fit_advice(fit, 65536, "q8_0")
        assert "仅权重" in advice
        assert "Q4_K_M" in advice
        assert "q8_0" not in advice

    def test_busy_advice_prioritizes_releasing_vram(self) -> None:
        fit = llmmodel.VramFit(needed=40, available=10, max_context=32 * 1024)
        advice = locllmw._busy_advice(fit, 65536, "f16")
        assert "先停止" in advice
        assert "32K" in advice
        assert "q8_0" in advice

    def test_busy_advice_without_room_for_context(self) -> None:
        fit = llmmodel.VramFit(needed=40, available=10, max_context=0)
        assert "更小量化" in locllmw._busy_advice(fit, 65536, "q8_0")


class TestServewFitState:
    def test_unknown_without_gpu(self) -> None:
        assert locllmw._fit_state(None, None) == "-"

    def test_hardware_unsupported(self) -> None:
        total = llmmodel.VramFit(needed=40, available=32, max_context=0)
        free = llmmodel.VramFit(needed=40, available=10, max_context=0)
        assert locllmw._fit_state(total, free) == "硬件不足"

    def test_needs_release(self) -> None:
        total = llmmodel.VramFit(needed=20, available=32, max_context=100000)
        free = llmmodel.VramFit(needed=20, available=10, max_context=20000)
        assert locllmw._fit_state(total, free) == "需释放显存"

    def test_available(self) -> None:
        total = llmmodel.VramFit(needed=20, available=32, max_context=100000)
        assert locllmw._fit_state(total, llmmodel.VramFit(needed=20, available=30, max_context=90000)) == "可用"
        assert locllmw._fit_state(total, None) == "可用"

    def test_state_maps_to_row_tag(self) -> None:
        # 标签必须与中文状态一一对应，否则 Treeview 不会着色
        assert locllmw.LocllmWindow._state_tag("硬件不足") == ("unfit",)
        assert locllmw.LocllmWindow._state_tag("需释放显存") == ("busy",)
        assert locllmw.LocllmWindow._state_tag("可用") == ()
        assert locllmw.LocllmWindow._state_tag("运行中") == ()


class TestServewEdgeCases:
    def test_parse_gpu_rows_skips_blank_lines(self) -> None:
        assert locllmw._parse_gpu_rows("\n   \n") == []

    def test_sample_gpu_when_process_query_fails(self) -> None:
        def run(cmd: list[str], *args: Any, **kwargs: Any) -> CommandResult:
            del args, kwargs
            if any("query-compute-apps" in part for part in cmd):
                return CommandResult(1, "", "")
            return CommandResult(0, _GPU_CSV, "")

        sample = locllmw._sample_gpu(1, run)
        assert sample is not None
        assert sample.process_memory is None

    def test_sample_load_without_cpu_freq(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import psutil

        def boom() -> Any:
            raise NotImplementedError

        monkeypatch.setattr(psutil, "cpu_freq", boom)
        sample = locllmw._sample_load(2**31 - 1, None)
        assert sample.cpu_freq is None

    def test_sample_load_ignores_dead_process(self) -> None:
        import psutil

        class _DeadProcess:
            def cpu_percent(self) -> float:
                raise psutil.NoSuchProcess(pid=1)

        sample = locllmw._sample_load(2**31 - 1, _DeadProcess())
        assert sample.process_cpu is None
        assert sample.process_rss is None

    def test_format_duration_branches(self) -> None:
        assert locllmw._format_duration(45) == "45s"
        assert locllmw._format_duration(125) == "2m 05s"
        assert locllmw._format_duration(3661) == "1h 01m 01s"

    def test_format_gpu_without_power_draw(self) -> None:
        assert locllmw._format_gpu(locllmw.GpuSample(name="GPU"))["gpu_power"] == "-"

    def test_probe_web_ui_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from urllib.error import HTTPError

        class _Response:
            status = 200

            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *args: object) -> bool:
                return False

        monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: _Response())
        assert locllmw._probe_web_ui("http://127.0.0.1:8080") == 200

        def unauthorized(request: Any, timeout: float) -> Any:
            raise HTTPError("http://x/", 401, "unauthorized", cast(Any, None), None)

        monkeypatch.setattr("urllib.request.urlopen", unauthorized)
        assert locllmw._probe_web_ui("http://127.0.0.1:8080", "secret") == 401

        def offline(request: Any, timeout: float) -> Any:
            raise OSError("down")

        monkeypatch.setattr("urllib.request.urlopen", offline)
        assert locllmw._probe_web_ui("http://127.0.0.1:8080") is None


class TestServewGuiEntry:
    def test_run_gui_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_window() -> None:
            raise ImportError("no tkinter")

        monkeypatch.setattr(locllmw, "LocllmWindow", fake_window)
        locllmw.run_gui()  # 不应抛出

    def test_run_gui_starts_mainloop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        class _Root:
            def mainloop(self) -> None:
                calls.append("mainloop")

        class _Window:
            root = _Root()

        monkeypatch.setattr(locllmw, "LocllmWindow", _Window)
        locllmw.run_gui()
        assert calls == ["mainloop"]

    def test_launch_cmd_calls_run_gui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []
        monkeypatch.setattr(locllmw, "run_gui", lambda: calls.append(True))
        locllmw.launch_cmd()
        assert calls == [True]


class TestServewSettings:
    """locllmw 窗口位置/大小持久化（纯逻辑，不创建 Tk）。"""

    def test_config_path_uses_fcmd_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert locllmw.config_path() == tmp_path / "ui" / "locllmw.json"

    def test_load_missing_file_returns_default(self, tmp_path: Path) -> None:
        assert locllmw.load_settings(tmp_path / "none.json") == locllmw.LocllmSettings()

    def test_load_ignores_invalid_geometry(self, tmp_path: Path) -> None:
        path = tmp_path / "ui.json"
        path.write_text('{"geometry": "bad"}', encoding="utf-8")
        assert locllmw.load_settings(path).geometry is None
        path.write_text('{"geometry": 123}', encoding="utf-8")
        assert locllmw.load_settings(path).geometry is None

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "ui.json"
        locllmw.save_settings(locllmw.LocllmSettings(geometry="920x700+10+20"), path)
        assert locllmw.load_settings(path).geometry == "920x700+10+20"

    def test_chat_settings_collapsed_by_default(self, tmp_path: Path) -> None:
        path = tmp_path / "ui.json"
        path.write_text("{}", encoding="utf-8")
        assert locllmw.load_settings(path).chat_settings_open is False

    def test_chat_settings_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "ui.json"
        locllmw.save_settings(locllmw.LocllmSettings(chat_settings_open=True), path)
        assert locllmw.load_settings(path).chat_settings_open is True

    def test_load_ignores_non_bool_chat_settings(self, tmp_path: Path) -> None:
        path = tmp_path / "ui.json"
        path.write_text('{"chat_settings_open": "yes"}', encoding="utf-8")
        assert locllmw.load_settings(path).chat_settings_open is False

    def test_roundtrip_hardware_and_measured(self, tmp_path: Path) -> None:
        path = tmp_path / "ui.json"
        settings = locllmw.LocllmSettings(
            geometry="920x700",
            hardware={"schema": 1, "cpu": "CPU", "disks": []},
            measured={"d:/models/a.gguf": {"decode_tps": 42.0}},
        )
        locllmw.save_settings(settings, path)
        loaded = locllmw.load_settings(path)
        assert loaded == settings

    def test_load_drops_non_dict_measured_rows(self, tmp_path: Path) -> None:
        path = tmp_path / "ui.json"
        path.write_text('{"measured": {"a": {"decode_tps": 1}, "b": 2}}', encoding="utf-8")
        assert locllmw.load_settings(path).measured == {"a": {"decode_tps": 1}}

    def test_save_ignores_write_error(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        locllmw.save_settings(locllmw.LocllmSettings(geometry="920x700"), blocker / "ui.json")

    def test_resolve_geometry_keeps_visible_position(self) -> None:
        assert locllmw.resolve_geometry("1100x850+100+80", 1920, 1080) == "1100x850+100+80"

    def test_resolve_geometry_pulls_back_offscreen(self) -> None:
        assert locllmw.resolve_geometry("1100x850+5000+5000", 1920, 1080) == "1100x850+820+230"

    def test_resolve_geometry_enforces_minimum_size(self) -> None:
        assert locllmw.resolve_geometry("200x100+0+0", 1920, 1080) == "1000x800+0+0"

    def test_resolve_geometry_falls_back_to_default_size(self) -> None:
        assert locllmw.resolve_geometry(None, 1920, 1080) == "1080x880"
        assert locllmw.resolve_geometry("bad", 1920, 1080) == "1080x880"

    def test_resolve_geometry_clamps_default_to_small_screen(self) -> None:
        assert locllmw.resolve_geometry(None, 800, 600) == "800x600"

    def test_unfit_and_busy_markers_use_theme_semantic_colors(self) -> None:
        from fcmd.gui import theme as _guitheme

        # 标记色改为窗口实例的 self._theme.palette，不再有模块级静态绑定。
        assert not hasattr(locllmw, "_UNFIT_COLOR")
        assert not hasattr(locllmw, "_BUSY_COLOR")
        for theme in _guitheme.THEMES.values():
            assert theme.palette.danger != theme.palette.warning


class TestServewBench:
    """locllmw 性能测试页的纯逻辑：场景表、执行、结果与目标解析。"""

    @staticmethod
    def _endpoint(port: int = DEFAULT_PORT) -> llmbase.Endpoint:
        return llmbase.Endpoint(f"http://127.0.0.1:{port}", DEFAULT_API_KEY, "test-model")

    def test_scenarios_have_unique_keys_and_lookup(self) -> None:
        keys = [scenario.key for scenario in _locllmw_bench.SCENARIOS]
        assert len(keys) == len(set(keys))
        long8k = _locllmw_bench.scenario_by_key("long8k")
        assert long8k is not None
        assert long8k.prompt_tokens == 8192
        assert _locllmw_bench.scenario_by_key("missing") is None

    def test_scenarios_cover_typical_cases(self) -> None:
        labels = {scenario.label for scenario in _locllmw_bench.SCENARIOS}
        assert {"短对话", "长输出", "代码补全", "长上下文 8K", "长上下文 32K", "并发吞吐 4 路"} <= labels

    def test_checked_scenarios_default_all_and_subset(self) -> None:
        checked = _locllmw_bench.BENCH_CHECKED
        unchecked = _locllmw_bench.BENCH_UNCHECKED
        rows = [(checked, scenario.label) for scenario in _locllmw_bench.SCENARIOS]
        assert _locllmw_bench.checked_scenarios(rows) == list(_locllmw_bench.SCENARIOS)

        rows[2] = (unchecked, rows[2][1])
        expected = [scenario for index, scenario in enumerate(_locllmw_bench.SCENARIOS) if index != 2]
        assert _locllmw_bench.checked_scenarios(rows) == expected
        assert not _locllmw_bench.checked_scenarios([(unchecked, "x")] * len(rows))

    def test_run_scenario_sequential(self) -> None:
        calls: list[dict[str, Any]] = []

        def request(endpoint: Any, **kwargs: Any) -> llmbench.BenchResult:
            calls.append(kwargs)
            return llmbench.BenchResult(0.5, 10, 20, 40.0)

        scenario = _locllmw_bench.scenario_by_key("short")
        assert scenario is not None
        result = _locllmw_bench.run_scenario(self._endpoint(), scenario, repeat=2, request=request)
        assert result.requests == 2
        assert result.total_prompt == 20
        assert result.total_completion == 40
        assert result.error is None
        assert all(call["prompt_tokens"] is None for call in calls)
        assert all(call["max_tokens"] == 128 for call in calls)

    def test_run_scenario_uses_scenario_prompt_and_override(self) -> None:
        seen: list[dict[str, Any]] = []

        def request(endpoint: Any, **kwargs: Any) -> llmbench.BenchResult:
            seen.append(kwargs)
            return llmbench.BenchResult(0.1, 1024, 64, 32.0)

        scenario = _locllmw_bench.scenario_by_key("code")
        assert scenario is not None
        result = _locllmw_bench.run_scenario(self._endpoint(), scenario, max_tokens=64, request=request)
        assert seen[0]["prompt_tokens"] == 1024
        assert seen[0]["max_tokens"] == 64
        assert seen[0]["timeout"] == 180.0
        assert result.requests == 1

    def test_run_scenario_concurrent(self) -> None:
        calls: list[int] = []
        lock = threading.Lock()

        def request(endpoint: Any, **kwargs: Any) -> llmbench.BenchResult:
            with lock:
                calls.append(1)
            return llmbench.BenchResult(0.2, 10, 20, 100.0)

        scenario = _locllmw_bench.scenario_by_key("concurrent4")
        assert scenario is not None
        result = _locllmw_bench.run_scenario(self._endpoint(), scenario, request=request)
        assert scenario.concurrency == 4
        assert result.requests == 4
        assert len(calls) == 4

    def test_run_scenario_records_error(self) -> None:
        def request(endpoint: Any, **kwargs: Any) -> llmbench.BenchResult:
            raise ServeError("boom")

        result = _locllmw_bench.run_scenario(self._endpoint(), _locllmw_bench.SCENARIOS[0], request=request)
        assert result.error == "boom"
        assert result.requests == 0
        assert result.cancelled is False

    def test_run_scenario_cancelled_before_start(self) -> None:
        cancel = threading.Event()
        cancel.set()
        called: list[bool] = []

        def request(endpoint: Any, **kwargs: Any) -> llmbench.BenchResult:
            called.append(True)
            return llmbench.BenchResult(0.1, 1, 1, 10.0)

        result = _locllmw_bench.run_scenario(
            self._endpoint(), _locllmw_bench.SCENARIOS[0], cancel=cancel, request=request
        )
        assert result.cancelled is True
        assert not called

    def test_result_metrics_and_row(self) -> None:
        scenario = _locllmw_bench.SCENARIOS[0]
        samples = (
            _locllmw_bench.BenchSample(0.5, 10, 20, 40.0),
            _locllmw_bench.BenchSample(0.7, 30, 40, 60.0),
        )
        result = _locllmw_bench.ScenarioResult(scenario, samples, 2.0)
        assert result.requests == 2
        assert result.total_prompt == 40
        assert result.total_completion == 60
        assert result.aggregate_tps == pytest.approx(30.0)
        assert result.mean_tps == pytest.approx(50.0)
        row = _locllmw_bench.format_result_row(result)
        assert row[0] == scenario.label
        assert row[1] == "2"
        assert row[5] == "30.0"
        assert row[6] == "50.0"
        assert row[-1] == "完成"
        assert len(row) == len(_locllmw_bench.RESULT_COLUMNS)

    def test_result_row_reports_error_and_cancel(self) -> None:
        scenario = _locllmw_bench.SCENARIOS[0]
        failed = _locllmw_bench.ScenarioResult(scenario, error="x" * 100)
        status = _locllmw_bench.format_result_row(failed)[-1]
        assert status.startswith("失败: ")
        assert len(status) < 60
        cancelled = _locllmw_bench.ScenarioResult(scenario, cancelled=True)
        assert _locllmw_bench.format_result_row(cancelled)[-1] == "已取消"
        assert _locllmw_bench.ScenarioResult(scenario).mean_tps == 0.0

    def test_format_result_csv_has_header_and_escapes_fields(self) -> None:
        text = _locllmw_bench.format_result_csv([("场景,带逗号", "1", "2", "3", "1.00 s", "9.0", "8.0", '失败: "x"')])
        lines = text.strip().splitlines()
        assert lines[0].split(",") == list(_locllmw_bench.RESULT_COLUMNS)
        assert '"场景,带逗号"' in lines[1]
        assert '""x""' in lines[1]

    def test_endpoint_from_states_prefers_named_and_single(self) -> None:
        states = [_state("alpha", port=8081), _state("beta", port=8082)]
        named = _locllmw_bench.endpoint_from_states(states, "beta", alive=lambda state: state.name == "beta")
        assert named.base_url == "http://127.0.0.1:8082"
        single = _locllmw_bench.endpoint_from_states(states, alive=lambda state: state.name == "alpha")
        assert single.base_url == "http://127.0.0.1:8081"

    def test_endpoint_from_states_errors(self) -> None:
        with pytest.raises(ServeError, match="没有运行中的服务"):
            _locllmw_bench.endpoint_from_states([_state("alpha")], alive=lambda state: False)
        registered = [_state("alpha"), _state("beta", port=8081)]
        with pytest.raises(ServeError, match="未在运行"):
            _locllmw_bench.endpoint_from_states(registered, "alpha", alive=lambda state: state.name == "beta")
        with pytest.raises(ServeError, match="多个服务在运行"):
            _locllmw_bench.endpoint_from_states(registered, alive=lambda state: True)


class TestServeMiscBranches:
    def test_parse_prometheus_skips_missing_value(self) -> None:
        assert llmserve.parse_prometheus("without_value\nother 1.5\n") == {"other": 1.5}

    def test_make_plan_auto_detects_gpu(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path
    ) -> None:
        engine = _engine_dir(tmp_path) / llmengine.engine_exe_name()
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(engine))
        calls: list[int] = []

        def _detect_gpu(*_args: object) -> GpuInfo:
            calls.append(1)
            return GpuInfo("Test GPU", 16384)

        monkeypatch.setattr(llmengine, "detect_gpu", _detect_gpu)

        plan = llmserve.make_plan("qwen", home=home, model=model_file, tune="auto")

        assert calls == [1]
        assert plan.tuning.label

    def test_launch_once_uses_new_session_off_windows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, model_file: Path
    ) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        captured: dict[str, object] = {}

        def _popen(argv: list[str], **kwargs: object) -> _FakeProc:
            captured["kwargs"] = kwargs
            return _FakeProc(os.getpid())

        monkeypatch.setattr(llmserve.os, "name", "posix")

        state = llmserve._launch_once(
            plan,
            home=home,
            popen=_popen,
            health=lambda host, port, key: True,
            sleep=lambda seconds: None,
        )

        assert state.name == "qwen"
        kwargs = cast(dict[str, object], captured["kwargs"])
        assert kwargs["start_new_session"] is True
        assert kwargs["creationflags"] == 0

    def test_print_plan_warns_when_estimate_exceeds_gpu(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        home: Path,
        model_file: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        plan = _plan(tmp_path, monkeypatch, home, model_file)
        monkeypatch.setattr(llmmodel, "plan_vram", lambda plan: 2 * 1024**3)
        monkeypatch.setattr(llmengine, "detect_gpu", lambda *_args: GpuInfo("Test GPU", 1024))

        llmserve._print_plan(plan)

        out = capsys.readouterr().out
        assert "警告" in out
        assert "可能 OOM" in out
