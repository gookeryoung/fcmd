"""fonttool 工具测试。

验证 ``fcmd.tools.system.fonttool``：
- 工具注册（list/status/install/remove 子命令）
- 用户字体目录、安装清单路径与字体 ID 解析
- 下载校验、安装 / 跳过 / 冲突 / 计划模式 / 许可证兜底
- 状态查询、移除与清单清理
- 字体缓存刷新与平台提示
- CLI 端到端输出
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path

import pytest

import fcmd as fx
from fcmd.core.toolkit import run_tool
from fcmd.tools.system import fonttool
from fcmd.tools.system.fonttool import (
    DEFAULT_FONT_IDS,
    FONTS,
    FontSpec,
    FontToolError,
    InstalledFont,
    InstallOutcome,
    InstallReport,
    RemoveOutcome,
    RemoveReport,
    collect_status,
    fcmd_home,
    file_matches,
    format_font_table,
    format_size,
    format_status_report,
    install_fonts,
    license_path,
    load_manifest,
    manifest_path,
    platform_hint,
    refresh_font_cache,
    register_installed_fonts,
    remove_fonts,
    resolve_fonts,
    save_manifest,
    sha256_file,
    unregister_installed_fonts,
    user_font_dir,
)

FAKE_BYTES = b"FAKE-FONT-BYTES-0123456789"
FAKE_SPEC = FontSpec(
    font_id="demo",
    family="Demo Font",
    label="Demo Font",
    kind="mono",
    filename="Demo-Regular.ttf",
    url="https://example.invalid/demo.ttf",
    sha256=hashlib.sha256(FAKE_BYTES).hexdigest(),
    size=len(FAKE_BYTES),
    license_name="SIL Open Font License 1.1",
    license_url="https://example.invalid/OFL.txt",
)


# ============================================================================ #
# 测试辅助
# ============================================================================ #
@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """隔离 ``FCMD_HOME`` / ``XDG_DATA_HOME``，避免触碰真实用户目录。"""
    monkeypatch.setenv("FCMD_HOME", str(tmp_path / "fcmd-home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    return tmp_path


@pytest.fixture
def quiet_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """安装 / 移除测试中不真正调用 fc-cache。"""
    monkeypatch.setattr(fonttool, "refresh_font_cache", lambda *_args, **_kwargs: "已刷新缓存")


def _downloader(data: bytes = FAKE_BYTES):
    """构造写入固定内容的下载函数。"""

    def download(_url: str, target: Path) -> None:
        target.write_bytes(data)

    return download


def _manifest_entry(spec: FontSpec, path: Path, target_dir: Path) -> InstalledFont:
    """构造清单记录。"""
    return InstalledFont(
        font_id=spec.font_id,
        family=spec.family,
        path=str(path),
        sha256=spec.sha256,
        license_name=spec.license_name,
        license_path=str(license_path(target_dir, spec)),
        installed_at="2026-01-01T00:00:00+08:00",
    )


# ============================================================================ #
# 注册验证
# ============================================================================ #
def test_tool_registered() -> None:
    """fonttool 应注册 list / status / install / remove 四个子命令。"""
    assert {"list", "status", "install", "remove"} <= set(fx.list_subcommands("fonttool"))


# ============================================================================ #
# 路径
# ============================================================================ #
class TestPaths:
    """用户字体目录与清单路径。"""

    def test_linux_default(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """未设置 XDG_DATA_HOME 时落到 ~/.local/share/fonts。"""
        result = user_font_dir("linux", env={}, home=tmp_path)
        assert result == tmp_path / ".local" / "share" / "fonts"

    def test_linux_xdg(self, tmp_path: Path) -> None:
        """XDG_DATA_HOME 优先。"""
        result = user_font_dir("linux", env={"XDG_DATA_HOME": str(tmp_path / "data")}, home=tmp_path)
        assert result == tmp_path / "data" / "fonts"

    def test_macos(self, tmp_path: Path) -> None:
        """macOS 使用 ~/Library/Fonts。"""
        assert user_font_dir("darwin", env={}, home=tmp_path) == tmp_path / "Library" / "Fonts"

    def test_windows_localappdata(self, tmp_path: Path) -> None:
        """Windows 使用 %LOCALAPPDATA%。"""
        result = user_font_dir("win32", env={"LOCALAPPDATA": str(tmp_path / "local")}, home=tmp_path)
        assert result == tmp_path / "local" / "Microsoft" / "Windows" / "Fonts"

    def test_windows_fallback(self, tmp_path: Path) -> None:
        """缺少 LOCALAPPDATA 时回落到 ~/AppData/Local。"""
        result = user_font_dir("win32", env={}, home=tmp_path)
        assert result == tmp_path / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts"

    def test_fcmd_home_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """FCMD_HOME 覆盖默认目录。"""
        monkeypatch.setenv("FCMD_HOME", str(tmp_path / "custom"))
        assert fcmd_home() == tmp_path / "custom"
        assert manifest_path() == tmp_path / "custom" / "fonts" / "manifest.json"

    def test_fcmd_home_default(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """未设置 FCMD_HOME 时使用 ~/.fcmd。"""
        monkeypatch.delenv("FCMD_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert fcmd_home() == tmp_path / ".fcmd"


# ============================================================================ #
# 字体清单解析与格式化
# ============================================================================ #
class TestResolveFonts:
    """字体 ID 解析。"""

    def test_default(self) -> None:
        """缺省安装两款默认字体。"""
        assert [spec.font_id for spec in resolve_fonts()] == list(DEFAULT_FONT_IDS)

    def test_explicit_and_dedup(self) -> None:
        """显式 ID 去重且保序。"""
        specs = resolve_fonts(["noto-sans-sc", "cascadia-code", "noto-sans-sc"])
        assert [spec.font_id for spec in specs] == ["noto-sans-sc", "cascadia-code"]

    def test_comma_separated(self) -> None:
        """支持逗号分隔。"""
        specs = resolve_fonts(["cascadia-code,noto-sans-sc"])
        assert len(specs) == 2

    def test_unknown(self) -> None:
        """未知字体报错并列出可用项。"""
        with pytest.raises(FontToolError, match="未知字体"):
            resolve_fonts(["nope"])

    def test_empty(self) -> None:
        """全部为空时报错。"""
        with pytest.raises(FontToolError, match="未指定有效字体"):
            resolve_fonts(["", " , "])

    def test_registry_defaults_exist(self) -> None:
        """默认字体都在注册表中。"""
        assert set(DEFAULT_FONT_IDS) <= set(FONTS)


class TestFormatting:
    """纯格式化函数。"""

    def test_format_size(self) -> None:
        """字节 / KB / MB 三档。"""
        assert format_size(512) == "512 B"
        assert format_size(2048) == "2.0 KB"
        assert format_size(2 * 1024 * 1024) == "2.0 MB"

    def test_font_table(self) -> None:
        """表格包含 ID、字体名与中文类型。"""
        table = format_font_table(list(FONTS.values()))
        assert "cascadia-code" in table
        assert "Cascadia Code" in table
        assert "等宽" in table
        assert "中文" in table

    def test_status_report(self, tmp_path: Path) -> None:
        """状态报告包含平台、目录、清单与逐字体状态。"""
        statuses = collect_status(list(FONTS.values()), font_dir=tmp_path, manifest={})
        report = format_status_report(
            statuses,
            target_dir=tmp_path,
            manifest_file=tmp_path / "manifest.json",
            system="linux",
        )
        assert "平台: linux" in report
        assert str(tmp_path) in report
        assert "未安装" in report

    def test_platform_hint(self) -> None:
        """Linux 有提示，Windows 无提示。"""
        assert "Xft" in platform_hint("linux")
        assert platform_hint("win32") == ""


# ============================================================================ #
# 校验
# ============================================================================ #
class TestVerification:
    """SHA256 与文件匹配。"""

    def test_sha256_file(self, tmp_path: Path) -> None:
        """摘要与 hashlib 一致。"""
        path = tmp_path / "x.bin"
        path.write_bytes(FAKE_BYTES)
        assert sha256_file(path) == hashlib.sha256(FAKE_BYTES).hexdigest()

    def test_file_matches_true(self, tmp_path: Path) -> None:
        """内容一致时返回 True。"""
        path = tmp_path / "x.ttf"
        path.write_bytes(FAKE_BYTES)
        assert file_matches(path, FAKE_SPEC) is True

    def test_file_matches_size_mismatch(self, tmp_path: Path) -> None:
        """大小不一致时返回 False。"""
        path = tmp_path / "x.ttf"
        path.write_bytes(FAKE_BYTES + b"x")
        assert file_matches(path, FAKE_SPEC) is False

    def test_file_matches_hash_mismatch(self, tmp_path: Path) -> None:
        """大小相同但内容不同返回 False。"""
        path = tmp_path / "x.ttf"
        path.write_bytes(b"x" * len(FAKE_BYTES))
        assert file_matches(path, FAKE_SPEC) is False

    def test_file_matches_missing(self, tmp_path: Path) -> None:
        """文件不存在返回 False。"""
        assert file_matches(tmp_path / "missing.ttf", FAKE_SPEC) is False

    def test_verify_size_error(self, tmp_path: Path) -> None:
        """下载大小不符时抛出业务错误。"""
        path = tmp_path / "x.ttf"
        path.write_bytes(b"x")
        with pytest.raises(FontToolError, match="大小不符"):
            fonttool._verify_font(path, FAKE_SPEC)

    def test_verify_hash_error(self, tmp_path: Path) -> None:
        """大小相同但哈希不符时抛出业务错误。"""
        path = tmp_path / "x.ttf"
        path.write_bytes(b"x" * len(FAKE_BYTES))
        with pytest.raises(FontToolError, match="SHA256"):
            fonttool._verify_font(path, FAKE_SPEC)

    def test_verify_missing(self, tmp_path: Path) -> None:
        """读取失败时抛出业务错误。"""
        with pytest.raises(FontToolError, match="读取下载文件失败"):
            fonttool._verify_font(tmp_path / "missing.ttf", FAKE_SPEC)


# ============================================================================ #
# 清单读写
# ============================================================================ #
class TestManifest:
    """安装清单持久化。"""

    def test_roundtrip(self, isolated: Path, tmp_path: Path) -> None:
        """保存后可原样读回。"""
        entry = _manifest_entry(FAKE_SPEC, tmp_path / FAKE_SPEC.filename, tmp_path)
        save_manifest({"demo": entry})
        assert load_manifest() == {"demo": entry}

    def test_missing_file(self, isolated: Path) -> None:
        """清单不存在时返回空字典。"""
        assert load_manifest() == {}

    def test_corrupt_json(self, isolated: Path) -> None:
        """JSON 损坏时返回空字典。"""
        path = manifest_path()
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        assert load_manifest() == {}

    def test_non_dict_payload(self, isolated: Path) -> None:
        """顶层不是对象或 fonts 不是对象时返回空字典。"""
        path = manifest_path()
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(["x"]), encoding="utf-8")
        assert load_manifest() == {}
        path.write_text(json.dumps({"fonts": []}), encoding="utf-8")
        assert load_manifest() == {}

    def test_invalid_entry_skipped(self, isolated: Path) -> None:
        """条目缺少必填字段时跳过该条。"""
        path = manifest_path()
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"version": 1, "fonts": {"demo": {"family": "Demo"}, "bad": "x"}}),
            encoding="utf-8",
        )
        assert load_manifest() == {}

    def test_write_error(self, tmp_path: Path) -> None:
        """父路径被文件占用时抛出业务错误。"""
        blocker = tmp_path / "block"
        blocker.write_text("x", encoding="utf-8")
        with pytest.raises(FontToolError, match="写入安装清单失败"):
            save_manifest({}, blocker / "manifest.json")


# ============================================================================ #
# 安装
# ============================================================================ #
class TestInstall:
    """安装流程。"""

    def test_install_success(self, isolated: Path, quiet_cache: None, tmp_path: Path) -> None:
        """下载、校验、写许可证与清单。"""
        target = tmp_path / "fonts"
        report = install_fonts(
            [FAKE_SPEC],
            font_dir=target,
            downloader=_downloader(),
            license_fetcher=lambda _url: "OFL TEXT",
        )
        assert report.outcomes[0].state == "installed"
        assert report.cache_message == "已刷新缓存"
        destination = target / FAKE_SPEC.filename
        assert destination.read_bytes() == FAKE_BYTES
        assert (target / "demo.LICENSE.txt").read_text(encoding="utf-8") == "OFL TEXT\n"
        manifest = load_manifest()
        assert manifest["demo"].path == str(destination)

    def test_install_skips_matching_file(self, isolated: Path, quiet_cache: None, tmp_path: Path) -> None:
        """目标文件已匹配时跳过下载。"""
        target = tmp_path / "fonts"
        target.mkdir()
        (target / FAKE_SPEC.filename).write_bytes(FAKE_BYTES)

        def fail_download(_url: str, _target: Path) -> None:
            raise AssertionError("不应重复下载")

        report = install_fonts(
            [FAKE_SPEC],
            font_dir=target,
            downloader=fail_download,
            license_fetcher=lambda _url: "OFL",
        )
        assert report.outcomes[0].state == "skipped"
        assert report.cache_message == ""

    def test_install_twice_keeps_manifest(self, isolated: Path, quiet_cache: None, tmp_path: Path) -> None:
        """重复安装同一字体不改写已有清单记录。"""
        target = tmp_path / "fonts"
        install_fonts([FAKE_SPEC], font_dir=target, downloader=_downloader(), license_fetcher=lambda _url: "OFL")
        first = load_manifest()["demo"]

        report = install_fonts([FAKE_SPEC], font_dir=target, downloader=_downloader(), license_fetcher=lambda _u: "OFL")
        assert report.outcomes[0].state == "skipped"
        assert load_manifest()["demo"] == first

    def test_install_conflict_without_force(self, isolated: Path, quiet_cache: None, tmp_path: Path) -> None:
        """目标文件内容不一致且未加 --force 时报错。"""
        target = tmp_path / "fonts"
        target.mkdir()
        (target / FAKE_SPEC.filename).write_bytes(b"other")
        with pytest.raises(FontToolError, match="内容不一致"):
            install_fonts([FAKE_SPEC], font_dir=target, downloader=_downloader())

    def test_install_conflict_with_force(self, isolated: Path, quiet_cache: None, tmp_path: Path) -> None:
        """--force 时覆盖已有文件。"""
        target = tmp_path / "fonts"
        target.mkdir()
        (target / FAKE_SPEC.filename).write_bytes(b"other")
        report = install_fonts(
            [FAKE_SPEC],
            font_dir=target,
            force=True,
            downloader=_downloader(),
            license_fetcher=lambda _url: "OFL",
        )
        assert report.outcomes[0].state == "installed"
        assert (target / FAKE_SPEC.filename).read_bytes() == FAKE_BYTES

    def test_install_plan(self, isolated: Path, tmp_path: Path) -> None:
        """计划模式不建目录、不下载、不写清单。"""
        target = tmp_path / "fonts"

        def fail_download(_url: str, _target: Path) -> None:
            raise AssertionError("计划模式不应下载")

        report = install_fonts([FAKE_SPEC], font_dir=target, plan=True, downloader=fail_download)
        assert report.outcomes[0].state == "planned"
        assert not target.exists()
        assert load_manifest() == {}

    def test_install_hash_mismatch_cleans_partial(self, isolated: Path, quiet_cache: None, tmp_path: Path) -> None:
        """下载内容哈希不符时清理 .part 文件且不写清单。"""
        target = tmp_path / "fonts"
        with pytest.raises(FontToolError, match="大小不符"):
            install_fonts(
                [FAKE_SPEC],
                font_dir=target,
                downloader=_downloader(b"bad"),
                license_fetcher=lambda _url: "OFL",
            )
        assert not (target / (FAKE_SPEC.filename + ".part")).exists()
        assert load_manifest() == {}

    def test_install_license_download_failure(self, isolated: Path, quiet_cache: None, tmp_path: Path) -> None:
        """许可证下载失败时写入名称与链接，不阻断安装。"""
        target = tmp_path / "fonts"

        def fail_license(_url: str) -> str:
            raise OSError("network down")

        report = install_fonts(
            [FAKE_SPEC],
            font_dir=target,
            downloader=_downloader(),
            license_fetcher=fail_license,
        )
        assert report.outcomes[0].state == "installed"
        text = (target / "demo.LICENSE.txt").read_text(encoding="utf-8")
        assert "SIL Open Font License 1.1" in text
        assert FAKE_SPEC.license_url in text

    def test_install_license_write_failure(self, isolated: Path, quiet_cache: None, tmp_path: Path) -> None:
        """许可证路径不可写时抛出业务错误（字体已落盘）。"""
        target = tmp_path / "fonts"
        target.mkdir()
        (target / "demo.LICENSE.txt").mkdir()
        with pytest.raises(FontToolError, match="写入许可证失败"):
            install_fonts(
                [FAKE_SPEC],
                font_dir=target,
                downloader=_downloader(),
                license_fetcher=lambda _url: "OFL",
            )

    def test_install_default_downloaders(
        self, isolated: Path, quiet_cache: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """缺省下载器从模块函数解析，便于替换。"""
        target = tmp_path / "fonts"
        monkeypatch.setattr(fonttool, "_download", _downloader())
        monkeypatch.setattr(fonttool, "_fetch_text", lambda _url: "OFL")
        report = install_fonts([FAKE_SPEC], font_dir=target)
        assert report.outcomes[0].state == "installed"


# ============================================================================ #
# 移除
# ============================================================================ #
class TestRemove:
    """移除流程。"""

    def _install(self, target: Path) -> None:
        install_fonts(
            [FAKE_SPEC],
            font_dir=target,
            downloader=_downloader(),
            license_fetcher=lambda _url: "OFL",
        )

    def test_remove_installed(self, isolated: Path, quiet_cache: None, tmp_path: Path) -> None:
        """移除字体、许可证并清空清单。"""
        target = tmp_path / "fonts"
        self._install(target)
        report = remove_fonts(["demo"], font_dir=target)
        assert report.outcomes[0].state == "removed"
        assert report.cache_message == "已刷新缓存"
        assert not (target / FAKE_SPEC.filename).exists()
        assert not (target / "demo.LICENSE.txt").exists()
        assert load_manifest() == {}

    def test_remove_missing_file_cleans_manifest(self, isolated: Path, quiet_cache: None, tmp_path: Path) -> None:
        """文件已丢失时仍清理清单记录。"""
        target = tmp_path / "fonts"
        self._install(target)
        (target / FAKE_SPEC.filename).unlink()
        (target / "demo.LICENSE.txt").unlink()
        report = remove_fonts(["demo"], font_dir=target)
        assert report.outcomes[0].state == "missing"
        assert load_manifest() == {}

    def test_remove_all_managed(self, isolated: Path, quiet_cache: None, tmp_path: Path) -> None:
        """--all-managed 移除清单中的全部字体。"""
        target = tmp_path / "fonts"
        self._install(target)
        other = FontSpec(
            font_id="demo2",
            family="Demo Two",
            label="Demo Two",
            kind="cjk",
            filename="Demo2.ttf",
            url="https://example.invalid/demo2.ttf",
            sha256=hashlib.sha256(b"two").hexdigest(),
            size=3,
            license_name="SIL Open Font License 1.1",
            license_url="https://example.invalid/OFL2.txt",
        )
        install_fonts(
            [other],
            font_dir=target,
            downloader=_downloader(b"two"),
            license_fetcher=lambda _url: "OFL",
        )
        report = remove_fonts(all_managed=True, font_dir=target)
        assert {outcome.font_id for outcome in report.outcomes} == {"demo", "demo2"}
        assert load_manifest() == {}

    def test_remove_plan(self, isolated: Path, quiet_cache: None, tmp_path: Path) -> None:
        """计划模式不删除文件。"""
        target = tmp_path / "fonts"
        self._install(target)
        report = remove_fonts(["demo"], font_dir=target, plan=True)
        assert report.outcomes[0].state == "planned"
        assert (target / FAKE_SPEC.filename).exists()
        assert "demo" in load_manifest()

    def test_remove_known_id_without_manifest(self, isolated: Path, quiet_cache: None, tmp_path: Path) -> None:
        """注册表字体未安装时按默认文件名检查并返回 missing。"""
        report = remove_fonts(["cascadia-code"], font_dir=tmp_path / "fonts")
        assert report.outcomes[0].state == "missing"
        assert report.cache_message == ""

    def test_remove_unknown(self, isolated: Path) -> None:
        """未知字体报错。"""
        with pytest.raises(FontToolError, match="未知字体"):
            remove_fonts(["nope"])

    def test_remove_requires_argument(self, isolated: Path) -> None:
        """未指定字体且未加 --all-managed 时报错。"""
        with pytest.raises(FontToolError, match="--all-managed"):
            remove_fonts()

    def test_remove_empty_ids(self, isolated: Path) -> None:
        """只有空白 ID 时报错。"""
        with pytest.raises(FontToolError, match="未指定有效字体"):
            remove_fonts(["", " "])


# ============================================================================ #
# 状态
# ============================================================================ #
class TestCollectStatus:
    """安装状态判定。"""

    def test_not_installed(self, tmp_path: Path) -> None:
        """文件与清单都不存在 -> 未安装。"""
        statuses = collect_status([FAKE_SPEC], font_dir=tmp_path, manifest={})
        assert statuses[0].state == "未安装"

    def test_unmanaged_installed(self, tmp_path: Path) -> None:
        """文件匹配但无清单记录 -> 已安装（未登记）。"""
        (tmp_path / FAKE_SPEC.filename).write_bytes(FAKE_BYTES)
        statuses = collect_status([FAKE_SPEC], font_dir=tmp_path, manifest={})
        assert statuses[0].state == "已安装（未登记）"
        assert statuses[0].managed is False

    def test_installed(self, tmp_path: Path) -> None:
        """文件匹配且有清单记录 -> 已安装。"""
        path = tmp_path / FAKE_SPEC.filename
        path.write_bytes(FAKE_BYTES)
        entry = _manifest_entry(FAKE_SPEC, path, tmp_path)
        statuses = collect_status([FAKE_SPEC], font_dir=tmp_path, manifest={"demo": entry})
        assert statuses[0].state == "已安装"
        assert statuses[0].managed is True

    def test_missing_record(self, tmp_path: Path) -> None:
        """有清单记录但文件缺失 -> 文件缺失。"""
        entry = _manifest_entry(FAKE_SPEC, tmp_path / FAKE_SPEC.filename, tmp_path)
        statuses = collect_status([FAKE_SPEC], font_dir=tmp_path, manifest={"demo": entry})
        assert statuses[0].state == "文件缺失"

    def test_hash_mismatch(self, tmp_path: Path) -> None:
        """文件存在但内容不一致 -> 校验不一致。"""
        path = tmp_path / FAKE_SPEC.filename
        path.write_bytes(b"x" * len(FAKE_BYTES))
        entry = _manifest_entry(FAKE_SPEC, path, tmp_path)
        statuses = collect_status([FAKE_SPEC], font_dir=tmp_path, manifest={"demo": entry})
        assert statuses[0].state == "校验不一致"


# ============================================================================ #
# 系统集成
# ============================================================================ #
class TestSystemIntegration:
    """字体缓存刷新与平台空实现。"""

    def test_refresh_windows(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Windows 无需 fc-cache。"""
        monkeypatch.setattr(fonttool.sys, "platform", "win32")
        assert refresh_font_cache(tmp_path) == ""

    def test_refresh_macos(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """macOS 无需 fc-cache。"""
        monkeypatch.setattr(fonttool.sys, "platform", "darwin")
        assert refresh_font_cache(tmp_path) == ""

    def test_refresh_no_fc_cache(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Linux 缺少 fc-cache 时返回提示。"""
        monkeypatch.setattr(fonttool.sys, "platform", "linux")
        message = refresh_font_cache(tmp_path, which=lambda _name: None)
        assert "fc-cache" in message

    def test_refresh_success(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """调用 fc-cache -f。"""
        monkeypatch.setattr(fonttool.sys, "platform", "linux")
        calls: list[list[str]] = []

        def runner(args: list[str], **_kwargs: object) -> object:
            calls.append(args)
            return object()

        message = refresh_font_cache(tmp_path, which=lambda _name: "/usr/bin/fc-cache", runner=runner)
        assert message == "已刷新系统字体缓存。"
        assert calls == [["/usr/bin/fc-cache", "-f", str(tmp_path)]]

    def test_refresh_failure(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """执行失败时返回提示而不抛错。"""
        monkeypatch.setattr(fonttool.sys, "platform", "linux")

        def runner(_args: list[str], **_kwargs: object) -> object:
            raise OSError("boom")

        message = refresh_font_cache(tmp_path, which=lambda _name: "/usr/bin/fc-cache", runner=runner)
        assert "刷新字体缓存失败" in message

    def test_refresh_subprocess_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """子进程异常同样降级为提示。"""
        monkeypatch.setattr(fonttool.sys, "platform", "linux")

        def runner(_args: list[str], **_kwargs: object) -> object:
            raise subprocess.SubprocessError("bad")

        message = refresh_font_cache(tmp_path, which=lambda _name: "/usr/bin/fc-cache", runner=runner)
        assert "刷新字体缓存失败" in message

    def test_register_non_windows(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """非 Windows 注册 / 注销为空实现。"""
        monkeypatch.setattr(fonttool.sys, "platform", "linux")
        assert register_installed_fonts([("Demo", tmp_path / "x.ttf")]) is None
        assert unregister_installed_fonts([("Demo", tmp_path / "x.ttf")]) is None
        assert register_installed_fonts([]) is None
        assert unregister_installed_fonts([]) is None


# ============================================================================ #
# 默认下载实现
# ============================================================================ #
class _FakeResponse:
    """``urlopen`` 返回值的替身：支持上下文管理与分块 read。"""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk = self._payload[self._offset :]
            self._offset = len(self._payload)
            return chunk
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class TestDownloaders:
    """默认 ``_download`` / ``_fetch_text``。"""

    def test_download(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """流式写入目标文件。"""
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda _request, timeout: _FakeResponse(FAKE_BYTES),
        )
        target = tmp_path / "font.ttf"
        fonttool._download(FAKE_SPEC.url, target)
        assert target.read_bytes() == FAKE_BYTES

    def test_fetch_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """许可证文本按 UTF-8 解码。"""
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda _request, timeout: _FakeResponse("OFL 文本".encode()),
        )
        assert fonttool._fetch_text(FAKE_SPEC.license_url) == "OFL 文本"


# ============================================================================ #
# CLI
# ============================================================================ #
class TestCli:
    """``fcmd fonttool`` 端到端。"""

    def test_list(self, capsys: pytest.CaptureFixture[str]) -> None:
        """list 输出字体表与默认安装项。"""
        code = run_tool("fonttool", ["list"])
        out = capsys.readouterr().out
        assert code == 0
        assert "cascadia-code" in out
        assert "默认安装" in out

    def test_status(self, isolated: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """status 输出字体目录与状态。"""
        monkeypatch.setattr("fcmd.tools.system.fonttool.sys.platform", "linux")
        code = run_tool("fonttool", ["status"])
        out = capsys.readouterr().out
        assert code == 0
        assert "字体目录" in out
        assert "未安装" in out
        assert "Xft" in out

    def test_status_without_hint(
        self, isolated: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """无平台提示时不追加空行。"""
        monkeypatch.setattr(fonttool, "platform_hint", lambda _system=None: "")
        code = run_tool("fonttool", ["status"])
        out = capsys.readouterr().out
        assert code == 0
        assert "Xft" not in out

    def test_install_plan(self, isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """install --plan 只打印计划。"""
        code = run_tool("fonttool", ["install", "--plan"])
        out = capsys.readouterr().out
        assert code == 0
        assert "计划安装" in out
        assert "未下载" in out

    def test_install_unknown(self, isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """未知字体输出错误。"""
        code = run_tool("fonttool", ["install", "--font", "nope"])
        out = capsys.readouterr().out
        assert code == 0
        assert "未知字体" in out

    def test_install_success_output(
        self, isolated: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """安装成功时输出结果、缓存提示与平台提示。"""
        monkeypatch.setattr("fcmd.tools.system.fonttool.sys.platform", "linux")
        report = InstallReport(
            (InstallOutcome("cascadia-code", "Cascadia Code", Path("/fonts/x.ttf"), "installed"),),
            "已刷新系统字体缓存。",
        )
        monkeypatch.setattr(fonttool, "install_fonts", lambda *_args, **_kwargs: report)
        code = run_tool("fonttool", ["install"])
        out = capsys.readouterr().out
        assert code == 0
        assert "已安装" in out
        assert "已刷新系统字体缓存" in out
        assert "Xft" in out

    def test_remove_all_managed_empty(self, isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """空清单时提示无需移除。"""
        code = run_tool("fonttool", ["remove", "--all-managed", "--plan"])
        out = capsys.readouterr().out
        assert code == 0
        assert "没有需要移除的字体" in out

    def test_remove_plan(self, isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """remove --plan 输出计划状态。"""
        code = run_tool("fonttool", ["remove", "--font", "cascadia-code", "--plan"])
        out = capsys.readouterr().out
        assert code == 0
        assert "文件不存在" in out

    def test_remove_unknown(self, isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """未知字体输出错误。"""
        code = run_tool("fonttool", ["remove", "--font", "nope"])
        out = capsys.readouterr().out
        assert code == 0
        assert "未知字体" in out

    def test_remove_success_output(
        self, isolated: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """移除成功时输出结果与缓存提示。"""
        report = RemoveReport(
            (RemoveOutcome("cascadia-code", Path("/fonts/x.ttf"), "removed"),),
            "已刷新系统字体缓存。",
        )
        monkeypatch.setattr(fonttool, "remove_fonts", lambda *_args, **_kwargs: report)
        code = run_tool("fonttool", ["remove", "--font", "cascadia-code"])
        out = capsys.readouterr().out
        assert code == 0
        assert "已移除" in out
        assert "已刷新系统字体缓存" in out
