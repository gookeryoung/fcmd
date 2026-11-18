"""locllmw 概览页纯逻辑：硬件快照读写、磁盘匹配与智能推荐。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fcmd.apps.locllm import _locllmw_hardware as hardware
from fcmd.apps.locllm import _locllmw_recommend as recommend
from fcmd.apps.locllm import llmenv
from fcmd.apps.locllm.llmbase import CommandResult, ModelEntry


def _entry(tmp_path: Path, name: str, size: int, tags: tuple[str, ...] = ()) -> ModelEntry:
    path = tmp_path / name
    path.write_bytes(b"")
    return ModelEntry(root=tmp_path, path=path, size=size, tags=tags)


def _snapshot(gpu_mib: int | None = 8000, disks: tuple[hardware.DiskInfo, ...] = ()) -> hardware.HardwareSnapshot:
    return hardware.HardwareSnapshot(
        cpu="Test CPU",
        cpu_cores=8,
        cpu_threads=16,
        memory_total=32 * 1024**3,
        gpu="Test GPU" if gpu_mib else "",
        gpu_mib=gpu_mib,
        disks=disks,
        model_root="D:/models",
        detected_at="2026-01-01T00:00:00+00:00",
    )


class TestHardwareSnapshot:
    def test_roundtrip(self, tmp_path: Path) -> None:
        disk = hardware.DiskInfo(str(tmp_path), "NTFS", 1000, 400)
        snapshot = hardware.HardwareSnapshot(
            cpu="CPU",
            cpu_cores=8,
            cpu_threads=16,
            memory_total=1024,
            gpu="GPU",
            gpu_mib=8192,
            disks=(disk,),
            model_root=str(tmp_path),
            detected_at="2026-01-01T00:00:00+00:00",
        )
        restored = hardware.snapshot_from_dict(hardware.snapshot_to_dict(snapshot))
        assert restored == snapshot

    def test_invalid_snapshot_returns_none(self) -> None:
        assert hardware.snapshot_from_dict(None) is None
        assert hardware.snapshot_from_dict({}) is None
        assert hardware.snapshot_from_dict({"schema": 99}) is None

    def test_snapshot_tolerates_bad_disk_rows(self) -> None:
        snapshot = hardware.snapshot_from_dict(
            {
                "schema": 1,
                "cpu": "CPU",
                "disks": [{"mount": "C:/", "total": 1, "free": 1}, "bad", {"no_mount": 1}],
            }
        )
        assert snapshot is not None
        assert len(snapshot.disks) == 1

    def test_disk_for_path_uses_longest_mount(self, tmp_path: Path) -> None:
        outer = hardware.DiskInfo(str(tmp_path), "NTFS", 100, 50)
        inner = hardware.DiskInfo(str(tmp_path / "models"), "NTFS", 80, 30)
        assert hardware.disk_for_path(tmp_path / "models" / "a.gguf", (outer, inner)) is inner
        assert hardware.disk_for_path(tmp_path / "other" / "a.gguf", (outer, inner)) is outer

    def test_format_disk_reports_free_and_total(self, tmp_path: Path) -> None:
        text = hardware.format_disk(hardware.DiskInfo(str(tmp_path), "NTFS", 1024**3, 512 * 1024**2))
        assert "可用" in text and "已用 50.0 %" in text
        assert hardware.format_disk(None) == "未检测到磁盘"

    def test_detect_cpu_name_never_empty(self) -> None:
        assert hardware.detect_cpu_name().strip()

    def test_detect_cpu_name_tolerates_missing_winreg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 Windows 上注入 nt 平台时没有 winreg，应回落到其它来源而不是抛错。"""
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> object:
            if name == "winreg":
                raise ImportError("no winreg")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        monkeypatch.setattr(hardware, "os", SimpleNamespace(name="nt"))
        assert hardware.detect_cpu_name().strip()

    def test_detect_hardware_uses_runner(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # 内存代数查询仅在 Windows 分支可达：只替换模块内的 os，不改全局 os.name。
        monkeypatch.setattr(hardware, "os", SimpleNamespace(name="nt"))

        def runner(argv: list[str]) -> CommandResult:
            assert argv[0] in ("nvidia-smi", "powershell")
            if argv[0] == "powershell":
                return CommandResult(0, "34\n")  # SMBIOSMemoryType: DDR5
            return CommandResult(0, "NVIDIA Test, 16384\n")

        snapshot = hardware.detect_hardware(tmp_path, runner=runner)
        assert snapshot.gpu == "NVIDIA Test"
        assert snapshot.gpu_mib == 16384
        assert snapshot.memory_total > 0
        assert snapshot.memory_gen == "DDR5"
        assert snapshot.model_root == str(tmp_path)

    def test_detect_memory_gen_maps_smbios_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 只替换模块内的 os，不改全局 os.name：测试失败时 pytest 会在调用阶段
        # （monkeypatch 尚未回滚）渲染报告，届时 pathlib 会按 os.name 选 WindowsPath，
        # 在 Linux 上直接抛 NotImplementedError 变成 INTERNALERROR / worker 崩溃。
        monkeypatch.setattr(hardware, "os", SimpleNamespace(name="nt"))
        for code, expected in ((20, "DDR"), (21, "DDR2"), (24, "DDR3"), (26, "DDR4"), (34, "DDR5")):
            runner = lambda argv, code=code: CommandResult(0, f"{code}\n")  # noqa: E731
            assert hardware.detect_memory_gen(runner) == expected

    def test_detect_memory_gen_tolerates_failure(self) -> None:
        assert hardware.detect_memory_gen(lambda argv: CommandResult(1, "", "err")) == ""
        assert hardware.detect_memory_gen(lambda argv: CommandResult(0, "unknown\n")) == ""
        assert hardware.detect_memory_gen(lambda argv: CommandResult(0, "35\n")) == ""

    def test_detect_memory_gen_skips_non_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hardware, "os", SimpleNamespace(name="posix"))
        assert hardware.detect_memory_gen(lambda argv: CommandResult(0, "34\n")) == ""


