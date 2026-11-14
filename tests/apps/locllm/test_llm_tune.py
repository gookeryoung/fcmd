"""llmserve 调优策略（_llmtune）纯逻辑与启动参数生成测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apps.locllm import _llmtune, llmserve
from fcmd.apps.locllm.llmbase import PROFILES, EngineInfo, LaunchPlan, ServeError, Tuning


def _plan(tmp_path: Path, **kwargs: object) -> LaunchPlan:
    """构造带可执行引擎占位符的启动计划。"""
    model = tmp_path / "m.gguf"
    model.write_bytes(b"0" * 1024)
    engine = tmp_path / "llama-server.exe"
    engine.write_bytes(b"")
    options: dict[str, object] = {
        "name": "qwen",
        "model": model,
        "profile": PROFILES["balanced"],
        "context": 65536,
        "host": "127.0.0.1",
        "port": 8080,
        "alias": "qwen",
        "api_key": "k",
        "threads": 8,
        "engine": EngineInfo("b1", engine),
        "log": tmp_path / "log.txt",
    }
    options.update(kwargs)
    return LaunchPlan(**options)  # type: ignore[arg-type]


class TestPresets:
    def test_default_matches_existing_behavior(self) -> None:
        tuning = _llmtune.preset("default")
        assert (tuning.batch, tuning.ubatch, tuning.parallel, tuning.cache_reuse) == (4096, None, 1, 256)
        assert tuning.flash_attn == "on"
        assert tuning.kv_type is None

    def test_all_documented_names_have_presets(self) -> None:
        for name in _llmtune.TUNE_NAMES:
            if name == "auto":
                continue
            assert _llmtune.preset(name).name == name

    def test_unknown_preset_raises(self) -> None:
        with pytest.raises(ServeError, match="未知调优策略"):
            _llmtune.preset("turbo")

    def test_every_label_round_trips(self) -> None:
        for name, label in _llmtune.TUNE_LABELS.items():
            assert _llmtune.LABEL_TO_TUNE[label] == name


class TestAuto:
    def test_without_gpu_keeps_default(self) -> None:
        tuning = _llmtune.resolve("auto", context=65536, model_bytes=8 * 1024**3, gpu_mib=None)
        assert tuning.name == "auto"
        assert tuning.label == "自动 → 默认（现状）"
        assert tuning.parallel == 1
        assert "未检测到 NVIDIA GPU" in tuning.detail

    def test_normal_gpu_keeps_default(self) -> None:
        tuning = _llmtune.resolve("auto", context=65536, model_bytes=20 * 1024**3, gpu_mib=32607)
        assert tuning.parallel == 1
        assert tuning.ubatch is None
        assert "显存与上下文适中" in tuning.detail

    def test_tight_vram_switches_to_low_vram(self) -> None:
        tuning = _llmtune.resolve("auto", context=65536, model_bytes=32 * 1024**3, gpu_mib=32607)
        assert tuning.kv_type == "q4_0"
        assert tuning.ubatch == 512
        assert "省显存" in tuning.label

    def test_long_context_switches_to_q8_kv(self) -> None:
        tuning = _llmtune.resolve("auto", context=131072, model_bytes=8 * 1024**3, gpu_mib=32607)
        assert tuning.kv_type == "q8_0"
        assert "长上下文" in tuning.label

    def test_long_context_falls_back_when_q8_does_not_fit(self) -> None:
        tuning = _llmtune.resolve("auto", context=131072, model_bytes=30 * 1024**3, gpu_mib=32607)
        assert tuning.kv_type == "q4_0"
        assert "省显存" in tuning.label


class TestBackendArgs:
    def test_throughput_flags(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, tuning=_llmtune.preset("throughput"))
        argv = plan.argv()
        assert argv[argv.index("-np") + 1] == "4"
        assert argv[argv.index("-b") + 1] == "4096"
        assert argv[argv.index("--threads-http") + 1] == "8"
        assert argv[argv.index("--cache-reuse") + 1] == "256"

    def test_low_vram_flags(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, tuning=_llmtune.preset("low-vram"))
        assert plan.effective_kv_type == "q4_0"
        assert plan.effective_ubatch == 512
        argv = plan.argv()
        assert argv[argv.index("-b") + 1] == "2048"
        assert argv[argv.index("-ctk") + 1] == "q4_0"
        assert argv[argv.index("--cache-reuse") + 1] == "128"

    def test_explicit_fields_override_tuning(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, tuning=_llmtune.preset("low-vram"), kv_type="f16", ubatch=1024)
        assert plan.effective_kv_type == "f16"
        assert plan.effective_ubatch == 1024

    def test_cache_reuse_zero_omits_flag(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, tuning=Tuning(cache_reuse=0))
        assert "--cache-reuse" not in plan.argv()

    def test_spec_type_is_forwarded(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, tuning=Tuning(spec_type="ngram-simple"))
        argv = plan.argv()
        assert argv[argv.index("--spec-type") + 1] == "ngram-simple"

    def test_explicit_parallel_and_threads_http_override_tuning(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, tuning=_llmtune.preset("default"), parallel=2, threads_http=6)
        assert plan.effective_parallel == 2
        assert plan.effective_threads_http == 6
        argv = plan.argv()
        assert argv[argv.index("-np") + 1] == "2"
        assert argv[argv.index("--threads-http") + 1] == "6"


class TestSyncAdvancedFields:
    @staticmethod
    def _sync(tuning: Tuning, **fields: object) -> _llmtune.FieldSync:
        values: dict[str, object] = {
            "kv_value": "",
            "ubatch_value": "",
            "parallel_value": "",
            "threads_http_value": "",
            "kv_auto": False,
            "ubatch_auto": False,
            "parallel_auto": False,
            "threads_http_auto": False,
        }
        values.update(fields)
        return _llmtune.sync_advanced_fields(tuning, **values)  # type: ignore[arg-type]

    def test_strategy_value_fills_empty_fields(self) -> None:
        synced = self._sync(_llmtune.preset("low-vram"))
        assert (synced.kv_value, synced.ubatch_value) == ("q4_0", "512")
        assert (synced.kv_auto, synced.ubatch_auto) == (True, True)
        assert (synced.parallel_value, synced.threads_http_value) == ("", "")

    def test_throughput_fills_parallel_and_threads_http(self) -> None:
        synced = self._sync(_llmtune.preset("throughput"))
        assert (synced.parallel_value, synced.parallel_auto) == ("4", True)
        assert (synced.threads_http_value, synced.threads_http_auto) == ("8", True)
        assert (synced.kv_value, synced.ubatch_value) == ("", "")

    def test_strategy_value_overrides_previous_strategy_value(self) -> None:
        synced = self._sync(
            _llmtune.preset("long-context"),
            kv_value="q4_0",
            ubatch_value="512",
            kv_auto=True,
            ubatch_auto=True,
        )
        assert synced.kv_value == "q8_0"
        assert synced.kv_auto is True
        assert synced.ubatch_value == ""
        assert synced.ubatch_auto is False

    def test_switching_back_clears_but_keeps_manual_values(self) -> None:
        synced = self._sync(
            _llmtune.preset("default"),
            kv_value="f16",
            ubatch_value="512",
            parallel_value="2",
            threads_http_value="8",
            kv_auto=False,
            ubatch_auto=True,
            parallel_auto=False,
            threads_http_auto=True,
        )
        assert (synced.kv_value, synced.kv_auto) == ("f16", False)
        assert (synced.ubatch_value, synced.ubatch_auto) == ("", False)
        assert (synced.parallel_value, synced.parallel_auto) == ("2", False)
        assert (synced.threads_http_value, synced.threads_http_auto) == ("", False)


class TestMakePlanTune:
    @pytest.fixture
    def env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
        model = tmp_path / "m.gguf"
        model.write_bytes(b"0" * 1024)
        engine = tmp_path / "llama-server.exe"
        engine.write_bytes(b"")
        monkeypatch.setenv("FCMD_LLAMA_SERVER", str(engine))
        return model, tmp_path / "home"

    def test_tune_is_applied(self, env: tuple[Path, Path]) -> None:
        model, home = env
        plan = llmserve.make_plan("qwen", home=home, model=model, tune="throughput")
        assert plan.tuning.parallel == 4
        assert plan.argv()[plan.argv().index("-np") + 1] == "4"

    def test_auto_uses_supplied_gpu_memory(self, env: tuple[Path, Path]) -> None:
        model, home = env
        plan = llmserve.make_plan("qwen", home=home, model=model, tune="auto", gpu_mib=32607)
        assert plan.tuning.name == "auto"
        assert plan.tuning.kv_type is None

    def test_explicit_concurrency_overrides_strategy(self, env: tuple[Path, Path]) -> None:
        model, home = env
        plan = llmserve.make_plan("qwen", home=home, model=model, tune="throughput", parallel=2, threads_http=6)
        assert (plan.effective_parallel, plan.effective_threads_http) == (2, 6)

    def test_invalid_concurrency_raises(self, env: tuple[Path, Path]) -> None:
        model, home = env
        with pytest.raises(ServeError, match="并发槽必须为正整数"):
            llmserve.make_plan("qwen", home=home, model=model, parallel=0)
        with pytest.raises(ServeError, match="HTTP 线程数必须为正整数"):
            llmserve.make_plan("qwen", home=home, model=model, threads_http=0)

    def test_unknown_tune_raises(self, env: tuple[Path, Path]) -> None:
        model, home = env
        with pytest.raises(ServeError, match="未知调优策略"):
            llmserve.make_plan("qwen", home=home, model=model, tune="turbo")
