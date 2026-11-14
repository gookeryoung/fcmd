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
