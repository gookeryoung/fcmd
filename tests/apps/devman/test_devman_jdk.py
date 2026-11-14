"""JDK（Adoptium）：feature/lts 解析、API 校验值与通用 zip 安装。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apps.devman import _install, _jdk
from fcmd.apps.devman._errors import DevmanError
from fcmd.apps.devman._paths import Layout
from fcmd.apps.devman._toolchain import parse_spec
from tests.apps.devman import _fakes

SEMVER = "21.0.5+11"
CORE = "21.0.5"
FILENAME = "OpenJDK21U-jdk_x64_windows_hotspot_21.0.5_11.zip"
LINK = "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.5%2B11/" + FILENAME


def _layout(tmp_path: Path) -> Layout:
    return Layout(tmp_path / "root")


def _assets_url(feature: str) -> str:
    return f"{_jdk.API}/assets/latest/{feature}/hotspot?architecture=x64&image_type=jdk&os=windows&vendor=eclipse"


def _available(*, lts: int = 21, latest: int = 23) -> str:
    return json.dumps({"most_recent_lts": lts, "most_recent_feature_release": latest, "available_releases": [21, 23]})


def test_lts_resolves_via_available_releases(tmp_path: Path) -> None:
    data, digest = _fakes.make_jdk_zip(SEMVER)
    fetcher = _fakes.FakeFetcher(
        texts={
            _jdk.AVAILABLE: _available(),
            _assets_url("21"): _fakes.jdk_assets_json(SEMVER, FILENAME, LINK, digest),
        },
        payloads={LINK: data},
    )
    plan = _jdk.plan_install(fetcher, _layout(tmp_path), "lts")
    assert plan.version == SEMVER
    assert plan.urls == (LINK,)
    assert plan.executable == "bin/java.exe"
    assert plan.check_contains == CORE
    assert plan.expected_verify == "official-api"


def test_exact_version_uses_version_endpoint(tmp_path: Path) -> None:
    _data, digest = _fakes.make_jdk_zip(SEMVER)
    url = f"{_jdk.API}/assets/version/21.0.5%2B11?architecture=x64&image_type=jdk&os=windows&vendor=eclipse"
    fetcher = _fakes.FakeFetcher(texts={url: _fakes.jdk_assets_json(SEMVER, FILENAME, LINK, digest)})
    plan = _jdk.plan_install(fetcher, _layout(tmp_path), SEMVER)
    assert plan.version == SEMVER


def test_unknown_spec_rejected(tmp_path: Path) -> None:
    with pytest.raises(DevmanError) as info:
        _jdk.plan_install(_fakes.FakeFetcher(), _layout(tmp_path), "jdk21")
    assert info.value.code == "VERSION_NOT_FOUND"


def test_install_end_to_end(tmp_path: Path) -> None:
    data, digest = _fakes.make_jdk_zip(SEMVER)
    fetcher = _fakes.FakeFetcher(
        texts={
            _jdk.AVAILABLE: _available(),
            _assets_url("21"): _fakes.jdk_assets_json(SEMVER, FILENAME, LINK, digest),
        },
        payloads={LINK: data},
    )
    plan = _jdk.plan_install(fetcher, _layout(tmp_path), "lts")
    runner = _fakes.FakeRunner(lambda argv, env: _fakes.result(stderr=f'openjdk version "{CORE}" 2024-10-15 LTS\n'))
    result = _install.install_zip(plan, _layout(tmp_path), fetcher=fetcher, runner=runner)
    assert result["changed"] is True
    assert result["actions"][0]["verify"] == "official-api"
    assert (_layout(tmp_path).version_dir("jdk", SEMVER) / "bin" / "java.exe").is_file()


def test_release_names_are_listed(tmp_path: Path) -> None:
    fetcher = _fakes.FakeFetcher(
        texts={_jdk.RELEASES.format(arch="x64"): json.dumps(["jdk-21.0.5+11", "jdk-21.0.4+7", "jdk-17.0.13+11"])}
    )
    assert _jdk.release_names(fetcher)[:2] == ["21.0.5+11", "21.0.4+7"]


def test_lts_spec_allowed_for_jdk_only() -> None:
    assert parse_spec("jdk@lts").tool == "jdk"
    with pytest.raises(DevmanError):
        parse_spec("go@lts")


def _package(
    *,
    semver: object = SEMVER,
    name: object = FILENAME,
    link: object = LINK,
    checksum: object = "a" * 64,
) -> dict[str, object]:
    return {"version": {"semver": semver}, "binary": {"package": {"name": name, "link": link, "checksum": checksum}}}


def test_asset_index_parse_errors(tmp_path: Path) -> None:
    broken = _fakes.FakeFetcher(texts={_assets_url("21"): "{not json"})
    with pytest.raises(DevmanError) as info:
        _jdk.plan_install(broken, _layout(tmp_path), "21")
    assert info.value.code == "RUNTIME_ERROR"
    assert "无法解析" in info.value.message

    shape = _fakes.FakeFetcher(texts={_assets_url("21"): json.dumps({"assets": []})})
    with pytest.raises(DevmanError) as info:
        _jdk.plan_install(shape, _layout(tmp_path), "21")
    assert info.value.code == "RUNTIME_ERROR"
    assert "不是数组" in info.value.message


def test_asset_rows_are_filtered_until_usable(tmp_path: Path) -> None:
    digest = "b" * 64
    rows = [
        "not-a-dict",
        {"version": {"semver": SEMVER}},
        _package(semver=7),
        _package(semver=""),
        _package(link=None),
        _package(checksum=None),
        _package(name=None),
        _package(checksum="zz"),
        _package(name="OpenJDK21U.tar.gz"),
        _package(checksum=digest),
    ]
    fetcher = _fakes.FakeFetcher(texts={_assets_url("21"): json.dumps(rows)})
    plan = _jdk.plan_install(fetcher, _layout(tmp_path), "21")
    assert plan.version == SEMVER
    assert plan.expected_sha256 == digest
    assert plan.expected_verify == "official-api"
    assert plan.filename == FILENAME


def test_asset_index_without_usable_zip(tmp_path: Path) -> None:
    rows = [_package(name="OpenJDK21U.tar.gz")]
    fetcher = _fakes.FakeFetcher(texts={_assets_url("21"): json.dumps(rows)})
    with pytest.raises(DevmanError) as info:
        _jdk.plan_install(fetcher, _layout(tmp_path), "21")
    assert info.value.code == "VERSION_NOT_FOUND"
    assert "没有可用的 Windows JDK zip" in info.value.message


def test_latest_feature_errors(tmp_path: Path) -> None:
    broken = _fakes.FakeFetcher(texts={_jdk.AVAILABLE: "{not json"})
    with pytest.raises(DevmanError) as info:
        _jdk.plan_install(broken, _layout(tmp_path), "lts")
    assert info.value.code == "RUNTIME_ERROR"
    assert "Adoptium 版本信息无法解析" in info.value.message

    missing = _fakes.FakeFetcher(texts={_jdk.AVAILABLE: json.dumps({"available_releases": [21]})})
    with pytest.raises(DevmanError) as info:
        _jdk.plan_install(missing, _layout(tmp_path), "lts")
    assert info.value.code == "VERSION_NOT_FOUND"
    assert "feature 版本" in info.value.message


def test_numeric_feature_accepts_explicit_sha(tmp_path: Path) -> None:
    _data, digest = _fakes.make_jdk_zip(SEMVER)
    fetcher = _fakes.FakeFetcher(texts={_assets_url("21"): _fakes.jdk_assets_json(SEMVER, FILENAME, LINK, digest)})
    plan = _jdk.plan_install(fetcher, _layout(tmp_path), "21", sha256=digest.upper())
    assert plan.version == SEMVER
    assert plan.expected_sha256 == digest
    assert plan.expected_verify == "explicit-sha256"
    assert plan.urls == (LINK,)


def test_plan_install_rejects_bad_sha(tmp_path: Path) -> None:
    _data, digest = _fakes.make_jdk_zip(SEMVER)
    fetcher = _fakes.FakeFetcher(texts={_assets_url("21"): _fakes.jdk_assets_json(SEMVER, FILENAME, LINK, digest)})
    with pytest.raises(DevmanError) as info:
        _jdk.plan_install(fetcher, _layout(tmp_path), "21", sha256="not-a-sha")
    assert info.value.code == "INVALID_ARGUMENT"
    assert info.value.field == "sha256"


def test_resolve_version_uses_version_endpoint(tmp_path: Path) -> None:
    _data, digest = _fakes.make_jdk_zip(SEMVER)
    url = f"{_jdk.API}/assets/version/21.0.5%2B11?architecture=x64&image_type=jdk&os=windows&vendor=eclipse"
    fetcher = _fakes.FakeFetcher(texts={url: _fakes.jdk_assets_json(SEMVER, FILENAME, LINK, digest)})
    assert _jdk.resolve_version(fetcher, SEMVER) == SEMVER


def test_release_names_skips_non_strings() -> None:
    body = json.dumps(["jdk-21.0.5+11", 42, {"version": "jdk-20"}, "temurin-17.0.13+11"])
    fetcher = _fakes.FakeFetcher(texts={_jdk.RELEASES.format(arch="x64"): body})
    assert _jdk.release_names(fetcher, arch="x64") == ["21.0.5+11"]
