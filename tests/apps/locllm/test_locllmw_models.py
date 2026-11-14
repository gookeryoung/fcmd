"""locllmw 模型页（整合原 llmdownw）测试。

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
from typing import Any

import pytest

from fcmd.apps.locllm._locllmw_models import (
    DEFAULT_MODEL_TAB,
    MODEL_TAB_NAMES,
    DownloadGuiSettings,
    detail_pairs,
    filter_models,
    local_row,
    model_row,
    open_path,
    parse_patterns,
    settings_from_dict,
    settings_to_dict,
    sort_models,
    sync_tree_rows,
)
from fcmd.apps.locllm.llmdown import LocalModel, ModelInfo, ModelSummary
from fcmd.core.toolkit import _TOOL_REGISTRY


class TestRegistration:
    def test_removed_from_registry(self) -> None:
        assert "llmdownw" not in _TOOL_REGISTRY


class TestModelTabLayout:
    """子页顺序与默认页由常量统一声明，防止只改创建顺序却留下索引硬编码。"""

    def test_tab_order(self) -> None:
        assert MODEL_TAB_NAMES == ("浏览", "下载", "已安装", "转换", "量化")

    def test_default_tab_is_browse(self) -> None:
        assert DEFAULT_MODEL_TAB == "浏览"
        assert MODEL_TAB_NAMES.index(DEFAULT_MODEL_TAB) == 0


class TestSettings:
    def test_defaults(self) -> None:
        assert settings_from_dict(None) == DownloadGuiSettings()

    def test_roundtrip(self) -> None:
        settings = DownloadGuiSettings(
            source="hf-mirror",
            workers=8,
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
                "verify": "yes",
                "include": 1,
                "exclude": "*.bin",
            }
        )
        assert settings.source == "modelscope"
        assert settings.workers == 16
        assert settings.verify is True
        assert settings.include == ""
        assert settings.exclude == "*.bin"

    def test_bool_workers_rejected(self) -> None:
        assert settings_from_dict({"workers": True}).workers == 4


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


class _FakeTree:
    """最小 Treeview 替身：记录 iid 顺序、行值与各类操作次数。"""

    def __init__(self, rows: list[tuple[str, tuple[str, ...]]] | None = None) -> None:
        self.rows = list(rows or [])
        self.inserts = 0
        self.deletes = 0
        self.moves = 0
        self.updates = 0

    def get_children(self) -> tuple[str, ...]:
        return tuple(iid for iid, _ in self.rows)

    def exists(self, iid: str) -> bool:
        return any(row[0] == iid for row in self.rows)

    def item(self, iid: str, option: str | None = None, values: tuple[str, ...] | None = None) -> Any:
        if values is not None:
            self.updates += 1
            self.rows = [(key, tuple(values)) if key == iid else (key, current) for key, current in self.rows]
            return None
        assert option == "values"
        return next(current for key, current in self.rows if key == iid)

    def insert(self, _parent: str, index: int, iid: str, values: tuple[str, ...]) -> None:
        self.inserts += 1
        self.rows.insert(index, (iid, tuple(values)))

    def delete(self, iid: str) -> None:
        self.deletes += 1
        self.rows = [row for row in self.rows if row[0] != iid]

    def move(self, iid: str, _parent: str, index: int) -> None:
        self.moves += 1
        row = next(row for row in self.rows if row[0] == iid)
        self.rows.remove(row)
        self.rows.insert(index, row)


class TestSyncTreeRows:
    """增量同步：只处理变化项，保留未变化的行（选中与滚动不受影响）。"""

    def test_insert_update_and_remove(self) -> None:
        tree = _FakeTree([("a", ("a", "1")), ("b", ("b", "1"))])
        sync_tree_rows(tree, [("b", ("b", "2")), ("c", ("c", "1"))])
        assert tree.rows == [("b", ("b", "2")), ("c", ("c", "1"))]
        assert (tree.inserts, tree.deletes, tree.updates, tree.moves) == (1, 1, 1, 0)

    def test_reorder_without_rebuild(self) -> None:
        tree = _FakeTree([("a", ("a",)), ("b", ("b",)), ("c", ("c",))])
        sync_tree_rows(tree, [("c", ("c",)), ("a", ("a",)), ("b", ("b",))])
        assert [iid for iid, _ in tree.rows] == ["c", "a", "b"]
        assert (tree.inserts, tree.deletes) == (0, 0)

    def test_identical_rows_do_nothing(self) -> None:
        rows: list[tuple[str, tuple[str, ...]]] = [("a", ("a", "1")), ("b", ("b", "2"))]
        tree = _FakeTree(rows)
        sync_tree_rows(tree, rows)
        assert tree.rows == rows
        assert (tree.inserts, tree.deletes, tree.updates, tree.moves) == (0, 0, 0, 0)


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
