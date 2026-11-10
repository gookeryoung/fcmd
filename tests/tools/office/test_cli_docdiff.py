"""docdiff 工具测试。

验证 ``fcmd.tools.office.docdiff`` 模块：
- 工具注册
- 平台 / 输入 / 输出路径校验
- 对比模式规范化
- Word COM 调用流程（用替身替代真实 Word）
- CLI 成功与失败输出
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, override

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.office import docdiff
from fcmd.tools.office.docdiff import (
    COMPARE_MODES,
    DEFAULT_TITLE,
    DocDiffCommand,
    DocDiffError,
    ensure_windows,
    normalize_compare_mode,
    output_filename,
    resolve_output_path,
    validate_documents,
)

STAMP = datetime(2026, 11, 10, 9, 8, 7)
STAMP_TEXT = "20261110_09_08_07"


# ============================================================================ #
# 替身
# ============================================================================ #
class _FakeCompared:
    """Word 返回的对比文档替身。"""

    def __init__(self) -> None:
        self.ShowRevisions: bool | None = None
        self.saved: list[str] = []

    def SaveAs2(self, path: str) -> None:
        self.saved.append(path)


class _SaveFails(_FakeCompared):
    """保存时抛出 OSError 的对比文档。"""

    @override
    def SaveAs2(self, path: str) -> None:
        raise OSError("disk full")


class _FakeDocuments:
    """Word Documents 集合替身。"""

    def __init__(self, app: _FakeApp) -> None:
        self._app = app
        self.opened: list[SimpleNamespace] = []

    def Open(self, path: str) -> SimpleNamespace:
        document = SimpleNamespace(path=path)
        self.opened.append(document)
        return document

    def Close(self, SaveChanges: bool = False) -> None:
        self._app.close_calls.append(SaveChanges)


class _FakeApp:
    """Word Application 替身。"""

    def __init__(self) -> None:
        self.close_calls: list[bool] = []
        self.quit_count = 0
        self.Options: Any = SimpleNamespace(TrackRevisions=False)
        self.Visible = True
        self.DisplayAlerts = True
        self.compare_calls: list[tuple[Any, ...]] = []
        self.result: Any = _FakeCompared()
        self.compare_error: Exception | None = None
        self.Documents = _FakeDocuments(self)

    def CompareDocuments(self, *args: Any) -> Any:
        self.compare_calls.append(args)
        if self.compare_error is not None:
            raise self.compare_error
        return self.result

    def Quit(self) -> None:
        self.quit_count += 1


class _FakeWin32:
    """``win32com.client`` 模块替身。"""

    def __init__(self, app: _FakeApp) -> None:
        self._app = app
        self.requested: list[str] = []
        self.gencache = SimpleNamespace(EnsureDispatch=self._dispatch)

    def _dispatch(self, prog_id: str) -> _FakeApp:
        self.requested.append(prog_id)
        return self._app


class _BrokenWin32:
    """Word 启动失败的 ``win32com.client`` 替身。"""

    def __init__(self) -> None:
        self.gencache = SimpleNamespace(EnsureDispatch=self._raise)

    @staticmethod
    def _raise(_prog_id: str) -> Any:
        raise RuntimeError("no word")


def _prepare_docs(tmp_path: Path) -> tuple[Path, Path]:
    old_doc = tmp_path / "old.docx"
    new_doc = tmp_path / "new.docx"
    old_doc.write_bytes(b"old")
    new_doc.write_bytes(b"new")
    return old_doc, new_doc


def _patch_word(
    monkeypatch: pytest.MonkeyPatch,
    app: _FakeApp,
    *,
    pythoncom: Any = None,
) -> tuple[_FakeWin32, list[bool], list[str]]:
    """把 Word/COM/进程清理替换为可观测的替身。"""
    fake = _FakeWin32(app)
    kills: list[bool] = []
    com_calls: list[str] = []

    class _Com:
        def CoUninitialize(self) -> None:
            com_calls.append("uninit")

    if pythoncom is None:
        pythoncom = _Com()
    monkeypatch.setattr(docdiff, "_load_win32_client", lambda: fake)
    monkeypatch.setattr(docdiff, "_com_initialize", lambda: pythoncom)
    monkeypatch.setattr(docdiff, "kill_word_process", lambda: kills.append(True))
    return fake, kills, com_calls


# ============================================================================ #
# 注册与平台
# ============================================================================ #
def test_tool_registered() -> None:
    """docdiff 应以单命令形态注册。"""
    assert set(_TOOL_REGISTRY["docdiff"]) == {None}


class TestEnsureWindows:
    """平台校验。"""

    def test_accepts_windows(self) -> None:
        ensure_windows("Windows")

    @pytest.mark.parametrize("system", ["Linux", "Darwin"])
    def test_rejects_other_platforms(self, system: str) -> None:
        with pytest.raises(DocDiffError, match="Windows"):
            ensure_windows(system)

    def test_uses_platform_system_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(docdiff.platform, "system", lambda: "Windows")
        ensure_windows()


# ============================================================================ #
# 输入校验
# ============================================================================ #
class TestValidateDocuments:
    """输入文件校验。"""

    def test_missing_old(self, tmp_path: Path) -> None:
        new_doc = tmp_path / "new.docx"
        new_doc.write_bytes(b"new")
        with pytest.raises(DocDiffError, match="旧文件不存在"):
            validate_documents(tmp_path / "missing.docx", new_doc)

    def test_missing_new(self, tmp_path: Path) -> None:
        old_doc = tmp_path / "old.docx"
        old_doc.write_bytes(b"old")
        with pytest.raises(DocDiffError, match="新文件不存在"):
            validate_documents(old_doc, tmp_path / "missing.docx")

    def test_rejects_unsupported_extension(self, tmp_path: Path) -> None:
        old_doc = tmp_path / "old.docx"
        new_doc = tmp_path / "new.txt"
        old_doc.write_bytes(b"old")
        new_doc.write_bytes(b"new")
        with pytest.raises(DocDiffError, match="扩展名不受支持"):
            validate_documents(old_doc, new_doc)

    @pytest.mark.parametrize(("old_name", "new_name"), [("a.docx", "b.docx"), ("a.doc", "b.doc")])
    def test_accepts_supported_extensions(self, tmp_path: Path, old_name: str, new_name: str) -> None:
        old_doc = tmp_path / old_name
        new_doc = tmp_path / new_name
        old_doc.write_bytes(b"old")
        new_doc.write_bytes(b"new")
        validate_documents(old_doc, new_doc)


# ============================================================================ #
# 输出路径
# ============================================================================ #
class TestOutputPath:
    """结果文件名与路径解析。"""

    def test_output_filename_uses_timestamp(self) -> None:
        assert output_filename("合同", STAMP) == f"合同@{STAMP_TEXT}.docx"

    def test_output_filename_default_title_and_now(self) -> None:
        name = output_filename()
        assert name.startswith(f"{DEFAULT_TITLE}@")
        assert name.endswith(".docx")

    def test_defaults_to_new_document_directory(self, tmp_path: Path) -> None:
        new_doc = tmp_path / "docs" / "new.docx"
        assert resolve_output_path(new_doc, None, title="T", timestamp=STAMP) == (
            new_doc.parent / f"T@{STAMP_TEXT}.docx"
        )

    def test_existing_directory_gets_generated_name(self, tmp_path: Path) -> None:
        output = tmp_path / "out"
        output.mkdir()
        assert resolve_output_path(tmp_path / "new.docx", output, title="T", timestamp=STAMP) == (
            output / f"T@{STAMP_TEXT}.docx"
        )

    def test_existing_file_without_suffix_is_output_file(self, tmp_path: Path) -> None:
        output = tmp_path / "result"
        output.write_bytes(b"")
        assert resolve_output_path(tmp_path / "new.docx", output, timestamp=STAMP) == output

    @pytest.mark.parametrize("name", ["custom.docx", "custom.doc"])
    def test_existing_word_suffix_is_output_file(self, tmp_path: Path, name: str) -> None:
        output = tmp_path / name
        assert resolve_output_path(tmp_path / "new.docx", output, timestamp=STAMP) == output

    def test_other_path_is_treated_as_directory(self, tmp_path: Path) -> None:
        output = tmp_path / "out" / "nested"
        assert resolve_output_path(tmp_path / "new.docx", output, title="T", timestamp=STAMP) == (
            output / f"T@{STAMP_TEXT}.docx"
        )


# ============================================================================ #
# 对比模式
# ============================================================================ #
class TestCompareMode:
    """对比模式规范化。"""

    @pytest.mark.parametrize(("raw", "expected"), [("original", "original"), (" REVISED ", "revised")])
    def test_valid(self, raw: str, expected: str) -> None:
        assert normalize_compare_mode(raw) == expected

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="对比模式"):
            normalize_compare_mode("ultra")

    def test_modes_cover_literal(self) -> None:
        assert COMPARE_MODES == ("original", "revised")


# ============================================================================ #
# Word 自动化
# ============================================================================ #
class TestCompareWithWord:
    """Word COM 流程（不启动真实 Word）。"""

    def test_success_revised_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        old_doc, new_doc = _prepare_docs(tmp_path)
        app = _FakeApp()
        fake, kills, com_calls = _patch_word(monkeypatch, app)

        target = tmp_path / "nested" / "result.docx"
        result = docdiff.compare_with_word(old_doc, new_doc, target, show_changes=False, compare_mode="revised")

        assert result == target
        assert target.parent.is_dir()
        assert fake.requested == ["Word.Application"]
        assert app.Visible is False
        assert app.DisplayAlerts is False
        assert app.Options.TrackRevisions is True
        assert [doc.path for doc in app.Documents.opened] == [str(old_doc.resolve()), str(new_doc.resolve())]
        assert app.compare_calls[0][2:] == (0, 2, True)
        compared = app.result
        assert compared.ShowRevisions is False
        assert compared.saved == [str(target)]
        assert app.close_calls == [False]
        assert app.quit_count == 1
        assert com_calls == ["uninit"]
        assert kills == [True]

    def test_success_original_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        old_doc, new_doc = _prepare_docs(tmp_path)
        app = _FakeApp()
        _patch_word(monkeypatch, app)

        docdiff.compare_with_word(old_doc, new_doc, tmp_path / "result.docx")

        assert app.compare_calls[0][2:] == (0, 0, True)
        assert app.result.ShowRevisions is True

    def test_track_revisions_attribute_errors_are_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        old_doc, new_doc = _prepare_docs(tmp_path)
        app = _FakeApp()
        app.Options = object()
        _patch_word(monkeypatch, app)

        docdiff.compare_with_word(old_doc, new_doc, tmp_path / "result.docx")

        assert app.result.saved == [str(tmp_path / "result.docx")]

    def test_start_word_failure_cleans_up_nothing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        old_doc, new_doc = _prepare_docs(tmp_path)
        kills: list[bool] = []
        monkeypatch.setattr(docdiff, "_load_win32_client", _BrokenWin32)
        monkeypatch.setattr(docdiff, "_com_initialize", lambda: None)
        monkeypatch.setattr(docdiff, "kill_word_process", lambda: kills.append(True))

        with pytest.raises(DocDiffError, match="启动 Word 失败"):
            docdiff.compare_with_word(old_doc, new_doc, tmp_path / "result.docx")
        assert kills == []

    def test_compare_error_wrapped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        old_doc, new_doc = _prepare_docs(tmp_path)
        app = _FakeApp()
        app.compare_error = RuntimeError("boom")
        _patch_word(monkeypatch, app)

        with pytest.raises(DocDiffError, match="对比失败: boom"):
            docdiff.compare_with_word(old_doc, new_doc, tmp_path / "result.docx")
        assert app.quit_count == 1

    def test_missing_compared_document_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        old_doc, new_doc = _prepare_docs(tmp_path)
        app = _FakeApp()
        app.result = None
        _patch_word(monkeypatch, app)

        with pytest.raises(DocDiffError, match="未返回对比文档"):
            docdiff.compare_with_word(old_doc, new_doc, tmp_path / "result.docx")

    def test_save_error_wrapped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        old_doc, new_doc = _prepare_docs(tmp_path)
        app = _FakeApp()
        app.result = _SaveFails()
        _patch_word(monkeypatch, app)

        with pytest.raises(DocDiffError, match="保存对比结果失败"):
            docdiff.compare_with_word(old_doc, new_doc, tmp_path / "result.docx")
        assert app.quit_count == 1


class TestWordHelpers:
    """Word 辅助函数。"""

    def test_load_win32_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sentinel = object()
        monkeypatch.setattr(docdiff.importlib, "import_module", lambda _name: sentinel)
        assert docdiff._load_win32_client() is sentinel

    def test_load_win32_client_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(_name: str) -> Any:
            raise ImportError("missing")

        monkeypatch.setattr(docdiff.importlib, "import_module", boom)
        with pytest.raises(DocDiffError, match="pywin32"):
            docdiff._load_win32_client()

    def test_com_initialize_without_pythoncom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(_name: str) -> Any:
            raise ImportError("missing")

        monkeypatch.setattr(docdiff.importlib, "import_module", boom)
        assert docdiff._com_initialize() is None

    def test_com_initialize_and_uninitialize(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []
        fake = SimpleNamespace(
            CoInitialize=lambda: calls.append("init"),
            CoUninitialize=lambda: calls.append("uninit"),
        )
        monkeypatch.setattr(docdiff.importlib, "import_module", lambda _name: fake)

        assert docdiff._com_initialize() is fake
        docdiff._com_uninitialize(fake)
        docdiff._com_uninitialize(None)

        assert calls == ["init", "uninit"]

    def test_com_uninitialize_ignores_errors(self) -> None:
        def boom() -> None:
            raise RuntimeError("com gone")

        docdiff._com_uninitialize(SimpleNamespace(CoUninitialize=boom))

    def test_release_word_none_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        kills: list[bool] = []
        monkeypatch.setattr(docdiff, "kill_word_process", lambda: kills.append(True))
        docdiff._release_word(None)
        assert kills == []

    def test_release_word_swallows_cleanup_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _BadDocuments:
            @staticmethod
            def Close(**_kwargs: Any) -> None:
                raise OSError("close failed")

        class _BadApp:
            Documents = _BadDocuments()

            @staticmethod
            def Quit() -> None:
                raise RuntimeError("quit failed")

        kills: list[bool] = []
        monkeypatch.setattr(docdiff, "kill_word_process", lambda: kills.append(True))

        docdiff._release_word(_BadApp())

        assert kills == [True]

    def test_kill_word_process_runs_taskkill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[tuple[list[str], dict[str, Any]]] = []
        monkeypatch.setattr(
            docdiff.subprocess,
            "run",
            lambda args, **kwargs: calls.append((args, kwargs)),
        )

        docdiff.kill_word_process()

        assert calls == [(["taskkill", "/f", "/t", "/im", "WINWORD.EXE"], {"capture_output": True, "check": False})]

    def test_kill_word_process_ignores_oserror(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*args: Any, **kwargs: Any) -> None:
            raise OSError("taskkill missing")

        monkeypatch.setattr(docdiff.subprocess, "run", boom)
        docdiff.kill_word_process()


# ============================================================================ #
# 命令与 CLI
# ============================================================================ #
class TestCommand:
    """DocDiffCommand。"""

    def test_resolve_output(self, tmp_path: Path) -> None:
        new_doc = tmp_path / "new.docx"
        command = DocDiffCommand(tmp_path / "old.docx", new_doc, title="T")
        assert command.resolve_output(STAMP) == new_doc.parent / f"T@{STAMP_TEXT}.docx"

    def test_run_delegates_to_word(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        old_doc, new_doc = _prepare_docs(tmp_path)
        captured: dict[str, Any] = {}

        def fake_compare(
            old: Path,
            new: Path,
            output: Path,
            *,
            show_changes: bool,
            compare_mode: str,
        ) -> Path:
            captured.update(old=old, new=new, output=output, show_changes=show_changes, compare_mode=compare_mode)
            return output

        monkeypatch.setattr(docdiff, "ensure_windows", lambda: None)
        monkeypatch.setattr(docdiff, "compare_with_word", fake_compare)

        command = DocDiffCommand(
            old_doc,
            new_doc,
            tmp_path / "out",
            title="T",
            show_changes=False,
            compare_mode="revised",
        )
        result = command.run()

        assert result.parent == tmp_path / "out"
        assert result.name.startswith("T@")
        assert captured == {
            "old": old_doc,
            "new": new_doc,
            "output": result,
            "show_changes": False,
            "compare_mode": "revised",
        }

    def test_run_validates_inputs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(docdiff, "ensure_windows", lambda: None)
        with pytest.raises(DocDiffError, match="旧文件不存在"):
            DocDiffCommand(tmp_path / "missing.docx", tmp_path / "new.docx").run()


class TestCli:
    """CLI 输出。"""

    def test_success_prints_target(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
        target = tmp_path / "result.docx"

        class _Command:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                pass

            @staticmethod
            def run() -> Path:
                return target

        monkeypatch.setattr(docdiff, "DocDiffCommand", _Command)

        docdiff.compare_docs(tmp_path / "old.docx", tmp_path / "new.docx")

        out = capsys.readouterr().out
        assert "对比完成" in out
        assert str(target) in out

    def test_error_prints_message(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
        class _Command:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                pass

            @staticmethod
            def run() -> Path:
                raise DocDiffError("boom")

        monkeypatch.setattr(docdiff, "DocDiffCommand", _Command)

        docdiff.compare_docs(tmp_path / "old.docx", tmp_path / "new.docx")

        out = capsys.readouterr().out
        assert "错误" in out
        assert "boom" in out
