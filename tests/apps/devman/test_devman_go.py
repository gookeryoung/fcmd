"""Go 扩展：索引解析、计划与通用 zip 安装流水线。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apps.devman import _go, _install
from fcmd.apps.devman._errors import DevmanError
from fcmd.apps.devman._paths import Layout
from tests.apps.devman import _fakes

VERSION = "1.23.4"


def _layout(tmp_path: Path) -> Layout:
    return Layout(tmp_path / "root")


def _index(
    version: str = VERSION, *, stable: bool = True, sha: str | None = None
) -> tuple[bytes, str, str]:
    data, digest = _fakes.make_go_zip(version)
    return data, digest, _fakes.go_index_json(version, sha or digest, stable=stable)


def _plan(tmp_path: Path) -> tuple[_install.ArtifactPlan, bytes, _fakes.FakeFetcher]:
    layout = _layout(tmp_path)
    data, digest, text = _index()
    fetcher = _fakes.FakeFetcher(texts={_go.INDEX: text}, payloads={f"{_go.DIST}/go{VERSION}.windows-amd64.zip": data})
    plan = _go.plan_install(fetcher, layout, "1.23")
    assert plan.expected_sha256 == digest
    return plan, data, fetcher


def _runner(version: str = VERSION) -> _fakes.FakeRunner:
    return _fakes.FakeRunner(lambda argv, env: _fakes.result(stdout=f"go version go{version} windows/amd64\n"))


def test_parse_index_and_resolve() -> None:
    _data, digest, text = _index()
    releases = _go.parse_index(text)
    assert [release.version for release in releases] == [VERSION]
    assert releases[0].files[0].sha256 == digest
    assert _go.latest_matching(releases, "latest") == VERSION
    assert _go.latest_matching(releases, "1.23") == VERSION
    assert _go.latest_matching(releases, VERSION) == VERSION
    with pytest.raises(DevmanError) as info:
        _go.latest_matching(releases, "1.22")
    assert info.value.code == "VERSION_NOT_FOUND"


def test_parse_index_skips_bad_entries() -> None:
    text = json.dumps(
        [
            {"version": "not-a-go-version"},
            {
                "version": "go1.23.4",
                "stable": True,
                "files": [{"filename": "x.zip", "os": "windows", "arch": "amd64", "sha256": "zzz"}],
            },
        ]
    )
    releases = _go.parse_index(text)
    assert [release.version for release in releases] == [VERSION]
    assert releases[0].files == ()


def test_parse_index_rejects_non_list() -> None:
    with pytest.raises(DevmanError):
        _go.parse_index("{}")


def test_plan_install_uses_official_sha(tmp_path: Path) -> None:
    plan, _data, _fetcher = _plan(tmp_path)
    assert plan.version == VERSION
    assert plan.urls == (f"{_go.DIST}/go{VERSION}.windows-amd64.zip",)
    assert plan.sha_url == ""
    assert plan.executable == "bin/go.exe"
    assert plan.check_contains == f"go{VERSION}"
    assert plan.to_data()["artifact"] == f"go{VERSION}.windows-amd64.zip"

    with pytest.raises(DevmanError) as info:
        _go.plan_install(_fakes.FakeFetcher(texts={_go.INDEX: _index()[2]}), _layout(tmp_path), VERSION, sha256="bad")
    assert info.value.code == "INVALID_ARGUMENT"


def test_plan_install_without_windows_artifact(tmp_path: Path) -> None:
    _data, digest, _text = _index()
    text = _fakes.go_index_json(VERSION, digest, arch="arm64")
    fetcher = _fakes.FakeFetcher(texts={_go.INDEX: text})
    with pytest.raises(DevmanError) as info:
        _go.plan_install(fetcher, _layout(tmp_path), VERSION, arch="amd64")
    assert info.value.code == "VERSION_NOT_FOUND"


def test_install_zip_end_to_end(tmp_path: Path) -> None:
    plan, _data, fetcher = _plan(tmp_path)
    result = _install.install_zip(plan, _layout(tmp_path), fetcher=fetcher, runner=_runner())
    assert result["changed"] is True
    assert result["actions"][0]["action"] == "download"
    assert result["actions"][0]["verify"] == "official-index"
    final = _layout(tmp_path).version_dir("go", VERSION)
    assert (final / "bin" / "go.exe").is_file()
    marker = json.loads((final / ".devman.json").read_text(encoding="utf-8"))
    assert marker["tool"] == "go"
    assert marker["verify"] == "official-index"


def test_install_zip_self_check_mismatch_cleans_staging(tmp_path: Path) -> None:
    plan, _data, fetcher = _plan(tmp_path)
    with pytest.raises(DevmanError):
        _install.install_zip(plan, _layout(tmp_path), fetcher=fetcher, runner=_runner("1.22.0"))
    assert not plan.dest.exists()
    assert not list(_layout(tmp_path).go_root.glob(".staging-*"))


def test_install_zip_checksum_mismatch_deletes_cache(tmp_path: Path) -> None:
    plan, _data, _fetcher = _plan(tmp_path)
    tampered = _fakes.FakeFetcher(payloads={plan.urls[0]: b"tampered"})
    with pytest.raises(DevmanError) as info:
        _install.install_zip(plan, _layout(tmp_path), fetcher=tampered, runner=_runner())
    assert info.value.code == "CHECKSUM_MISMATCH"
    assert not plan.cache_path.exists()


def test_install_zip_skips_complete_install(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    plan, _data, fetcher = _plan(tmp_path)
    _install.install_zip(plan, layout, fetcher=fetcher, runner=_runner())
    calls = len(fetcher.download_calls)
    again = _install.install_zip(plan, layout, fetcher=fetcher, runner=_runner())
    assert again["changed"] is False
    assert len(fetcher.download_calls) == calls


def test_installed_go_is_resolved_and_activated(tmp_path: Path) -> None:
    from fcmd.apps.devman._state import active_version, set_active
    from fcmd.apps.devman._toolchain import Spec, resolve_installed

    layout = _layout(tmp_path)
    _fakes.install_fake_go(layout, VERSION)
    row = resolve_installed(layout, Spec("go", "1.23"))
    assert row.name == VERSION and row.complete
    set_active(layout, "go", VERSION)
    assert active_version(layout, "go") == VERSION