class TestPredictPerformance:
    def test_larger_gpu_and_smaller_model_are_faster(self) -> None:
        slow, _ = recommend.predict_performance(20 * 1024**3, 8000)
        fast, _ = recommend.predict_performance(20 * 1024**3, 32000)
        small, _ = recommend.predict_performance(8 * 1024**3, 8000)
        assert fast > slow
        assert small > slow
        assert recommend.predict_performance(0, 8000) == (0.0, 0.0)

    def test_cpu_only_is_much_slower(self) -> None:
        cpu_decode, _ = recommend.predict_performance(20 * 1024**3, None)
        gpu_decode, _ = recommend.predict_performance(20 * 1024**3, 32000)
        assert cpu_decode < gpu_decode / 10

    def test_tuning_reduces_decode(self) -> None:
        default, _ = recommend.predict_performance(20 * 1024**3, 32000, tuning="default")
        long_context, _ = recommend.predict_performance(20 * 1024**3, 32000, tuning="自动 → 长上下文")
        assert long_context < default


class TestRecommend:
    def test_recommends_models_that_fit(self, tmp_path: Path) -> None:
        huge = _entry(tmp_path, "huge.gguf", 20 * 1024**3)
        medium = _entry(tmp_path, "medium.gguf", 5 * 1024**3, ("代码",))
        small = _entry(tmp_path, "small.gguf", 2 * 1024**3)
        rows = recommend.recommend([huge, medium, small], _snapshot(gpu_mib=8000))
        names = [row.model.path.name for row in rows]
        # 按评分降序，而不是按模型大小：小模型显存余量 / 速度更好，排在前面。
        assert names == ["small.gguf", "medium.gguf"]
        assert [row.score for row in rows] == sorted((row.score for row in rows), reverse=True)
        assert rows[0].context >= 4096
        assert "预测" in rows[0].performance_text

    def test_measured_value_overrides_prediction(self, tmp_path: Path) -> None:
        entry = _entry(tmp_path, "model.gguf", 5 * 1024**3)
        key = llmenv.path_key(entry.path)
        rows = recommend.recommend([entry], _snapshot(), {key: {"decode_tps": 12.5, "prompt_tps": 999}})
        assert rows[0].measured is True
        assert rows[0].decode_tps == 12.5
        assert rows[0].prompt_tps == 999
        assert "实测" in rows[0].performance_text

    def test_cpu_only_still_recommends(self, tmp_path: Path) -> None:
        entry = _entry(tmp_path, "model.gguf", 5 * 1024**3)
        rows = recommend.recommend([entry], _snapshot(gpu_mib=None))
        assert len(rows) == 1
        assert rows[0].decode_tps > 0

    def test_limit_and_empty(self, tmp_path: Path) -> None:
        entries = [_entry(tmp_path, f"m{i}.gguf", (i + 1) * 1024**3) for i in range(5)]
        assert len(recommend.recommend(entries, _snapshot(), limit=2)) == 2
        assert recommend.recommend([], _snapshot()) == ()

    def test_limit_picks_largest_fitting_then_sorts_by_score(self, tmp_path: Path) -> None:
        entries = [_entry(tmp_path, f"m{i}.gguf", (i + 1) * 1024**3) for i in range(5)]
        rows = recommend.recommend(entries, _snapshot(), limit=2)
        # 先取最大的两个能放下的模型（m3 / m4），再按评分降序排列。
        assert {row.model.path.name for row in rows} == {"m3.gguf", "m4.gguf"}
        assert [row.score for row in rows] == sorted((row.score for row in rows), reverse=True)

    def test_unknown_tag_falls_through_to_known_profile(self, tmp_path: Path) -> None:
        entry = _entry(tmp_path, "model.gguf", 1024**3, ("未知标签", "代码"))
        rows = recommend.recommend([entry], _snapshot(gpu_mib=32000))
        assert rows[0].profile == "balanced"

    def test_fits_at_preferred_profile_without_downgrade(self, tmp_path: Path) -> None:
        entry = _entry(tmp_path, "model.gguf", 1024**3)
        rows = recommend.recommend([entry], _snapshot(gpu_mib=32000))
        assert rows[0].kv_type == "f16"
        assert rows[0].context == 65536

    def test_non_numeric_prompt_tps_keeps_prediction(self, tmp_path: Path) -> None:
        entry = _entry(tmp_path, "model.gguf", 1024**3)
        key = llmenv.path_key(entry.path)
        rows = recommend.recommend([entry], _snapshot(gpu_mib=32000), {key: {"decode_tps": 12.5, "prompt_tps": "n/a"}})
        assert rows[0].decode_tps == 12.5
        assert rows[0].prompt_tps != "n/a"


