"""Node 来源：索引解析、版本解析、下载校验与安装流水线。"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.devman import _node
from fcmd.apps.devman._errors import DevmanError
from fcmd.apps.devman._paths import Layout
from tests.apps.devman import _fakes

VERSION = "20.11.1"


def _layout(tmp_path: Path) -> Layout:
    return Layout(tmp_path / "root")


def _good_runner(version: str = VERSION) -> _fakes.FakeRunner:
    return _fakes.FakeRunner(lambda argv, env: _fakes.result(stdout=f"v{version}\n"))


def _zip(*members: tuple[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, payload in members:
            # 固定时间戳与压缩方式，避免 zip 头写入当前时间导致参数化 id 每次收集都不同
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            bundle.writestr(info, payload)
    return buffer.getvalue()


# ---------------------------------------------------------------------- #
# 索引与计划
# ---------------------------------------------------------------------- #


def test_detect_arch_and_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_node.platform, "machine", lambda: "ARM64")
    assert _node.detect_arch() == "arm64"
    monkeypatch.setattr(_node.platform, "machine", lambda: "AMD64")
    assert _node.detect_arch() == "x64"
    assert _node.artifact_name(VERSION, "x64") == "node-v20.11.1-win-x64.zip"
    assert _node.sha_url(VERSION) == "https://nodejs.org/dist/v20.11.1/SHASUMS256.txt"


def test_candidate_bases() -> None:
    assert _node.candidate_bases("auto") == [
        "https://nodejs.org/dist",
        "https://npmmirror.com/mirrors/node",
    ]
    assert _node.candidate_bases("") == _node.candidate_bases("auto")
    assert _node.candidate_bases("official") == ["https://nodejs.org/dist"]
    assert _node.candidate_bases("https://mirror.invalid/node/") == [
        "https://mirror.invalid/node",
        "https://nodejs.org/dist",
    ]


def test_parse_release_index_skips_bad_entries() -> None:
    text = _fakes.node_index_json(("20.11.1", "Iron"), ("21.0.0", False), ("not-a-version", False))
    raw = text.replace('{"version": "vnot-a-version"', '{"version": "vnot-a-version"')
    releases = _node.parse_release_index(raw)
    assert [item.version for item in releases] == ["20.11.1", "21.0.0"]
    assert releases[0].lts == "Iron"
    assert releases[1].lts is False
    assert releases[0].to_data()["date"] == "2026-01-01"


def test_parse_release_index_rejects_non_list() -> None:
    with pytest.raises(DevmanError):
        _node.parse_release_index('{"version": "v20.0.0"}')
    with pytest.raises(DevmanError):
        _node.parse_release_index("not json")
    assert _node.parse_release_index("[1, null, {}]") == []


def test_latest_matching_channels() -> None:
    releases = _node.parse_release_index(
        _fakes.node_index_json(("20.11.1", "Iron"), ("20.12.0", "Iron"), ("21.5.0", False))
    )
    assert _node.latest_matching(releases, "latest") == "21.5.0"
    assert _node.latest_matching(releases, "lts") == "20.12.0"
    assert _node.latest_matching(releases, "20") == "20.12.0"
    assert _node.latest_matching(releases, "20.11") == "20.11.1"
    with pytest.raises(DevmanError) as info:
        _node.latest_matching(releases, "22")
    assert info.value.code == "VERSION_NOT_FOUND"
    with pytest.raises(DevmanError):
        _node.latest_matching(releases, "vNext")
    with pytest.raises(DevmanError):
        _node.latest_matching([], "latest")


def test_latest_matching_without_lts() -> None:
    releases = _node.parse_release_index(_fakes.node_index_json(("21.0.0", False)))
    with pytest.raises(DevmanError) as info:
        _node.latest_matching(releases, "lts")
    assert "LTS" in str(info.value)


def test_parse_shasums() -> None:
    digest = "a" * 64
    table = _node.parse_shasums(f"{digest}  node-v20.11.1-win-x64.zip\nbad-line\n{'b' * 64} *star.zip\n")
    assert table == {"node-v20.11.1-win-x64.zip": digest, "star.zip": "b" * 64}


def test_plan_install_variants(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    plan = _node.plan_install(layout, VERSION, arch="arm64")
    assert plan.filename == "node-v20.11.1-win-arm64.zip"
    assert plan.urls[0] == f"https://nodejs.org/dist/v{VERSION}/node-v20.11.1-win-arm64.zip"
    assert plan.dest == layout.version_dir("node", VERSION)
    assert plan.expected_sha256 is None
    assert "arm64" in plan.to_data()["artifact"]

    explicit = _node.plan_install(layout, VERSION, sha256="A" * 64)
    assert explicit.expected_sha256 == "a" * 64
    with pytest.raises(DevmanError) as info:
        _node.plan_install(layout, VERSION, sha256="nope")
    assert info.value.code == "INVALID_ARGUMENT"


# ---------------------------------------------------------------------- #
# 安装
# ---------------------------------------------------------------------- #


def test_install_success_writes_marker(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    plan = _node.plan_install(layout, VERSION)
    data, digest = _fakes.make_node_zip(VERSION)
    fetcher = _fakes.FakeFetcher(
        texts={plan.sha_url: _fakes.shasums_text((plan.filename, digest))},
        payloads={plan.urls[0]: data},
    )
    result = _node.install(plan, layout, fetcher=fetcher, runner=_good_runner())
    assert result["changed"] is True
    assert (plan.dest / "node.exe").is_file()
    marker = (plan.dest / ".devman.json").read_text(encoding="utf-8")
    assert '"verify": "official-shasums"' in marker
    assert result["actions"][0]["verify"] == "official-shasums"
    assert not list(layout.node_root.glob(".staging-*"))


def test_install_skips_complete_without_force(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _fakes.install_fake_node(layout, VERSION)
    plan = _node.plan_install(layout, VERSION)
    fetcher = _fakes.FakeFetcher()
    result = _node.install(plan, layout, fetcher=fetcher, runner=_good_runner())
    assert result["changed"] is False
    assert fetcher.text_calls == []
    assert fetcher.download_calls == []


def test_install_force_reinstalls(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _fakes.install_fake_node(layout, VERSION)
    plan = _node.plan_install(layout, VERSION)
    data, digest = _fakes.make_node_zip(VERSION)
    fetcher = _fakes.FakeFetcher(
        texts={plan.sha_url: _fakes.shasums_text((plan.filename, digest))},
        payloads={plan.urls[0]: data},
    )
    result = _node.install(plan, layout, fetcher=fetcher, runner=_good_runner(), force=True)
    assert result["changed"] is True


def test_install_checksum_mismatch_removes_cache(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    plan = _node.plan_install(layout, VERSION)
    fetcher = _fakes.FakeFetcher(
        texts={plan.sha_url: _fakes.shasums_text((plan.filename, "0" * 64))},
        payloads={plan.urls[0]: _fakes.make_node_zip(VERSION)[0]},
    )
    with pytest.raises(DevmanError) as info:
        _node.install(plan, layout, fetcher=fetcher, runner=_good_runner())
    assert info.value.code == "CHECKSUM_MISMATCH"
    assert not plan.cache_path.exists()
    assert not plan.dest.exists()


def test_install_checksum_unavailable(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    plan = _node.plan_install(layout, VERSION)
    fetcher = _fakes.FakeFetcher(errors={plan.sha_url: _fakes.fetch_error("offline")})
    with pytest.raises(DevmanError) as info:
        _node.install(plan, layout, fetcher=fetcher, runner=_good_runner())
    assert info.value.code == "CHECKSUM_UNAVAILABLE"
    assert "--sha256" in (info.value.hint or "")


def test_install_shasums_without_artifact(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    plan = _node.plan_install(layout, VERSION)
    fetcher = _fakes.FakeFetcher(texts={plan.sha_url: _fakes.shasums_text(("other.zip", "a" * 64))})
    with pytest.raises(DevmanError) as info:
        _node.install(plan, layout, fetcher=fetcher, runner=_good_runner())
    assert info.value.code == "CHECKSUM_UNAVAILABLE"


def test_install_download_failure_tries_all_urls(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    plan = _node.plan_install(layout, VERSION, sha256="a" * 64)
    fetcher = _fakes.FakeFetcher(errors={url: _fakes.fetch_error("blocked") for url in plan.urls})
    with pytest.raises(DevmanError) as info:
        _node.install(plan, layout, fetcher=fetcher, runner=_good_runner())
    assert info.value.code == "DOWNLOAD_FAILED"
    assert len(fetcher.download_calls) == len(plan.urls)
    assert not plan.cache_path.exists()


def test_install_reuses_cached_artifact(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    data, digest = _fakes.make_node_zip(VERSION)
    cache = layout.cache_dir / _node.artifact_name(VERSION, "x64")
    cache.parent.mkdir(parents=True)
    cache.write_bytes(data)
    plan = _node.plan_install(layout, VERSION, sha256=digest)
    fetcher = _fakes.FakeFetcher()
    result = _node.install(plan, layout, fetcher=fetcher, runner=_good_runner())
    assert result["changed"] is True
    assert fetcher.download_calls == []


def test_install_with_explicit_sha_skips_shasums(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    data, digest = _fakes.make_node_zip(VERSION)
    plan = _node.plan_install(layout, VERSION, sha256=digest)
    fetcher = _fakes.FakeFetcher(payloads={plan.urls[0]: data})
    result = _node.install(plan, layout, fetcher=fetcher, runner=_good_runner())
    assert result["changed"] is True
    assert fetcher.text_calls == []
    assert result["actions"][0]["verify"] == "explicit-sha256"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        pytest.param(_zip(("node.exe", b"MZ")), "缺少顶层目录", id="missing-top-dir"),
        pytest.param(_zip(("node-v20.11.1-win-x64/README.md", b"x")), "没有 node.exe", id="missing-node-exe"),
    ],
)
def test_install_rejects_unexpected_archive_layout(tmp_path: Path, payload: bytes, message: str) -> None:
    layout = _layout(tmp_path)
    plan = _node.plan_install(layout, VERSION)
    digest = hashlib.sha256(payload).hexdigest()
    fetcher = _fakes.FakeFetcher(
        texts={plan.sha_url: _fakes.shasums_text((plan.filename, digest))},
        payloads={plan.urls[0]: payload},
    )
    with pytest.raises(DevmanError) as info:
        _node.install(plan, layout, fetcher=fetcher, runner=_good_runner())
    assert message in str(info.value)
    assert not plan.dest.exists()


def test_install_self_test_mismatch_aborts(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    plan = _node.plan_install(layout, VERSION)
    data, digest = _fakes.make_node_zip(VERSION)
    fetcher = _fakes.FakeFetcher(
        texts={plan.sha_url: _fakes.shasums_text((plan.filename, digest))},
        payloads={plan.urls[0]: data},
    )
    with pytest.raises(DevmanError) as info:
        _node.install(plan, layout, fetcher=fetcher, runner=_good_runner("18.0.0"))
    assert "自检失败" in str(info.value)
    assert not plan.dest.exists()


def test_install_self_test_nonzero_exit_aborts(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    plan = _node.plan_install(layout, VERSION)
    data, digest = _fakes.make_node_zip(VERSION)
    fetcher = _fakes.FakeFetcher(
        texts={plan.sha_url: _fakes.shasums_text((plan.filename, digest))},
        payloads={plan.urls[0]: data},
    )
    runner = _fakes.FakeRunner(lambda argv, env: _fakes.result(returncode=1, stderr="crash"))
    with pytest.raises(DevmanError) as info:
        _node.install(plan, layout, fetcher=fetcher, runner=runner)
    assert "crash" in str(info.value)


def test_install_force_replaces_existing_version(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    existing = _fakes.install_fake_node(layout, VERSION)
    (existing / "stale.txt").write_text("old", encoding="utf-8")
    data, digest = _fakes.make_node_zip(VERSION)
    plan = _node.plan_install(layout, VERSION, sha256=digest)
    fetcher = _fakes.FakeFetcher(payloads={plan.urls[0]: data})
    _node.install(plan, layout, fetcher=fetcher, runner=_good_runner(), force=True)
    assert not (existing / "stale.txt").exists()


# ---------------------------------------------------------------------- #
# 离线导入
# ---------------------------------------------------------------------- #


def _local_zip(tmp_path: Path, version: str = VERSION) -> tuple[Path, str]:
    data, digest = _fakes.make_node_zip(version)
    artifact = tmp_path / f"node-v{version}-win-x64.zip"
    artifact.write_bytes(data)
    return artifact, digest


def test_plan_local_variants(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    artifact, digest = _local_zip(tmp_path)
    plan = _node.plan_local(layout, VERSION, artifact)
    assert plan.urls == ()
    assert plan.local == artifact
    assert plan.cache_path == layout.cache_dir / artifact.name
    assert plan.to_data()["local"] == str(artifact)
    assert plan.to_data()["sha256_url"] is None

    explicit = _node.plan_local(layout, VERSION, artifact, sha256=digest)
    assert explicit.expected_sha256 == digest
    with pytest.raises(DevmanError) as info:
        _node.plan_local(layout, "20", artifact)
    assert info.value.code == "INVALID_ARGUMENT"
    with pytest.raises(DevmanError) as info:
        _node.plan_local(layout, VERSION, tmp_path / "missing.zip")
    assert info.value.code == "INVALID_ARGUMENT"


def test_install_from_local_zip_with_sidecar(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    artifact, digest = _local_zip(tmp_path)
    artifact.with_name(artifact.name + ".sha256").write_text(f"{digest}  {artifact.name}\n", encoding="utf-8")
    plan = _node.plan_local(layout, VERSION, artifact)
    fetcher = _fakes.FakeFetcher()
    result = _node.install(plan, layout, fetcher=fetcher, runner=_good_runner())
    assert result["changed"] is True
    assert result["actions"][0]["action"] == "import"
    assert result["actions"][0]["verify"] == "local-sha256"
    assert fetcher.text_calls == [] and fetcher.download_calls == []
    marker = (plan.dest / ".devman.json").read_text(encoding="utf-8")
    assert '"verify": "local-sha256"' in marker
    assert str(artifact).replace("\\", "\\\\") in marker


def test_install_from_local_zip_with_shasums_manifest(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    artifact, digest = _local_zip(tmp_path)
    (tmp_path / "SHASUMS256.txt").write_text(_fakes.shasums_text((artifact.name, digest)), encoding="utf-8")
    plan = _node.plan_local(layout, VERSION, artifact)
    result = _node.install(plan, layout, fetcher=_fakes.FakeFetcher(), runner=_good_runner())
    assert result["actions"][0]["verify"] == "local-shasums"


def test_install_from_local_zip_with_bare_sha_file(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    artifact, digest = _local_zip(tmp_path)
    artifact.with_name(artifact.name + ".sha256").write_text(digest + "\n", encoding="utf-8")
    plan = _node.plan_local(layout, VERSION, artifact)
    result = _node.install(plan, layout, fetcher=_fakes.FakeFetcher(), runner=_good_runner())
    assert result["changed"] is True


def test_install_from_local_requires_checksum(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    artifact, _digest = _local_zip(tmp_path)
    plan = _node.plan_local(layout, VERSION, artifact)
    with pytest.raises(DevmanError) as info:
        _node.install(plan, layout, fetcher=_fakes.FakeFetcher(), runner=_good_runner())
    assert info.value.code == "INVALID_ARGUMENT"
    assert not plan.dest.exists()


def test_install_from_local_checksum_mismatch(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    artifact, _digest = _local_zip(tmp_path)
    artifact.with_name(artifact.name + ".sha256").write_text(f"{'0' * 64}  {artifact.name}\n", encoding="utf-8")
    plan = _node.plan_local(layout, VERSION, artifact)
    with pytest.raises(DevmanError) as info:
        _node.install(plan, layout, fetcher=_fakes.FakeFetcher(), runner=_good_runner())
    assert info.value.code == "CHECKSUM_MISMATCH"
    assert not plan.cache_path.exists()


def test_install_runner_failure_cleans_staging(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    data, digest = _fakes.make_node_zip(VERSION)
    plan = _node.plan_install(layout, VERSION, sha256=digest)
    fetcher = _fakes.FakeFetcher(payloads={plan.urls[0]: data})

    def broken(argv: Any, env: Any) -> Any:
        raise OSError("cannot execute")

    with pytest.raises(OSError):
        _node.install(plan, layout, fetcher=fetcher, runner=_fakes.FakeRunner(broken))
    assert not list(layout.node_root.glob(".staging-*"))
