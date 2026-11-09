"""checksum 工具测试。

验证 ``fcmd.tools.ui.checksum`` 模块：
- 工具注册
- 算法规范化 / 字符串与文件摘要计算
- 期望值比较与结果文本生成
- 设置文件读写与异常回退
- GUI 启动入口的降级行为
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.ui import checksum
from fcmd.tools.ui.checksum import (
    ALGORITHMS,
    ChecksumSettings,
    calculate_file_checksum,
    calculate_string_checksum,
    config_path,
    format_result,
    load_settings,
    normalize_algorithm,
    save_settings,
    valid_geometry,
    verify_checksum,
)


# ============================================================================ #
# 注册验证
# ============================================================================ #
def test_tool_registered() -> None:
    """checksum 应以单命令形态注册。"""
    assert set(_TOOL_REGISTRY["checksum"]) == {None}


# ============================================================================ #
# 配置路径与设置文件
# ============================================================================ #
class TestSettings:
    """设置文件读写。"""

    def test_config_path_uses_fcmd_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """FCMD_HOME 优先。"""
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert config_path() == tmp_path / "ui" / "checksum.json"

    def test_config_path_falls_back_to_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """未设置 FCMD_HOME 时使用 ``~/.fcmd``。"""
        monkeypatch.delenv("FCMD_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert config_path() == tmp_path / ".fcmd" / "ui" / "checksum.json"

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        """保存后能原样读回（父目录自动创建）。"""
        path = tmp_path / "nested" / "checksum.json"
        expected = ChecksumSettings(algorithm="SHA256", compare=True, geometry="720x480+10+20")
        save_settings(expected, path)
        assert path.is_file()
        assert load_settings(path) == expected

    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        """文件不存在时返回默认设置。"""
        assert load_settings(tmp_path / "missing.json") == ChecksumSettings()

    def test_load_invalid_json_returns_defaults(self, tmp_path: Path) -> None:
        """JSON 损坏时返回默认设置。"""
        path = tmp_path / "bad.json"
        path.write_text("{oops", encoding="utf-8")
        assert load_settings(path) == ChecksumSettings()

    def test_load_non_object_returns_defaults(self, tmp_path: Path) -> None:
        """JSON 顶层不是对象时返回默认设置。"""
        path = tmp_path / "list.json"
        path.write_text("[]", encoding="utf-8")
        assert load_settings(path) == ChecksumSettings()

    def test_load_wrong_types_are_filtered(self, tmp_path: Path) -> None:
        """字段类型不符时逐项回退默认值。"""
        path = tmp_path / "wrong.json"
        path.write_text('{"algorithm": 1, "compare": "yes", "geometry": 5}', encoding="utf-8")
        assert load_settings(path) == ChecksumSettings()

    def test_load_invalid_algorithm_is_replaced(self, tmp_path: Path) -> None:
        """未知算法回退默认值。"""
        path = tmp_path / "invalid.json"
        path.write_text('{"algorithm": "crc32", "compare": true, "geometry": "640x480"}', encoding="utf-8")
        assert load_settings(path) == ChecksumSettings(algorithm="MD5", compare=True, geometry="640x480")

    def test_load_algorithm_is_normalized(self, tmp_path: Path) -> None:
        """保存的小写算法名规范为展示名。"""
        path = tmp_path / "lower.json"
        path.write_text('{"algorithm": "blake2b"}', encoding="utf-8")
        assert load_settings(path).algorithm == "Blake2b"

    def test_save_ignores_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """写入失败时静默忽略，不影响窗口关闭。"""

        def boom(*args: Any, **kwargs: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", boom)
        save_settings(ChecksumSettings(), tmp_path / "x.json")  # 不应抛出


class TestValidGeometry:
    """窗口 geometry 校验。"""

    @pytest.mark.parametrize("text", ["720x480", "720x480+300+200", "800x600-10-20"])
    def test_valid(self, text: str) -> None:
        assert valid_geometry(text) == text

    @pytest.mark.parametrize("text", [None, "", "720", "720x480abc", "720 x 480"])
    def test_invalid(self, text: str | None) -> None:
        assert valid_geometry(text) is None


# ============================================================================ #
# 摘要计算
# ============================================================================ #
class TestNormalizeAlgorithm:
    """算法名规范化。"""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("MD5", "MD5"),
            ("md5", "MD5"),
            (" SHA256 ", "SHA256"),
            ("blake2b", "Blake2b"),
            ("BLAKE2S", "Blake2s"),
        ],
    )
    def test_valid(self, name: str, expected: str) -> None:
        assert normalize_algorithm(name) == expected

    @pytest.mark.parametrize("name", ["", "crc32", "sha3-512"])
    def test_invalid(self, name: str) -> None:
        with pytest.raises(ValueError):
            normalize_algorithm(name)


class TestChecksums:
    """字符串与文件摘要。"""

    def test_string_md5_known_vector(self) -> None:
        assert calculate_string_checksum("hello world", "MD5") == "5eb63bbbe01eeed093cb22bb8f5acdc3"

    def test_string_sha256_known_vector(self) -> None:
        assert (
            calculate_string_checksum("hello world", "SHA256")
            == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        )

    @pytest.mark.parametrize("algorithm", ALGORITHMS)
    def test_string_matches_hashlib(self, algorithm: str) -> None:
        """全部算法与 hashlib 直接计算一致（UTF-8 编码）。"""
        expected = hashlib.new(algorithm.lower(), "中文 abc".encode()).hexdigest()
        assert calculate_string_checksum("中文 abc", algorithm) == expected

    def test_string_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            calculate_string_checksum("", "MD5")

    def test_string_unknown_algorithm_raises(self) -> None:
        with pytest.raises(ValueError):
            calculate_string_checksum("abc", "crc32")

    def test_file_checksum(self, tmp_path: Path) -> None:
        path = tmp_path / "data.bin"
        path.write_bytes(b"hello world")
        assert calculate_file_checksum(path, "SHA256") == hashlib.sha256(b"hello world").hexdigest()

    def test_file_checksum_small_chunks(self, tmp_path: Path) -> None:
        """分块读取（小于文件大小）结果与一次性计算一致。"""
        path = tmp_path / "chunked.bin"
        path.write_bytes(b"0123456789" * 100)
        assert calculate_file_checksum(path, "MD5", chunk_size=7) == hashlib.md5(b"0123456789" * 100).hexdigest()

    def test_file_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            calculate_file_checksum(tmp_path / "missing.bin", "MD5")

    def test_file_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            calculate_file_checksum(tmp_path, "MD5")


class TestVerify:
    """期望值比较。"""

    @pytest.mark.parametrize(
        ("calculated", "expected"),
        [
            ("abc123", "abc123"),
            ("ABC123", "abc123"),
            ("abc123", "  ABC123  "),
        ],
    )
    def test_match(self, calculated: str, expected: str) -> None:
        assert verify_checksum(calculated, expected)

    def test_mismatch(self) -> None:
        assert not verify_checksum("abc123", "abc124")


class TestFormatResult:
    """结果文本生成。"""

    def test_without_comparison(self) -> None:
        assert format_result("abc123") == "abc123"

    def test_passed(self) -> None:
        assert format_result("abc123", "ABC123") == f"abc123\n{checksum.VERIFY_PASSED}"

    def test_failed(self) -> None:
        assert format_result("abc123", "def456") == f"abc123\n{checksum.VERIFY_FAILED}"


# ============================================================================ #
# GUI 入口
# ============================================================================ #
class TestGuiEntry:
    """GUI 启动入口（不真正创建窗口）。"""

    def test_run_gui_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """tkinter 不可用时打印错误并退出。"""

        def fake_window(_path: Path | None) -> None:
            raise ImportError("no tkinter")

        monkeypatch.setattr(checksum, "ChecksumWindow", fake_window)
        checksum.run_gui()  # 不应抛出

    def test_run_gui_starts_mainloop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常创建窗口后进入 mainloop。"""
        calls: list[str] = []

        class _Root:
            def mainloop(self) -> None:
                calls.append("mainloop")

        class _Window:
            root = _Root()

        monkeypatch.setattr(checksum, "ChecksumWindow", lambda _path: _Window())
        checksum.run_gui()
        assert calls == ["mainloop"]

    def test_launch_cmd_calls_run_gui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []
        monkeypatch.setattr(checksum, "run_gui", lambda: calls.append(True))
        checksum.launch_cmd()
        assert calls == [True]