class TestScore:
    """评分规则：显存余量 40 / 上下文 25 / KV 15 / 解码速度 20，合计 100。"""

    @staticmethod
    def _score(**overrides: object) -> tuple[int, tuple[int, int, int, int]]:
        kwargs: dict[str, object] = {
            "profile": "balanced",
            "context": 65536,
            "kv_type": "f16",
            "model_bytes": 4 * 1024**3,
            "gpu_mib": 32000,
            "decode_tps": 40.0,
        }
        kwargs.update(overrides)
        return recommend.score_recommendation(**kwargs)  # type: ignore[arg-type]

    def test_small_model_with_headroom_scores_full_marks(self) -> None:
        total, parts = self._score()
        assert parts == (40, 25, 15, 20)
        assert total == 100

    def test_total_equals_sum_of_parts(self) -> None:
        for gpu_mib in (None, 8000, 16000, 32000):
            for context in (4096, 16384, 65536):
                total, parts = self._score(gpu_mib=gpu_mib, context=context)
                assert total == sum(parts)
                assert 0 <= total <= 100

    def test_tight_vram_lowers_fit_score(self) -> None:
        loose, _ = self._score(model_bytes=2 * 1024**3)
        tight, tight_parts = self._score(model_bytes=26 * 1024**3)
        assert tight < loose
        assert tight_parts[0] < 40

    def test_context_shrink_lowers_context_score(self) -> None:
        _, full = self._score(context=65536)
        _, shrunk = self._score(context=8192)
        assert full[1] == 25
        assert 0 < shrunk[1] < 25

    def test_kv_downgrade_lowers_kv_score(self) -> None:
        _, f16 = self._score(kv_type="f16")
        _, q8 = self._score(kv_type="q8_0")
        assert f16[2] == 15
        assert q8[2] < f16[2]

    def test_slow_decode_lowers_perf_score(self) -> None:
        _, fast = self._score(decode_tps=40.0)
        _, slow = self._score(decode_tps=4.0)
        assert fast[3] == 20
        assert slow[3] == 4

    def test_cpu_only_keeps_neutral_fit_score(self) -> None:
        _, parts = self._score(gpu_mib=None, decode_tps=3.0)
        assert parts[0] == round(recommend._CPU_FIT_RATIO * recommend.SCORE_WEIGHT_FIT)

    def test_band_thresholds(self) -> None:
        assert recommend.score_band(100) == "excellent"
        assert recommend.score_band(80) == "excellent"
        assert recommend.score_band(79) == "good"
        assert recommend.score_band(65) == "good"
        assert recommend.score_band(64) == "fair"
        assert recommend.score_band(50) == "fair"
        assert recommend.score_band(49) == "limited"
        assert recommend.score_band(0) == "limited"

    def test_recommend_attaches_score(self, tmp_path: Path) -> None:
        entry = _entry(tmp_path, "model.gguf", 5 * 1024**3)
        row = recommend.recommend([entry], _snapshot(gpu_mib=32000))[0]
        assert row.score == sum(row.score_parts)
        assert row.score_band == recommend.score_band(row.score)
        assert row.score_text == f"评分 {row.score}/100"
        assert row.score_detail.startswith("显存 ")
        assert "KV " in row.score_detail and "性能 " in row.score_detail


