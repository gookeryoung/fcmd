"""镜像源公用件测试：候选生成、测速排序与配置写入。"""

from __future__ import annotations

from pathlib import Path

from fcmd.infras.net.mirrors import (
    Candidate,
    Mirror,
    MirrorSpec,
    Proxy,
    apply_mirror_config,
    build_candidates,
    rank_candidates,
)

_MIRROR = Mirror(
    "ustc",
    "中科大",
    "https://mirrors.ustc.edu.cn",
    (("https://releases.example.com/", "releases/"),),
)
_PROXY = Proxy("ghfast", "加速", "https://ghfast.top/", frozenset({"github.com"}))


def test_build_candidates_rewrites_mirror_and_proxy() -> None:
    assert build_candidates("https://other.example.com/a", mirrors=(_MIRROR,), proxies=(_PROXY,)) == (
        Candidate("official", "官方源", "https://other.example.com/a", True),
    )
    mirrored = build_candidates("https://releases.example.com/x.iso", mirrors=(_MIRROR,))
    assert [item.url for item in mirrored] == [
        "https://mirrors.ustc.edu.cn/releases/x.iso",
        "https://releases.example.com/x.iso",
    ]

    proxied = build_candidates("https://github.com/o/r/releases/download/v1/a.zip", proxies=(_PROXY,))
    assert [item.ident for item in proxied] == ["ghfast", "official"]
    # 无官方 sha256 可用时只用官方源，避免未校验的第三方字节。
    assert [item.ident for item in build_candidates("https://github.com/o/r", proxies=(_PROXY,), verifiable=False)] == [
        "official"
    ]
    explicit = build_candidates("https://releases.example.com/x.iso", mirrors=(_MIRROR,), policy="official")
    assert [item.ident for item in explicit] == ["official"]


def test_rank_candidates_uses_throughput_then_latency() -> None:
    slow = Candidate("slow", "慢", "https://slow.example/x", True)
    fast = Candidate("fast", "快", "https://fast.example/x", True)
    ranked = rank_candidates(
        (slow, fast),
        probe=lambda url: 2.0 if "fast" in url else 1.0,
        latency=lambda _url: 0.0,
    )
    assert [item.ident for item in ranked] == ["fast", "slow"]

    fallback = rank_candidates(
        (slow, fast),
        probe=lambda _url: 0.0,
        latency=lambda url: 10.0 if "slow" in url else 20.0,
    )
    assert [item.ident for item in fallback] == ["slow", "fast"]
    assert rank_candidates((slow,), probe=lambda _url: 0.0, latency=lambda _url: 0.0) == (slow,)


def test_apply_mirror_config_writes_env_dirs_and_file(tmp_path: Path) -> None:
    target = tmp_path / "config" / "pip.ini"
    cache = tmp_path / "cache"
    written: dict[str, str] = {}
    spec = MirrorSpec(
        env_vars={"PIP_INDEX_URL": "https://mirror.example/simple"},
        config_path=target,
        config_content="[global]\nindex-url = https://mirror.example/simple\n",
        ensure_dirs=[cache],
    )
    apply_mirror_config(spec, persist_fn=lambda name, value: written.update({name: value}), label="Python")
    assert written == {"PIP_INDEX_URL": "https://mirror.example/simple"}
    assert cache.is_dir()
    assert "mirror.example" in target.read_text(encoding="utf-8")
