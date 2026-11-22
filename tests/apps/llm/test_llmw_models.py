"""llmw 模型页（整合原 llmdownw）测试。

覆盖：
- 独立工具已移除
- 下载设置字典读写与非法值回退
- 掩码解析、列表行与详情行格式化
- 系统文件管理器打开路径
- 删除模型前的路径校验沿用 llmdown 的本地管理实现
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from fcmd.apps.llm._llmw_models import (
    DEFAULT_MODEL_FILTER,
    MIN_ONLINE_MODEL_BYTES,
    MODEL_FILTERS,
    DownloadGuiSettings,
    dedupe_by_repo,
    detail_pairs,
    filter_by_local_fit,
    filter_by_name,
    filter_models,
    format_models_updated,
    group_same_name,
    is_usable_item,
    local_fit_size,
    local_row,
    matches_filter,
    merge_installed_items,
    model_item_lines,
    model_owner,
    model_param_billions,
    model_published_text,
    model_row,
    model_score,
    open_path,
    parse_patterns,
    quant_precision_label,
    repo_for_model_path,
    settings_from_dict,
    settings_to_dict,
    sort_browse_items,
    sort_models,
)
from fcmd.apps.llm.llmdown import LocalModel, ModelInfo, ModelSummary, format_timestamp
from fcmd.core.toolkit import _TOOL_REGISTRY


class TestRegistration:
    def test_removed_from_registry(self) -> None:
        assert "llmdownw" not in _TOOL_REGISTRY


class TestSettings:
    def test_defaults(self) -> None:
        assert settings_from_dict(None) == DownloadGuiSettings()
        assert DownloadGuiSettings().source == "auto"
        assert DownloadGuiSettings().workers == 8
        assert DownloadGuiSettings().parallel == 2

    def test_roundtrip(self) -> None:
        settings = DownloadGuiSettings(
            source="hf-mirror",
            workers=8,
            parallel=3,
            verify=False,
            include="*.safetensors",
            exclude="*.md",
        )
        assert settings_from_dict(settings_to_dict(settings)) == settings

    def test_invalid_values_fallback(self) -> None:
        settings = settings_from_dict(
            {
                "source": "unknown",
                "workers": 999,
                "parallel": 99,
                "verify": "yes",
                "include": 1,
                "exclude": "*.bin",
            }
        )
        assert settings.source == "auto"
        assert settings.workers == 8
        assert settings.parallel == 4
        assert settings.verify is True
        assert settings.include == ""
        assert settings.exclude == "*.bin"

    def test_bool_workers_rejected(self) -> None:
        assert settings_from_dict({"workers": True}).workers == 8

    def test_legacy_modelscope_default_migrates_to_auto(self) -> None:
        """升级前固定 ModelScope 的旧配置一次性迁到自动测速，避免仍被单源限速。"""
        settings = settings_from_dict({"source": "modelscope", "workers": 4})
        assert settings.source == "auto"
        assert settings.workers == 4

    def test_explicit_modelscope_kept_after_marker(self) -> None:
        raw = settings_to_dict(DownloadGuiSettings(source="modelscope"))
        assert settings_from_dict(raw).source == "modelscope"


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
        assert model_row(summary) == ("Qwen/demo", "Qwen", "文本生成、视觉问答", "1,234", "2.00 KB")

    def test_model_row_empty(self) -> None:
        assert model_row(ModelSummary(repo="Qwen/demo")) == ("Qwen/demo", "Qwen", "-", "0", "-")
        assert model_row(ModelSummary(repo="")) == ("", "其他", "-", "0", "-")

    def test_model_item_lines(self) -> None:
        summary = ModelSummary(
            repo="Qwen/demo",
            downloads=1234,
            stars=9,
            tasks=("文本生成", "视觉问答"),
            updated=1700000000,
            storage_size=2048,
        )
        assert model_item_lines(summary) == (
            "demo",
            "文本生成、视觉问答",
            "2.00 KB",
        )
        assert model_published_text(summary) == f"发布 {format_timestamp(summary.updated)}"

    def test_model_item_lines_empty(self) -> None:
        assert model_item_lines(ModelSummary(repo="Qwen/demo")) == ("demo", "-", "-")
        assert model_item_lines(ModelSummary(repo="")) == ("", "其他", "-")
        assert model_published_text(ModelSummary(repo="Qwen/demo")) == ""

    def test_model_owner_and_dedupe(self) -> None:
        assert model_owner(ModelSummary(repo="unsloth/demo")) == "unsloth"
        items = (
            ModelSummary(repo="unsloth/demo", name="demo"),
            ModelSummary(repo="UNSLOTH/DEMO", name="demo"),
            ModelSummary(repo="Qwen/demo", name="demo"),
        )
        assert [item.repo for item in dedupe_by_repo(items)] == ["unsloth/demo", "Qwen/demo"]

    def test_group_same_name_prefers_installed_then_score(self) -> None:
        installed = ModelSummary(repo="org/demo", name="demo", downloads=1, stars=1)
        popular = ModelSummary(repo="other/demo", name="demo", downloads=9999, stars=9999)
        grouped = group_same_name((popular, installed), {"org/demo"})
        assert grouped == ((installed, 2),)

    def test_group_same_name_keeps_local_directory_separate(self) -> None:
        """本地目录与同名官方仓库不是同一个对象：折叠不能把官方模型藏起来。"""
        local = ModelSummary(repo="Qwen3.8-27B")
        official = ModelSummary(repo="Qwen/Qwen3.8-27B", name="Qwen3.8-27B", downloads=10)
        grouped = group_same_name((local, official), {"qwen3.8-27b"})
        assert [(item.repo, count) for item, count in grouped] == [
            ("Qwen/Qwen3.8-27B", 1),
            ("Qwen3.8-27B", 1),
        ]
        # 远程仓库之间的同名折叠仍然生效（本地目录不参与）。
        other = ModelSummary(repo="someone/Qwen3.8-27B", name="Qwen3.8-27B")
        grouped = group_same_name((official, other, local), set())
        assert [(item.repo, count) for item, count in grouped] == [
            ("Qwen/Qwen3.8-27B", 2),
            ("Qwen3.8-27B", 1),
        ]

    def test_sort_by_name_and_size(self) -> None:
        large = ModelSummary(repo="b/large", name="large", storage_size=100)
        small = ModelSummary(repo="a/small", name="small", storage_size=10)
        assert [item.repo for item in sort_browse_items((large, small), set(), "all", "size")] == ["b/large", "a/small"]
        assert [item.repo for item in sort_browse_items((large, small), set(), "all", "name")] == ["b/large", "a/small"]

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


class TestBrowseHelpers:
    @staticmethod
    def _items() -> tuple[ModelSummary, ...]:
        return (
            ModelSummary(repo="Qwen/llm", tasks=("文本生成",), downloads=30, storage_size=300),
            ModelSummary(repo="BAAI/embed", tasks=("文本向量",), downloads=20, storage_size=100),
            ModelSummary(repo="Qwen/vl", tasks=("文本生成", "视觉问答"), downloads=10, storage_size=200),
        )

    def test_filter_options_cover_required_categories(self) -> None:
        keys = [key for key, _label, _query in MODEL_FILTERS]
        labels = [label for _key, label, _query in MODEL_FILTERS]
        assert len(set(keys)) == len(keys)
        assert DEFAULT_MODEL_FILTER in keys
        assert labels == ["适合本机", "全部", "对话", "编程", "看图", "画图", "推理", "视频", "音频"]

    def test_filter_by_local_fit_keeps_fitting_and_unknown_sizes(self) -> None:
        items = (*self._items(), ModelSummary(repo="Qwen/unknown"))
        repos = [item.repo for item in filter_by_local_fit(items, 200)]
        assert repos == ["BAAI/embed", "Qwen/vl", "Qwen/unknown"]
        assert filter_by_local_fit(items, 0) == items

    def test_model_param_billions_parses_names_and_moe(self) -> None:
        assert model_param_billions("Qwen3-8B") == 8
        assert model_param_billions("Qwen3-Coder-30B-A3B-Instruct-GGUF") == 30
        assert model_param_billions("Mixtral-8x7B-Instruct") == 56
        assert model_param_billions("Llama-3.1-8B-Instruct") == 8
        assert model_param_billions("Qwen3.8-Flash-Next") is None

    def test_local_fit_uses_single_quant_size_for_gguf_repos(self) -> None:
        """官方 GGUF 仓库总大小是多精度之和，不能拿整仓体积判「不适合本机」。"""
        gguf = ModelSummary(
            repo="Qwen/Qwen3-8B-GGUF",
            name="Qwen3-8B-GGUF",
            tags=("gguf",),
            storage_size=32 * 1024**3,
        )
        base = ModelSummary(repo="Qwen/Qwen3-8B", storage_size=10 * 1024**3)
        huge = ModelSummary(
            repo="Qwen/Qwen3-72B-GGUF",
            name="Qwen3-72B-GGUF",
            tags=("gguf",),
            storage_size=100 * 1024**3,
        )
        assert local_fit_size(gguf) < 8 * 1024**3
        assert local_fit_size(base) == base.storage_size
        capacity = 12 * 1024**3
        kept = [item.repo for item in filter_by_local_fit((gguf, base, huge), capacity)]
        assert kept == [gguf.repo, base.repo]
        # 同名仓库缺少可解析参数量时按未知保留，避免误杀官方新系列。
        unknown = ModelSummary(repo="Qwen/Qwen3.8-Flash-Next-GGUF", tags=("gguf",), storage_size=99 * 1024**3)
        assert filter_by_local_fit((unknown,), 1) == (unknown,)

    def test_model_score_is_bounded_and_rewards_popularity(self) -> None:
        low = ModelSummary(repo="Qwen/low", downloads=1, stars=0)
        high = ModelSummary(repo="Qwen/high", downloads=1_000_000, stars=10_000)
        assert 0 <= model_score(low) < model_score(high) <= 100

    def test_is_usable_item_hides_fake_local_models(self) -> None:
        """在线仓库有两段 repo 即有效；仅本地条目必须有可用权重或 .part。"""
        assert is_usable_item(ModelSummary(repo="Qwen/demo"), set())
        assert not is_usable_item(ModelSummary(repo="Unsloth"), set())
        assert is_usable_item(ModelSummary(repo="Unsloth"), {"unsloth"})
        # KB 级个人上传仓库隐藏；未知体积（0）保留，正常体积保留。
        assert not is_usable_item(ModelSummary(repo="someone/junk", storage_size=4096), set())
        assert not is_usable_item(ModelSummary(repo="someone/junk", storage_size=MIN_ONLINE_MODEL_BYTES - 1), set())
        assert is_usable_item(ModelSummary(repo="Qwen/demo", storage_size=0), set())
        assert is_usable_item(ModelSummary(repo="Qwen/demo", storage_size=MIN_ONLINE_MODEL_BYTES), set())

    def test_matches_filter_uses_tasks_tags_and_repo(self) -> None:
        assert matches_filter(ModelSummary(repo="Qwen/Coder", tasks=("代码生成",)), "code")
        assert matches_filter(ModelSummary(repo="Qwen/vl", tags=("vision",)), "vision")
        assert matches_filter(ModelSummary(repo="black-forest-labs/FLUX.1-dev"), "paint")
        assert matches_filter(ModelSummary(repo="Qwen/Qwen-Image", tasks=("文生图",)), "paint")
        assert matches_filter(ModelSummary(repo="Qwen/Qwen-Image-2.1"), "paint")
        assert matches_filter(ModelSummary(repo="Tongyi/Z-Image-Turbo"), "paint")
        assert matches_filter(ModelSummary(repo="MiniMax/MiniMax-H3-Image"), "paint")
        assert matches_filter(ModelSummary(repo="HiDream/HiDream-I1"), "paint")
        assert not matches_filter(ModelSummary(repo="Qwen/chat"), "paint")
        assert not matches_filter(ModelSummary(repo="Qwen/chat", tasks=("文本生成",)), "video")
        assert matches_filter(ModelSummary(repo="Qwen/chat"), "all")

    def test_merge_installed_items_adds_local_models_missing_from_page(self) -> None:
        page = (ModelSummary(repo="Qwen/chat"),)
        installed = {
            "qwen/chat": ModelSummary(repo="Qwen/chat"),
            "local/coder": ModelSummary(repo="local/coder", storage_size=1024),
        }
        assert [item.repo for item in merge_installed_items(page, installed, "all")] == [
            "Qwen/chat",
            "local/coder",
        ]
        assert [item.repo for item in merge_installed_items(page, installed, "code")] == [
            "Qwen/chat",
            "local/coder",
        ]
        assert [item.repo for item in merge_installed_items(page, installed, "video")] == ["Qwen/chat"]

    def test_sort_browse_items_pins_installed_category_matches_then_scores(self) -> None:
        installed_code = ModelSummary(repo="local/coder", tasks=("代码生成",), downloads=1)
        installed_vision = ModelSummary(repo="local/vision", tasks=("视觉问答",), downloads=1_000_000, stars=10_000)
        other = ModelSummary(repo="Qwen/chat", tasks=("文本生成",), downloads=500_000, stars=1_000)
        free_code = ModelSummary(repo="Qwen/coder2", tasks=("代码生成",), downloads=100_000, stars=100)
        items = (installed_vision, other, free_code, installed_code)
        installed = {"local/coder", "local/vision"}
        repos = [item.repo for item in sort_browse_items(items, installed, "code")]
        assert repos == ["local/coder", "local/vision", "Qwen/chat", "Qwen/coder2"]

    def test_sort_browse_items_pins_all_installed_for_all_filter(self) -> None:
        installed_low = ModelSummary(repo="local/low", downloads=1)
        installed_high = ModelSummary(repo="local/high", downloads=1_000_000)
        free = ModelSummary(repo="Qwen/free", downloads=500_000)
        repos = [
            item.repo
            for item in sort_browse_items((free, installed_low, installed_high), {"local/low", "local/high"}, "all")
        ]
        assert repos == ["local/high", "local/low", "Qwen/free"]

    def test_sort_browse_items_downloads_descending(self) -> None:
        low = ModelSummary(repo="a/low", downloads=10)
        high = ModelSummary(repo="a/high", downloads=1000)
        mid = ModelSummary(repo="a/mid", downloads=100)
        repos = [item.repo for item in sort_browse_items((low, high, mid), set(), "all", "downloads")]
        assert repos == ["a/high", "a/mid", "a/low"]

    def test_sort_browse_items_published_descending(self) -> None:
        old = ModelSummary(repo="a/old", downloads=1000, updated=100)
        new = ModelSummary(repo="a/new", downloads=10, updated=200)
        repos = [item.repo for item in sort_browse_items((old, new), set(), "all", "created")]
        assert repos == ["a/new", "a/old"]

    def test_filter_models_all(self) -> None:
        items = self._items()
        assert filter_models(items, "") == items
        assert filter_models(items, "全部类别") == items

    def test_filter_models_by_task(self) -> None:
        repos = [item.repo for item in filter_models(self._items(), "文本生成")]
        assert repos == ["Qwen/llm", "Qwen/vl"]
        assert filter_models(self._items(), "语音识别") == ()

    def test_sort_models_text_ascending(self) -> None:
        repos = [item.repo for item in sort_models(self._items(), "repo", descending=False)]
        assert repos == ["BAAI/embed", "Qwen/llm", "Qwen/vl"]

    def test_sort_models_numeric_descending(self) -> None:
        assert [item.repo for item in sort_models(self._items(), "downloads")] == [
            "Qwen/llm",
            "BAAI/embed",
            "Qwen/vl",
        ]
        assert [item.repo for item in sort_models(self._items(), "size", descending=False)] == [
            "BAAI/embed",
            "Qwen/vl",
            "Qwen/llm",
        ]

    def test_sort_models_by_series_rank(self) -> None:
        items = (
            ModelSummary(repo="acme/unknown"),
            ModelSummary(repo="01-ai/Yi-6B"),
            ModelSummary(repo="meta-llama/Llama-3-8B"),
        )
        assert [item.repo for item in sort_models(items, "series", descending=False)] == [
            "meta-llama/Llama-3-8B",
            "01-ai/Yi-6B",
            "acme/unknown",
        ]

    def test_sort_models_unknown_column(self) -> None:
        items = self._items()
        assert sort_models(items, None) == items
        assert sort_models(items, "unknown") == items


def test_filter_by_name_matches_repo_name_and_chinese_name() -> None:
    items = (
        ModelSummary(repo="Qwen/Qwen3-Coder-30B", name="Qwen3-Coder-30B"),
        ModelSummary(repo="deepseek/DeepSeek-R1", name="DeepSeek-R1", chinese_name="深度求索"),
        ModelSummary(repo="meta/Llama-3", name="Llama-3"),
    )
    assert [item.repo for item in filter_by_name(items, "coder")] == ["Qwen/Qwen3-Coder-30B"]
    assert [item.repo for item in filter_by_name(items, "  DEEPSEEK ")] == ["deepseek/DeepSeek-R1"]
    assert [item.repo for item in filter_by_name(items, "深度")] == ["deepseek/DeepSeek-R1"]
    assert filter_by_name(items, "   ") == items
    assert filter_by_name(items, "missing") == ()


def test_format_models_updated_handles_iso_and_invalid() -> None:
    assert format_models_updated("2026-11-18T07:37:00+08:00") == "更新于 2026-11-18 07:37"
    assert format_models_updated(None) == ""
    assert format_models_updated("") == ""
    assert format_models_updated("not-a-time") == ""


class TestQuantQuickStart:
    """模型页快捷启动用到的纯逻辑：仓库反推与量化精度标签。"""

    def test_repo_for_two_level_layout(self, tmp_path: Path) -> None:
        path = tmp_path / "org" / "model" / "x-Q4_K_M.gguf"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x")
        assert repo_for_model_path(path, tmp_path) == "org/model"

    def test_repo_for_manual_one_level_layout(self, tmp_path: Path) -> None:
        path = tmp_path / "model" / "x-Q4_K_M.gguf"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x")
        assert repo_for_model_path(path, tmp_path) == "model"

    def test_repo_for_path_outside_root_is_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "x-Q4_K_M.gguf"
        path.write_bytes(b"x")
        assert repo_for_model_path(path, tmp_path / "models") == ""

    def test_quant_precision_label_prefers_filename_marker(self, tmp_path: Path) -> None:
        path = tmp_path / "model-Q4_K_M.gguf"
        path.write_bytes(b"x")
        assert quant_precision_label(path) == "Q4_K_M"

    def test_quant_precision_label_keeps_ud_dynamic_name(self, tmp_path: Path) -> None:
        path = tmp_path / "model-UD-Q4_K_XL.gguf"
        path.write_bytes(b"x")
        assert quant_precision_label(path) == "UD-Q4_K_XL"

    def test_quant_precision_label_ignores_full_precision(self, tmp_path: Path) -> None:
        path = tmp_path / "model-F16.gguf"
        path.write_bytes(b"x")
        assert quant_precision_label(path) == ""

    def test_quant_precision_label_unknown_is_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "model.gguf"
        path.write_bytes(b"x")
        assert quant_precision_label(path) == ""


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
