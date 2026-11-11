"""mirrortoolw 工具测试。

验证 ``fcmd.tools.dev.mirrortoolw`` 模块：
- 工具注册
- 设置文件读写、超时校验与窗口 geometry
- 服务候选表 / 探测 / 过滤 / 推荐 / 应用 / 配置位置
- GUI 启动入口的降级行为
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import fcmd.tools.dev._envdev_mirrors as mirrors_mod
from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.infras.net import UrlProbe
from fcmd.tools.dev import mirrortoolw
from fcmd.tools.dev.mirrortoolw import (
    DEFAULT_GEOMETRY,
    DEFAULT_TIMEOUT,
    MAX_TIMEOUT,
    MIN_TIMEOUT,
    MODE_SERVICE,
    MODE_SITES,
    SERVICE_LABELS,
    MirrorResult,
    MirrorToolSettings,
    apply_service_mirror,
    config_location,
    config_path,
    filter_results,
    load_settings,
    normalize_timeout,
    probe_service,
    probe_sites,
    recommend_mirror,
    save_settings,
    service_candidates,
    valid_geometry,
)


# ============================================================================ #
# 注册验证
# ============================================================================ #
def test_tool_registered() -> None:
    """mirrortoolw 应以单命令形态注册。"""
    assert set(_TOOL_REGISTRY["mirrortoolw"]) == {None}


def test_service_labels_match_cli_candidates() -> None:
    """窗口展示的服务集合与 CLI 候选表严格一致。"""
    assert set(SERVICE_LABELS) == set(mirrors_mod._MIRROR_SERVICE_CANDIDATES)
    assert set(SERVICE_LABELS) == set(mirrors_mod._MIRROR_SERVICE_APPLY)


# ============================================================================ #
# 配置路径与设置文件
# ============================================================================ #
class TestSettings:
    """设置文件读写。"""

    def test_config_path_uses_fcmd_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert config_path() == tmp_path / "ui" / "mirrortoolw.json"

    def test_config_path_falls_back_to_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("FCMD_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert config_path() == tmp_path / ".fcmd" / "ui" / "mirrortoolw.json"

    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        assert load_settings(tmp_path / "missing.json") == MirrorToolSettings()

    def test_load_corrupt_json_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_settings(path) == MirrorToolSettings()

    def test_load_non_dict_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "list.json"
        path.write_text("[]", encoding="utf-8")
        assert load_settings(path) == MirrorToolSettings()

    def test_load_restores_values(self, tmp_path: Path) -> None:
        path = tmp_path / "ok.json"
        path.write_text(
            '{"mode": "sites", "service": "rustup", "timeout": 5.5, "source": "builtin", '
            '"search": "tuna", "geometry": "900x620+10+20"}',
            encoding="utf-8",
        )
        assert load_settings(path) == MirrorToolSettings(
            mode=MODE_SITES,
            service="rustup",
            timeout=5.5,
            source="builtin",
            search="tuna",
            geometry="900x620+10+20",
        )

    def test_load_invalid_fields_fall_back(self, tmp_path: Path) -> None:
        """非法模式 / 服务 / 超时 / 来源 / 搜索 / geometry 逐项回退默认值。"""
        path = tmp_path / "invalid.json"
        path.write_text(
            '{"mode": "x", "service": "uv", "timeout": 0.1, "source": "bad", "search": 1, "geometry": "bad"}',
            encoding="utf-8",
        )
        assert load_settings(path) == MirrorToolSettings()

    def test_load_without_timeout_keeps_default(self, tmp_path: Path) -> None:
        path = tmp_path / "partial.json"
        path.write_text('{"service": "go"}', encoding="utf-8")
        assert load_settings(path).timeout == DEFAULT_TIMEOUT

    def test_load_accepts_numeric_timeout_text(self, tmp_path: Path) -> None:
        path = tmp_path / "text.json"
        path.write_text('{"timeout": "4.5"}', encoding="utf-8")
        assert load_settings(path).timeout == 4.5

    def test_save_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "settings.json"
        settings = MirrorToolSettings(
            mode=MODE_SITES,
            service="npm",
            timeout=2.0,
            source="builtin",
            search="ali",
            geometry="900x620",
        )
        save_settings(settings, path)
        assert load_settings(path) == settings

    def test_save_ignores_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """写入失败时静默忽略，不影响窗口关闭。"""

        def boom(*args: Any, **kwargs: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", boom)
        save_settings(MirrorToolSettings(), tmp_path / "x.json")  # 不应抛出


class TestNormalizeTimeout:
    """超时校验。"""

    @pytest.mark.parametrize(("raw", "expected"), [(3, 3.0), (0.5, 0.5), ("4.5", 4.5), (MAX_TIMEOUT, MAX_TIMEOUT)])
    def test_valid(self, raw: object, expected: float) -> None:
        assert normalize_timeout(raw) == expected

    @pytest.mark.parametrize("raw", [True, None, "", "abc", MIN_TIMEOUT - 0.1, MAX_TIMEOUT + 0.1, [3]])
    def test_invalid_raises(self, raw: object) -> None:
        with pytest.raises(ValueError, match="超时"):
            normalize_timeout(raw)


class TestValidGeometry:
    """窗口 geometry 校验。"""

    @pytest.mark.parametrize("text", ["900x620", "900x620+300+200", "900x620-10-20"])
    def test_valid(self, text: str) -> None:
        assert valid_geometry(text) == text

    @pytest.mark.parametrize("text", [None, "", "900", "900x620abc", "900 x 620"])
    def test_invalid(self, text: str | None) -> None:
        assert valid_geometry(text) is None


class TestResolveGeometry:
    """启动时 geometry 恢复。"""

    def test_keeps_visible_position(self) -> None:
        assert mirrortoolw.resolve_geometry("1000x700+100+80", 1920, 1080) == "1000x700+100+80"

    def test_pulls_back_offscreen(self) -> None:
        assert mirrortoolw.resolve_geometry("1040x720+5000+5000", 1920, 1080) == "1040x720+880+360"

    def test_enforces_minimum_size(self) -> None:
        assert mirrortoolw.resolve_geometry("200x100+0+0", 1920, 1080) == "900x620+0+0"

    def test_falls_back_to_default_geometry(self) -> None:
        assert mirrortoolw.resolve_geometry(None, 1920, 1080) == DEFAULT_GEOMETRY
        assert mirrortoolw.resolve_geometry("bad", 1920, 1080) == DEFAULT_GEOMETRY


# ============================================================================ #
# 候选表 / 探测
# ============================================================================ #
class TestServiceCandidates:
    """服务候选表复用 CLI 数据。"""

    def test_returns_copy_of_cli_table(self) -> None:
        candidates = service_candidates("pip")
        assert candidates == mirrors_mod._MIRROR_SERVICE_CANDIDATES["pip"]
        candidates.clear()
        assert mirrors_mod._MIRROR_SERVICE_CANDIDATES["pip"]

    def test_unknown_service_raises(self) -> None:
        with pytest.raises(ValueError, match="未知服务"):
            service_candidates("uv")


class TestProbeService:
    """服务镜像测速。"""

    def test_maps_probes_and_uses_service_user_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        candidates = service_candidates("pip")
        captured: dict[str, Any] = {}

        def fake_probe(urls: list[str], **kwargs: Any) -> list[UrlProbe]:
            captured["urls"] = urls
            captured.update(kwargs)
            return sorted(
                [
                    UrlProbe(url, url == candidates["nju"], 12.0 if url == candidates["nju"] else 50.0, 2000.0, 4096)
                    for url in urls
                ],
                key=lambda probe: not probe.ok,
            )

        monkeypatch.setattr("fcmd.infras.net.probe_mirrors", fake_probe)
        rows = probe_service("pip", timeout=2.5)

        assert captured["timeout"] == 2.5
        assert captured["allow_html"] is True
        assert captured["user_agent"] == "pip/24.0"
        assert rows[0].name == "nju"
        assert rows[0].ok is True
        assert rows[0].throughput_text == "2000 KB/s"
        assert recommend_mirror(rows) == "nju"

    def test_unknown_service_raises_before_probe(self) -> None:
        with pytest.raises(ValueError, match="未知服务"):
            probe_service("uv")


class TestProbeSites:
    """教育网镜像站点探测。"""

    def test_builtin_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        urls = list(mirrors_mod._CERNET_MIRROR_SITES.values())
        monkeypatch.setattr(
            "fcmd.infras.net.check_urls",
            lambda targets, timeout=5.0, workers=8: [(url, True, 10.0) for url in targets],
        )
        rows, origin = probe_sites(timeout=2.0, source="builtin")

        assert origin == "help.mirrors.cernet.edu.cn 内置列表"
        assert len(rows) == len(urls)
        assert all(row.ok for row in rows)

    def test_dynamic_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mirrors_mod, "fetch_mirrorz_sites", lambda timeout=5.0: {"tuna": "https://tuna.test"})
        monkeypatch.setattr(
            "fcmd.infras.net.check_urls",
            lambda targets, timeout=5.0, workers=8: [("https://tuna.test", True, 8.0)],
        )
        rows, origin = probe_sites(source="auto")

        assert origin == "mirrors.cernet.edu.cn 动态列表"
        assert [(row.name, row.url) for row in rows] == [("tuna", "https://tuna.test")]

    def test_dynamic_failure_falls_back_to_builtin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mirrors_mod, "fetch_mirrorz_sites", lambda timeout=5.0: None)
        monkeypatch.setattr(
            "fcmd.infras.net.check_urls",
            lambda targets, timeout=5.0, workers=8: [(url, False, float("inf")) for url in targets],
        )
        rows, origin = probe_sites(source="auto")

        assert "回退内置列表" in origin
        assert len(rows) == len(mirrors_mod._CERNET_MIRROR_SITES)
        assert recommend_mirror(rows) is None


class TestResultModel:
    """结果模型的可读字段。"""

    def test_reachable(self) -> None:
        row = MirrorResult("nju", "https://nju.test", True, 12.0, 2000.0)
        assert row.status_text == "可访问"
        assert row.latency_text == "12 ms"
        assert row.throughput_text == "2000 KB/s"

    def test_unreachable(self) -> None:
        row = MirrorResult("nju", "https://nju.test", False, float("inf"), 0.0, "超时/网络错误")
        assert row.status_text == "不可访问（超时/网络错误）"
        assert row.latency_text == "—"
        assert row.throughput_text == "—"

    def test_site_without_throughput(self) -> None:
        row = MirrorResult("tuna", "https://tuna.test", True, 10.0)
        assert row.throughput_text == "—"


class TestFilterAndRecommend:
    """结果过滤与推荐。"""

    @pytest.fixture
    def rows(self) -> list[MirrorResult]:
        return [
            MirrorResult("tsinghua", "https://mirrors.tuna.tsinghua.edu.cn", True, 10.0, 100.0),
            MirrorResult("aliyun", "https://mirrors.aliyun.com", False, float("inf"), 0.0, "HTTP 403"),
        ]

    def test_filter_matches_name_or_url(self, rows: list[MirrorResult]) -> None:
        assert [row.name for row in filter_results(rows, "TUNA")] == ["tsinghua"]
        assert [row.name for row in filter_results(rows, "aliyun")] == ["aliyun"]

    def test_filter_empty_returns_copy(self, rows: list[MirrorResult]) -> None:
        filtered = filter_results(rows, "   ")
        assert filtered == rows
        filtered.clear()
        assert rows

    def test_filter_no_match(self, rows: list[MirrorResult]) -> None:
        assert filter_results(rows, "huaweicloud") == []

    def test_recommend_first_reachable(self, rows: list[MirrorResult]) -> None:
        assert recommend_mirror(rows) == "tsinghua"

    def test_recommend_none_when_all_unreachable(self) -> None:
        assert recommend_mirror([MirrorResult("a", "https://a.test", False, float("inf"))]) is None


# ============================================================================ #
# 应用配置与位置提示
# ============================================================================ #
class TestApplyServiceMirror:
    """应用镜像复用 CLI 写入函数。"""

    def test_delegates_and_captures_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        applied: list[str] = []

        def fake_setup(mirror: str) -> None:
            applied.append(mirror)
            print(f"配置完成: {mirror}")

        monkeypatch.setitem(mirrors_mod._MIRROR_SERVICE_APPLY, "pip", fake_setup)
        assert apply_service_mirror("pip", "nju") == "配置完成: nju"
        assert applied == ["nju"]

    def test_unknown_service_raises(self) -> None:
        with pytest.raises(ValueError, match="未知服务"):
            apply_service_mirror("uv", "nju")

    def test_unknown_mirror_raises(self) -> None:
        with pytest.raises(ValueError, match="未知 pip 镜像"):
            apply_service_mirror("pip", "unknown")


class TestConfigLocation:
    """配置位置提示。"""

    def test_pip_config_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert Path(config_location("pip")).name in {"pip.ini", "pip.conf"}

    def test_conda_config_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert config_location("conda") == str(tmp_path / ".condarc")

    def test_cargo_config_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert config_location("cargo") == str(tmp_path / ".cargo" / "config.toml")

    def test_maven_config_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert config_location("maven") == str(tmp_path / ".m2" / "settings.xml")

    @pytest.mark.parametrize("service", ["rustup", "go", "npm"])
    def test_environment_only_services(self, service: str) -> None:
        assert config_location(service) == "环境变量（重开终端后生效）"

    def test_unknown_service_raises(self) -> None:
        with pytest.raises(ValueError, match="未知服务"):
            config_location("uv")


# ============================================================================ #
# GUI 入口
# ============================================================================ #
class TestGuiEntry:
    """GUI 启动入口（不真正创建窗口）。"""

    def test_run_gui_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """tkinter 不可用时打印错误并退出。"""

        def fake_window(_path: Path | None) -> None:
            raise ImportError("no tkinter")

        monkeypatch.setattr(mirrortoolw, "MirrorToolWindow", fake_window)
        mirrortoolw.run_gui()  # 不应抛出

    def test_run_gui_starts_mainloop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        class _Root:
            def mainloop(self) -> None:
                calls.append("mainloop")

        class _Window:
            root = _Root()

        monkeypatch.setattr(mirrortoolw, "MirrorToolWindow", lambda _path: _Window())
        mirrortoolw.run_gui()
        assert calls == ["mainloop"]

    def test_launch_cmd_calls_run_gui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []
        monkeypatch.setattr(mirrortoolw, "run_gui", lambda: calls.append(True))
        mirrortoolw.launch_cmd()
        assert calls == [True]


def test_defaults_are_valid() -> None:
    """默认超时与 geometry 自身可用。"""
    assert normalize_timeout(DEFAULT_TIMEOUT) == DEFAULT_TIMEOUT
    assert normalize_timeout(MIN_TIMEOUT) == MIN_TIMEOUT
    assert valid_geometry(DEFAULT_GEOMETRY) == DEFAULT_GEOMETRY
    assert MODE_SERVICE != MODE_SITES
