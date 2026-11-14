"""Deno / Bun：latest 解析、GitHub 资产计划与通用 zip 安装。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apps.devman import _bun, _deno, _install
from fcmd.apps.devman._errors import DevmanError
from fcmd.apps.devman._paths import Layout
from tests.apps.devman import _fakes

DENO = "2.1.4"
BUN = "1.1.38"


def _layout(tmp_path: Path) -> Layout:
    return Layout(tmp_path / "root")


# ---------------------------------------------------------------------- #
# Deno
# ---------------------------------------------------------------------- #


def test_deno_latest_and_exact(tmp_path: Path) -> None:
    fetcher = _fakes.FakeFetcher(texts={_deno.LATEST_URL: f"v{DENO}\n"})
    plan = _deno.plan_install(fetcher, _layout(tmp_path), "latest")
    assert plan.version == DENO
    assert plan.urls == (
        f"https://dl.deno.land/release/v{DENO}/deno-x86_64-pc-windows-msvc.zip",
        f"https://github.com/denoland/deno/releases/download/v{DENO}/deno-x86_64-pc-windows-msvc.zip",
    )
    assert plan.sha_url.endswith(".sha256sum")
    assert plan.executable == "deno.exe"

    exact = _deno.plan_install(_fakes.FakeFetcher(), _layout(tmp_path), DENO)
    assert exact.version == DENO


def test_deno_rejects_prefix(tmp_path: Path) -> None:
    with pytest.raises(DevmanError) as info:
        _deno.plan_install(_fakes.FakeFetcher(), _layout(tmp_path), "2.1")
    assert info.value.code == "INVALID_ARGUMENT"


def test_deno_install_from_rootless_zip(tmp_path: Path) -> None:
    data, digest = _fakes.make_deno_zip(DENO)
    fetcher = _fakes.FakeFetcher(texts={_deno.LATEST_URL: DENO})
    plan = _deno.plan_install(fetcher, _layout(tmp_path), "latest")
    # Deno 的 .sha256sum 是「Algorithm : SHA256 / Hash : <hex>」格式
    fetcher.texts[plan.sha_url] = f"\r\nAlgorithm : SHA256\r\nHash      : {digest.upper()}\r\n"
    fetcher.payloads[plan.urls[0]] = data
    runner = _fakes.FakeRunner(lambda argv, env: _fakes.result(stdout=f"deno {DENO} (stable)\n"))
    result = _install.install_zip(plan, _layout(tmp_path), fetcher=fetcher, runner=runner)
    assert result["changed"] is True
    assert (_layout(tmp_path).version_dir("deno", DENO) / "deno.exe").is_file()


# ---------------------------------------------------------------------- #
# Bun
# ---------------------------------------------------------------------- #


def test_bun_latest_from_github_api(tmp_path: Path) -> None:
    fetcher = _fakes.FakeFetcher(texts={_bun.LATEST_API: json.dumps({"tag_name": f"bun-v{BUN}"})})
    plan = _bun.plan_install(fetcher, _layout(tmp_path), "latest")
    assert plan.version == BUN
    assert plan.urls == (f"https://github.com/oven-sh/bun/releases/download/bun-v{BUN}/bun-windows-x64.zip",)
    assert plan.sha_url.endswith("SHASUMS256.txt")


def test_bun_latest_rejects_bad_tag(tmp_path: Path) -> None:
    fetcher = _fakes.FakeFetcher(texts={_bun.LATEST_API: json.dumps({"tag_name": "weird"})})
    with pytest.raises(DevmanError) as info:
        _bun.plan_install(fetcher, _layout(tmp_path), "latest")
    assert info.value.code == "VERSION_NOT_FOUND"


def test_bun_install_from_top_level_zip(tmp_path: Path) -> None:
    data, digest = _fakes.make_bun_zip(BUN)
    fetcher = _fakes.FakeFetcher(texts={_bun.LATEST_API: json.dumps({"tag_name": f"bun-v{BUN}"})})
    plan = _bun.plan_install(fetcher, _layout(tmp_path), "latest")
    fetcher.texts[plan.sha_url] = _fakes.shasums_text((plan.filename, digest))
    fetcher.payloads[plan.urls[0]] = data
    runner = _fakes.FakeRunner(lambda argv, env: _fakes.result(stdout=f"{BUN}\n"))
    result = _install.install_zip(plan, _layout(tmp_path), fetcher=fetcher, runner=runner)
    assert result["changed"] is True
    assert (_layout(tmp_path).version_dir("bun", BUN) / "bun.exe").is_file()


def test_bun_exact_version_and_sha_validation(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    plan = _bun.plan_install(_fakes.FakeFetcher(), layout, BUN)
    assert plan.version == BUN
    assert plan.expected_sha256 is None
    assert plan.expected_verify == "official-shasums"

    sha = "c" * 64
    signed = _bun.plan_install(_fakes.FakeFetcher(), layout, BUN, sha256=sha.upper())
    assert signed.expected_sha256 == sha
    assert signed.expected_verify == "explicit-sha256"

    with pytest.raises(DevmanError) as info:
        _bun.plan_install(_fakes.FakeFetcher(), layout, BUN, sha256="bad")
    assert info.value.code == "INVALID_ARGUMENT"
    assert info.value.field == "sha256"


def test_bun_latest_rejects_broken_body(tmp_path: Path) -> None:
    broken = _fakes.FakeFetcher(texts={_bun.LATEST_API: "{not json"})
    with pytest.raises(DevmanError) as info:
        _bun.plan_install(broken, _layout(tmp_path), "latest")
    assert info.value.code == "RUNTIME_ERROR"
    assert "无法解析" in info.value.message

    missing = _fakes.FakeFetcher(texts={_bun.LATEST_API: json.dumps({"name": "bun"})})
    with pytest.raises(DevmanError) as info:
        _bun.plan_install(missing, _layout(tmp_path), "latest")
    assert info.value.code == "VERSION_NOT_FOUND"
    assert "缺少 tag_name" in info.value.message


def test_bun_rejects_prefix_and_garbage(tmp_path: Path) -> None:
    with pytest.raises(DevmanError) as info:
        _bun.plan_install(_fakes.FakeFetcher(), _layout(tmp_path), "1.1")
    assert info.value.code == "INVALID_ARGUMENT"
    assert "不支持版本前缀" in info.value.message

    with pytest.raises(DevmanError) as info:
        _bun.plan_install(_fakes.FakeFetcher(), _layout(tmp_path), "whatever")
    assert info.value.code == "VERSION_NOT_FOUND"


def test_deno_rejects_broken_latest_and_sha(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    broken = _fakes.FakeFetcher(texts={_deno.LATEST_URL: "nope\n"})
    with pytest.raises(DevmanError) as info:
        _deno.plan_install(broken, layout, "latest")
    assert info.value.code == "VERSION_NOT_FOUND"
    assert "无法解析 Deno 最新版本" in info.value.message

    with pytest.raises(DevmanError) as info:
        _deno.plan_install(_fakes.FakeFetcher(), layout, "whatever")
    assert info.value.code == "VERSION_NOT_FOUND"
    assert "无法解析 Deno 版本" in info.value.message

    sha = "d" * 64
    signed = _deno.plan_install(_fakes.FakeFetcher(), layout, DENO, sha256=sha.upper())
    assert signed.expected_sha256 == sha
    assert signed.expected_verify == "explicit-sha256"

    with pytest.raises(DevmanError) as info:
        _deno.plan_install(_fakes.FakeFetcher(), layout, DENO, sha256="bad")
    assert info.value.code == "INVALID_ARGUMENT"
    assert info.value.field == "sha256"


# ---------------------------------------------------------------------- #
# 通用 zip 安装流水线的失败分支
# ---------------------------------------------------------------------- #


def _pipeline_plan(tmp_path: Path, **overrides: object) -> _install.ArtifactPlan:
    layout = _layout(tmp_path)
    base: dict[str, object] = {
        "tool": "deno",
        "version": DENO,
        "filename": "deno-x86_64-pc-windows-msvc.zip",
        "urls": ("https://example.invalid/deno.zip",),
        "sha_url": "",
        "dest": layout.version_dir("deno", DENO),
        "cache_path": layout.cache_dir / "deno-x86_64-pc-windows-msvc.zip",
        "executable": "deno.exe",
        "check_args": ("--version",),
        "check_contains": f"deno {DENO}",
    }
    base.update(overrides)
    return _install.ArtifactPlan(**base)  # type: ignore[arg-type]


def test_install_zip_requires_checksum_source(tmp_path: Path) -> None:
    plan = _pipeline_plan(tmp_path)
    with pytest.raises(DevmanError) as info:
        _install.install_zip(plan, _layout(tmp_path), fetcher=_fakes.FakeFetcher(), runner=_fakes.FakeRunner())
    assert info.value.code == "CHECKSUM_UNAVAILABLE"
    assert "没有可用的 deno 校验清单" in info.value.message


def test_install_zip_checksum_list_fetch_error(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    plan = _deno.plan_install(_fakes.FakeFetcher(), layout, DENO)
    broken = _fakes.FakeFetcher(errors={plan.sha_url: _fakes.fetch_error("offline")})
    with pytest.raises(DevmanError) as info:
        _install.install_zip(plan, layout, fetcher=broken, runner=_fakes.FakeRunner())
    assert info.value.code == "CHECKSUM_UNAVAILABLE"
    assert "无法获取 deno 校验清单" in info.value.message


def test_install_zip_checksum_list_missing_artifact(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    plan = _deno.plan_install(_fakes.FakeFetcher(), layout, DENO)
    fetcher = _fakes.FakeFetcher(texts={plan.sha_url: _fakes.shasums_text(("other.zip", "0" * 64))})
    with pytest.raises(DevmanError) as info:
        _install.install_zip(plan, layout, fetcher=fetcher, runner=_fakes.FakeRunner())
    assert info.value.code == "CHECKSUM_UNAVAILABLE"
    assert plan.filename in info.value.message
    assert plan.sha_url in (info.value.hint or "")


def test_install_zip_reports_all_download_failures(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _data, digest = _fakes.make_deno_zip(DENO)
    plan = _deno.plan_install(_fakes.FakeFetcher(), layout, DENO, sha256=digest)
    fetcher = _fakes.FakeFetcher(
        errors={plan.urls[0]: _fakes.fetch_error("cdn down"), plan.urls[1]: _fakes.fetch_error("github down")}
    )
    with pytest.raises(DevmanError) as info:
        _install.install_zip(plan, layout, fetcher=fetcher, runner=_fakes.FakeRunner())
    assert info.value.code == "DOWNLOAD_FAILED"
    assert "cdn down" in info.value.message and "github down" in info.value.message


def test_install_zip_uses_cached_artifact_without_download(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    data, digest = _fakes.make_deno_zip(DENO)
    plan = _deno.plan_install(_fakes.FakeFetcher(), layout, DENO, sha256=digest)
    plan.cache_path.parent.mkdir(parents=True, exist_ok=True)
    plan.cache_path.write_bytes(data)
    fetcher = _fakes.FakeFetcher()
    runner = _fakes.FakeRunner(lambda argv, env: _fakes.result(stdout=f"deno {DENO} (stable)\n"))
    result = _install.install_zip(plan, layout, fetcher=fetcher, runner=runner)
    assert result["changed"] is True
    assert fetcher.download_calls == []
    assert (plan.dest / "deno.exe").is_file()


def test_install_zip_rejects_archive_without_executable(tmp_path: Path) -> None:
    data, digest = _fakes.make_deno_zip(DENO)
    plan = _pipeline_plan(tmp_path, executable="nope.exe", expected_sha256=digest)
    fetcher = _fakes.FakeFetcher(payloads={plan.urls[0]: data})
    with pytest.raises(DevmanError) as info:
        _install.install_zip(plan, _layout(tmp_path), fetcher=fetcher, runner=_fakes.FakeRunner())
    assert info.value.code == "DOWNLOAD_FAILED"
    assert "压缩包里没有 nope.exe" in info.value.message
    assert not plan.dest.exists()


def test_install_zip_replaces_existing_directory(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    data, digest = _fakes.make_deno_zip(DENO)
    plan = _deno.plan_install(_fakes.FakeFetcher(), layout, DENO, sha256=digest)
    fetcher = _fakes.FakeFetcher(payloads={plan.urls[0]: data})
    plan.dest.mkdir(parents=True)
    (plan.dest / "stale.txt").write_text("old", encoding="utf-8")
    runner = _fakes.FakeRunner(lambda argv, env: _fakes.result(stdout=f"deno {DENO} (stable)\n"))
    result = _install.install_zip(plan, layout, fetcher=fetcher, runner=runner)
    assert result["actions"][-1] == {"action": "install", "path": str(plan.dest)}
    assert not (plan.dest / "stale.txt").exists()
    assert (plan.dest / "deno.exe").is_file()