class TestHardwareDetection:
    def test_detect_cpu_name_reads_proc_cpuinfo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakePath:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def read_text(self, **_kwargs: object) -> str:
                return "processor\t: 0\nmodel name\t: Test CPU 9000\n"

        monkeypatch.setattr(hardware, "Path", _FakePath)
        # 替换模块内的 os 引用而非全局 os.name，理由见 test_detect_memory_gen_maps_smbios_code。
        monkeypatch.setattr(hardware, "os", SimpleNamespace(name="posix"))
        assert hardware.detect_cpu_name() == "Test CPU 9000"

    def test_detect_cpu_name_falls_back_to_platform(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakePath:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def read_text(self, **_kwargs: object) -> str:
                raise OSError("no /proc")

        monkeypatch.setattr(hardware, "Path", _FakePath)
        monkeypatch.setattr(hardware, "os", SimpleNamespace(name="posix"))
        monkeypatch.setattr(hardware.platform, "processor", lambda: "")
        monkeypatch.setattr(hardware.platform, "machine", lambda: "x86_64")
        assert hardware.detect_cpu_name() == "x86_64"

    def test_detect_cpu_name_registry_error_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakePath:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def read_text(self, **_kwargs: object) -> str:
                raise OSError("no /proc")

        def _open_key(*_args: object, **_kwargs: object) -> None:
            raise OSError("registry unavailable")

        monkeypatch.setitem(
            sys.modules,
            "winreg",
            SimpleNamespace(HKEY_LOCAL_MACHINE=0, OpenKey=_open_key),
        )
        monkeypatch.setattr(hardware, "Path", _FakePath)
        monkeypatch.setattr(hardware, "os", SimpleNamespace(name="nt"))
        monkeypatch.setattr(hardware.platform, "processor", lambda: "AMD Test")
        assert hardware.detect_cpu_name() == "AMD Test"

    def test_detect_hardware_skips_duplicate_and_unreadable_disks(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        partitions = [
            SimpleNamespace(fstype="NTFS", mountpoint="C:/"),
            SimpleNamespace(fstype="NTFS", mountpoint="C:/"),
            SimpleNamespace(fstype="tmpfs", mountpoint="/tmp"),
            SimpleNamespace(fstype="NTFS", mountpoint="E:/"),
        ]
        monkeypatch.setattr("psutil.disk_partitions", lambda all=False: partitions)
        monkeypatch.setattr(
            "psutil.disk_usage",
            lambda mount: (
                (_ for _ in ()).throw(OSError("locked")) if mount == "E:/" else SimpleNamespace(total=100, free=40)
            ),
        )
        monkeypatch.setattr("psutil.virtual_memory", lambda: SimpleNamespace(total=1024))
        monkeypatch.setattr("psutil.cpu_count", lambda logical=True: 8)
        monkeypatch.setattr(hardware.llmengine, "detect_gpu", lambda *_args: None)
        monkeypatch.setattr(hardware, "detect_memory_gen", lambda *_args: "")
        monkeypatch.setattr(hardware.llmenv, "models_dir", lambda home=None: tmp_path)

        snapshot = hardware.detect_hardware()

        assert snapshot.model_root == str(tmp_path)
        assert [disk.mount for disk in snapshot.disks] == ["C:/"]
        assert snapshot.gpu_mib is None

    def test_snapshot_from_dict_tolerates_non_list_disks(self) -> None:
        snapshot = hardware.snapshot_from_dict({"schema": 1, "cpu": "CPU", "disks": "oops"})
        assert snapshot is not None
        assert snapshot.cpu == "CPU"
        assert snapshot.disks == ()
