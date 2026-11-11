"""llmdownw 窗口版测试。

覆盖：
- 工具注册
- 设置文件读写与非法值回退
- geometry 恢复、掩码解析、列表行与详情行格式化
- 系统文件管理器打开路径
- 删除模型前的路径校验沿用 llmdown 的本地管理实现
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.llm import llmdownw
from fcmd.tools.llm.llmdown import LocalModel, ModelInfo, ModelSummary
from fcmd.tools.llm.llmdownw import (
    DownloadGuiSettings,
    config_path,
    detail_pairs,
    load_settings,
    local_row,
    model_row,
    open_path,
    parse_patterns,
    resolve_geometry,
    save_settings,
)


class TestRegistration:
    def test_registered(self) -> None:
        assert "llmdownw" in _TOOL_REGISTRY


class TestSettings:
    def test_defaults(self, tmp_path: Path) -> None:
        settings = load_settings(tmp_path / "missing.json")
        assert settings == DownloadGuiSettings()

    def test_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "llmdownw.json"
        save_settings(
            DownloadGuiSettings(
                geometry="1200x800+10+20",
                dest="/data/models",
                source="hf-mirror",
                workers=8,
                verify=False,
                include="*.safetensors",
                exclude="*.md",
            ),
            path,
        )

        loaded = load_settings(path)

        assert loaded.geometry == "1200x800+10+20"
        assert loaded.dest == "/data/models"
        assert loaded.source == "hf-mirror"
        assert loaded.workers == 8
        assert loaded.verify is False
        assert loaded.include == "*.safetensors"
        assert loaded.exclude == "*.md"

    def test_invalid_values_fallback(self, tmp_path: Path) -> None:
        path = tmp_path / "llmdownw.json"
        path.write_text(
            '{"geometry": "bad", "dest": "  ", "source": "unknown", "workers": 999, '
            '"verify": "yes", "include": 1, "exclude": "*.bin"}',
            encoding="utf-8",
        )

        settings = load_settings(path)

        assert settings.geometry is None
        assert settings.dest == "models"
        assert settings.source == "modelscope"
        assert settings.workers == 16
        assert settings.verify is True
        assert settings.include == ""
        assert settings.exclude == "*.bin"

    def test_bool_workers_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "llmdownw.json"
        path.write_text('{"workers": true}', encoding="utf-8")
        assert load_settings(path).workers == 4

    def test_config_path_under_fcmd_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert config_path() == tmp_path / "ui" / "llmdownw.json"


class TestGeometry:
    def test_default_when_missing(self) -> None:
        assert resolve_geometry(None, 1920, 1080) == "1280x880"

    def test_clamps_to_screen(self) -> None:
        geometry = resolve_geometry("3000x2000+5000+5000", 1920, 1080)
        assert geometry.startswith("1920x1080")

    def test_restores_valid(self) -> None:
        assert resolve_geometry("1200x800+30+40", 1920, 1080) == "1200x800+30+40"


class TestFormatting:
    def test_parse_patterns(self) -> None:
        assert parse_patterns("") is None
        assert parse_patterns("  , ; ") is None
        assert parse_patterns("*.bin, *.json;;README*") == ["*.bin", "*.json", "README*"]

    def test_model_row(self) -> None:
        summary = ModelSummary(
            repo="Qwen/demo",
            downloads=1234,
            stars=9,
            tasks=("文本生成", "视觉问答"),
            updated=1700000000,
            storage_size=2048,
        )
        assert model_row(summary) == ("Qwen/demo", "文本生成、视觉问答", "1,234", "2023-11-15", "2.00 KB")

    def test_model_row_empty(self) -> None:
        assert model_row(ModelSummary(repo="Qwen/demo")) == ("Qwen/demo", "-", "0", "-", "-")

    def test_local_row(self) -> None:
        path = Path("/models/Qwen/demo")
        model = LocalModel("Qwen/demo", path, 2048, 3, 0)
        assert local_row(model) == ("Qwen/demo", "2.00 KB", "3", "完整", str(path))
        partial = LocalModel("Qwen/demo", path, 2048, 3, 2)
        assert local_row(partial)[3] == "未完成（2 个 .part）"

    def test_detail_pairs(self) -> None:
        info = ModelInfo(
            summary=ModelSummary(
                repo="Qwen/demo",
                owner="Qwen",
                chinese_name="演示",
                tasks=("文本生成",),
                license="Apache-2.0",
                downloads=5,
                stars=2,
                storage_size=1024,
                updated=1700000000,
            ),
            organization="通义千问",
            frameworks=("PyTorch",),
            architectures=("Transformer",),
            model_types=("text-generation",),
            languages=("zh",),
            revision="master",
            inference="True",
        )
        pairs = dict(detail_pairs(info))
        assert pairs["中文名"] == "演示"
        assert pairs["组织"] == "通义千问"
        assert pairs["任务"] == "文本生成"
        assert pairs["框架"] == "PyTorch"
        assert pairs["架构"] == "Transformer"
        assert pairs["模型类型"] == "text-generation"
        assert pairs["语言"] == "zh"
        assert pairs["许可证"] == "Apache-2.0"
        assert pairs["大小"] == "1.00 KB"
        assert pairs["版本"] == "master"
        assert pairs["推理支持"] == "True"


class TestOpenPath:
    def test_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[Path] = []
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(os, "startfile", lambda path: calls.append(Path(path)), raising=False)
        open_path(Path("F:/models"))
        assert calls == [Path("F:/models")]

    def test_linux(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        class _Popen:
            def __init__(self, command: list[str]) -> None:
                calls.append(command)

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(subprocess, "Popen", _Popen)
        open_path(tmp_path)
        assert calls == [["xdg-open", str(tmp_path)]]

    def test_macos(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        class _Popen:
            def __init__(self, command: list[str]) -> None:
                calls.append(command)

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(subprocess, "Popen", _Popen)
        open_path(tmp_path)
        assert calls == [["open", str(tmp_path)]]


class TestLaunchEntry:
    def test_launch_calls_run_gui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called: list[bool] = []
        monkeypatch.setattr(llmdownw, "run_gui", lambda: called.append(True))
        llmdownw.launch_cmd()
        assert called == [True]

    def test_settings_type_annotations(self) -> None:
        # 防止 dataclass 字段被误删：窗口保存逻辑依赖这些字段
        fields = {field.name for field in DownloadGuiSettings.__dataclass_fields__.values()}
        assert fields == {"geometry", "dest", "source", "workers", "verify", "include", "exclude"}
