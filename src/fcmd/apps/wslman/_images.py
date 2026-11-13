"""可用镜像：``wsl --list --online`` 与微软 ``DistributionInfo.json`` 的合并、缓存与降级。

两个官方来源各有短板，合起来才接近「所有可用镜像」：

- ``wsl --list --online``：本机 WSL 当前能 ``--install`` 的名称与友好名；无架构、无体积、
  无校验和，老版本可能只有名称。
- 微软 WSL 仓库的 ``DistributionInfo.json``：``ModernDistributions`` 给出 amd64 / arm64
  的 ``.wsl`` 下载地址与 sha256；``Distributions`` 是旧版 Store 包（``.appx``）条目，
  只能走 ``wsl --install``。两者都不含下载体积。

缓存位于 ``${FCMD_HOME:-~/.fcmd}/cache/wslman-available.json``；在线源失败时读缓存并
标记 ``stale``，不静默返回空列表。
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from fcmd.apps.wslman._decode import OnlineRow
from fcmd.apps.wslman._errors import runtime_error

__all__ = [
    "CACHE_TTL_SECONDS",
    "MANIFEST_URL",
    "ImageCache",
    "ImageCatalog",
    "ImageDownload",
    "ImageEntry",
    "cache_path",
    "collect_images",
    "load_cache",
    "mark_local_state",
    "merge_entries",
    "parse_distribution_info",
    "save_cache",
]

MANIFEST_URL = "https://raw.githubusercontent.com/microsoft/WSL/master/distributions/DistributionInfo.json"
"""微软官方发行版清单（ModernDistributions + 旧版 Distributions），只走官方来源。"""

CACHE_TTL_SECONDS = 30 * 60
"""缓存有效期 30 分钟。

发行版清单以周 / 月为单位变化，但用户可能刚更新 WSL；30 分钟内的重复调用（脚本
连续跑、GUI 重开下载页）直接命中缓存，省掉一次 ``wsl.exe``（约 0.9–1.4 s）加一次
约 19 KB 的 JSON 下载，同时把「过期」控制在半小时以内。
"""

CACHE_SCHEMA = "wslman/available-cache/1"

_ARCH_X86 = "x86_64"
_ARCH_ARM = "aarch64"
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_HEX_PREFIXES = ("0x", "sha256:")


@dataclass(frozen=True, slots=True)
class ImageDownload:
    """某个架构的可导入归档（``.wsl`` / ``.appx``）。"""

    arch: str
    url: str
    sha256: str | None = None

    def to_data(self) -> dict[str, object]:
        return {"arch": self.arch, "url": self.url, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class ImageEntry:
    """一个可用镜像；``sources`` 取值 ``cli`` / ``manifest`` / ``store``。"""

    name: str
    friendly_name: str
    sources: tuple[str, ...]
    downloads: tuple[ImageDownload, ...] = ()
    installed: bool = False
    backed_up: bool = False

    @property
    def arches(self) -> tuple[str, ...]:
        return tuple(sorted({download.arch for download in self.downloads}))

    @property
    def has_sha256(self) -> bool:
        return any(download.sha256 for download in self.downloads)

    def to_data(self) -> dict[str, object]:
        return {
            "name": self.name,
            "friendly_name": self.friendly_name,
            "arches": list(self.arches),
            "download_sources": list(self.sources),
            "has_sha256": self.has_sha256,
            "installed": self.installed,
            "backed_up": self.backed_up,
            "downloads": [download.to_data() for download in self.downloads],
        }

    def to_cache_data(self) -> dict[str, object]:
        """缓存只存来源信息；已安装 / 已备份是本地状态，读取时重算。"""
        data = self.to_data()
        data.pop("installed")
        data.pop("backed_up")
        return data


@dataclass(frozen=True, slots=True)
class ImageCache:
    entries: tuple[ImageEntry, ...]
    fetched_at: float
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ImageCatalog:
    """一次 ``available`` 的结果：数据、来源、时间与降级标记。"""

    entries: tuple[ImageEntry, ...]
    sources: tuple[str, ...]
    fetched_at: float
    from_cache: bool
    stale: bool
    warnings: tuple[str, ...] = ()


def cache_path() -> Path:
    """``${FCMD_HOME:-~/.fcmd}/cache/wslman-available.json``。"""
    home = os.environ.get("FCMD_HOME")
    base = Path(home) if home else Path.home() / ".fcmd"
    return base / "cache" / "wslman-available.json"


def _sha256(value: Any) -> str | None:
    """规范化 sha256：去掉 ``0x`` / ``sha256:`` 前缀，只认 64 位十六进制。"""
    if not isinstance(value, str):
        return None
    text = value.strip().casefold()
    for prefix in _HEX_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text if _SHA_RE.fullmatch(text) else None


def _url(value: Any) -> tuple[str, str | None] | None:
    """把 ``{"Url": ..., "Sha256": ...}`` 或裸 URL 统一成 ``(url, sha256)``。"""
    if isinstance(value, Mapping):
        url = value.get("Url") or value.get("url")
        digest = value.get("Sha256") or value.get("sha256")
        return (url.strip(), _sha256(digest)) if isinstance(url, str) and url.strip() else None
    if isinstance(value, str) and value.strip():
        return value.strip(), None
    return None


def _modern_downloads(item: Mapping[str, Any]) -> tuple[ImageDownload, ...]:
    downloads: list[ImageDownload] = []
    for key, arch in (("Amd64Url", _ARCH_X86), ("Arm64Url", _ARCH_ARM)):
        parsed = _url(item.get(key))
        if parsed is not None:
            downloads.append(ImageDownload(arch=arch, url=parsed[0], sha256=parsed[1]))
    return tuple(downloads)


def _legacy_entry(item: Mapping[str, Any]) -> ImageEntry | None:
    name = item.get("Name")
    if not isinstance(name, str) or not name.strip():
        return None
    downloads: list[ImageDownload] = []
    for key, flag, arch in (
        ("Amd64PackageUrl", "Amd64", _ARCH_X86),
        ("Arm64PackageUrl", "Arm64", _ARCH_ARM),
    ):
        url = item.get(key)
        if isinstance(url, str) and url.strip() and item.get(flag, True):
            downloads.append(ImageDownload(arch=arch, url=url.strip()))
    friendly = item.get("FriendlyName")
    return ImageEntry(
        name=name.strip(),
        friendly_name=friendly.strip() if isinstance(friendly, str) else "",
        sources=("store",),
        downloads=tuple(downloads),
    )


def _load_manifest(raw: bytes | str | Any) -> Mapping[str, Any]:
    """把 bytes / str / 已解析对象统一成映射；结构与 JSON 错误转成带提示的运行错误。"""
    if isinstance(raw, (bytes, bytearray, str)):
        try:
            raw = json.loads(raw.decode("utf-8-sig") if isinstance(raw, (bytes, bytearray)) else raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise runtime_error(f"官方镜像清单不是合法 JSON：{exc}", hint=MANIFEST_URL) from exc
    if not isinstance(raw, Mapping):
        raise runtime_error("官方镜像清单结构不是对象", hint=MANIFEST_URL)
    return raw


def _modern_entries(modern: Any) -> tuple[list[ImageEntry], set[str]]:
    """解析 ``ModernDistributions``（``{分组: [条目, ...]}``），返回条目与已见名称。"""
    sections = modern.values() if isinstance(modern, Mapping) else [modern] if isinstance(modern, list) else []
    entries: list[ImageEntry] = []
    seen: set[str] = set()
    for section in sections:
        if not isinstance(section, list):
            continue
        for item in section:
            if not isinstance(item, Mapping):
                continue
            name = item.get("Name")
            if not isinstance(name, str) or not name.strip():
                continue
            key = name.strip().casefold()
            if key in seen:
                continue
            seen.add(key)
            friendly = item.get("FriendlyName")
            entries.append(
                ImageEntry(
                    name=name.strip(),
                    friendly_name=friendly.strip() if isinstance(friendly, str) else "",
                    sources=("manifest",),
                    downloads=_modern_downloads(item),
                )
            )
    return entries, seen


def parse_distribution_info(raw: bytes | str | Any) -> tuple[ImageEntry, ...]:
    """解析官方清单：ModernDistributions 优先，旧版 Distributions 只补未出现的名称。"""
    data = _load_manifest(raw)
    entries, seen = _modern_entries(data.get("ModernDistributions"))

    legacy = data.get("Distributions")
    for item in legacy if isinstance(legacy, list) else []:
        entry = _legacy_entry(item) if isinstance(item, Mapping) else None
        if entry is None or entry.name.casefold() in seen:
            continue
        seen.add(entry.name.casefold())
        entries.append(entry)
    return tuple(entries)


def merge_entries(cli_rows: Sequence[OnlineRow], manifest_entries: Sequence[ImageEntry]) -> tuple[ImageEntry, ...]:
    """按名称合并两个来源：CLI 决定顺序，清单补架构 / 下载地址 / sha256。"""
    by_name = {entry.name.casefold(): entry for entry in manifest_entries}
    merged: list[ImageEntry] = []
    seen: set[str] = set()
    for row in cli_rows:
        key = row.name.casefold()
        if key in seen:
            continue
        seen.add(key)
        match = by_name.get(key)
        sources: list[str] = ["cli"]
        downloads: tuple[ImageDownload, ...] = ()
        if match is not None:
            sources.extend(source for source in match.sources if source not in sources)
            downloads = match.downloads
        merged.append(
            ImageEntry(
                name=row.name,
                friendly_name=row.friendly_name or (match.friendly_name if match else ""),
                sources=tuple(sources),
                downloads=downloads,
            )
        )
    for entry in manifest_entries:
        if entry.name.casefold() in seen:
            continue
        seen.add(entry.name.casefold())
        merged.append(entry)
    return tuple(merged)


def load_cache(path: Path | None = None) -> ImageCache | None:
    """读取缓存；文件缺失、损坏或结构不符时返回 ``None``。"""
    try:
        raw = json.loads((path or cache_path()).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, Mapping) or raw.get("schema") != CACHE_SCHEMA:
        return None
    entries_raw = raw.get("entries")
    fetched_at = raw.get("fetched_at")
    if not isinstance(entries_raw, list) or not isinstance(fetched_at, (int, float)):
        return None
    entries: list[ImageEntry] = []
    for item in entries_raw:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        downloads: list[ImageDownload] = []
        for download in item.get("downloads") or []:
            if not isinstance(download, Mapping):
                continue
            arch = download.get("arch")
            url = download.get("url")
            if isinstance(arch, str) and isinstance(url, str) and url:
                sha = download.get("sha256")
                downloads.append(ImageDownload(arch, url, sha if isinstance(sha, str) else None))
        entry_sources = item.get("download_sources")
        friendly = item.get("friendly_name")
        entries.append(
            ImageEntry(
                name=name,
                friendly_name=friendly if isinstance(friendly, str) else "",
                sources=tuple(str(source) for source in entry_sources) if isinstance(entry_sources, list) else (),
                downloads=tuple(downloads),
            )
        )
    cache_sources = raw.get("sources")
    return ImageCache(
        entries=tuple(entries),
        fetched_at=float(fetched_at),
        sources=tuple(str(source) for source in cache_sources) if isinstance(cache_sources, list) else (),
    )


def save_cache(
    entries: Sequence[ImageEntry],
    fetched_at: float,
    path: Path | None = None,
    sources: Sequence[str] = (),
) -> None:
    """写缓存；失败（只读目录 / 磁盘满）静默跳过，不影响本次结果。"""
    target = path or cache_path()
    payload = {
        "schema": CACHE_SCHEMA,
        "fetched_at": fetched_at,
        "sources": list(sources),
        "entries": [entry.to_cache_data() for entry in entries],
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return


def _age_text(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} 分钟"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} 小时"
    return f"{seconds / 86400:.1f} 天"


def collect_images(
    *,
    fetch_cli: Callable[[], Sequence[OnlineRow]],
    fetch_manifest: Callable[[], Sequence[ImageEntry]],
    now: Callable[[], float] = time.time,
    cache_file: Path | None = None,
    fatal: Callable[[Exception], bool] | None = None,
) -> ImageCatalog:
    """并发无关的编排：两个源各自失败不影响另一个，必要时用缓存补齐。

    - 两个源都成功：合并后用 ``now()`` 作数据时间，并刷新缓存；
    - 只有一个源成功：用它出列表（没有缓存可补时也不报错），并写入缓存；
    - 部分失败但有缓存：成功的源 + 缓存补齐缺的条目，标记 ``stale``；
    - 全部失败：只用缓存，标记 ``stale`` 与「已过期」；
    - 全部失败且没有缓存：抛出 CLI 的原错误，保留既有退出码语义。

    ``fatal`` 用来把「不算抓取失败」的错误（例如 WSL 未启用）直接上抛，
    避免被当成可降级的单源故障。
    """
    warnings: list[str] = []
    cache = load_cache(cache_file)
    current = now()
    if cache is not None and cache.entries and current - cache.fetched_at <= CACHE_TTL_SECONDS:
        # 命中 TTL：直接复用上次结果，不再拉起 wsl.exe 也不再下载清单。
        return ImageCatalog(
            entries=cache.entries,
            sources=cache.sources or ("cache",),
            fetched_at=cache.fetched_at,
            from_cache=True,
            stale=False,
        )
    cli_rows: tuple[OnlineRow, ...] | None = None
    manifest_entries: tuple[ImageEntry, ...] | None = None
    cli_error: Exception | None = None
    try:
        cli_rows = tuple(fetch_cli())
    except Exception as exc:
        if fatal is not None and fatal(exc):
            raise
        cli_error = exc
        warnings.append(f"wsl --list --online 不可用：{exc}")
    try:
        manifest_entries = tuple(fetch_manifest())
    except Exception as exc:
        warnings.append(f"官方镜像清单不可用：{exc}")

    if cli_rows is None and manifest_entries is None and cache is None:
        # 两条在线源都拿不到、也没有缓存时才报错：优先抛 CLI 的原错误（退出码语义不变）。
        # 只失败一个源不算错误——``wsl --list --online`` 自身要联网下载清单，慢且易超时，
        # 官方 DistributionInfo.json 通常仍可直连；此时用它给出完整列表比直接失败有用。
        if cli_error is not None:
            raise cli_error
        raise runtime_error(
            "在线镜像清单不可用且没有可用缓存",
            hint="检查网络或代理；也可先运行 fcmd wslman available 联网缓存，或改用 --source file",
        )
    if cli_rows is None and manifest_entries is None:
        if cache is None:  # 不可达：上面的分支已拦截，这里只做类型收窄
            raise runtime_error("在线镜像清单不可用且没有可用缓存", hint="检查网络或代理")
        return _cache_catalog(cache, current, warnings)

    # 只有一个源成功时，用缓存补另一个来源的条目，避免列表突然缩水。
    cli_fresh = cli_rows is not None
    manifest_fresh = manifest_entries is not None
    cli_rows, manifest_entries = _backfill_from_cache(cli_rows, manifest_entries, cache)
    merged = merge_entries(cli_rows, manifest_entries)
    fetched_at = current
    used_cache = cache is not None and not (cli_fresh and manifest_fresh)
    if used_cache and cache is not None and cache.fetched_at:
        # 混合来源时，时间取缓存里最新的一次，避免把旧数据标成刚刚获取。
        fetched_at = min(fetched_at, cache.fetched_at)
    sources = tuple(source for source, ok in (("cli", cli_fresh), ("manifest", manifest_fresh)) if ok)
    if merged and not used_cache:
        # 全部条目都来自本次在线抓取才写缓存（含「只成功一个源且没有缓存可补」的情况），
        # 这样下次单源失效时至少有上一次的真实列表可降级；混合缓存的结果不落盘。
        save_cache(merged, fetched_at, cache_file, sources)
    if used_cache:
        warnings.append("部分来源不可用，已用上次缓存补齐；条目可能不是最新")
    return ImageCatalog(
        entries=merged,
        sources=sources,
        fetched_at=fetched_at,
        from_cache=False,
        stale=used_cache,
        warnings=tuple(warnings),
    )


def _backfill_from_cache(
    cli_rows: tuple[OnlineRow, ...] | None,
    manifest_entries: tuple[ImageEntry, ...] | None,
    cache: ImageCache | None,
) -> tuple[tuple[OnlineRow, ...], tuple[ImageEntry, ...]]:
    """单源成功时用缓存补另一个来源缺的条目；没有缓存就原样返回。"""
    if cache is None:
        return cli_rows or (), manifest_entries or ()
    if cli_rows is None:
        cli_rows = tuple(
            OnlineRow(name=entry.name, friendly_name=entry.friendly_name)
            for entry in cache.entries
            if "cli" in entry.sources
        )
    if manifest_entries is None:
        manifest_entries = tuple(entry for entry in cache.entries if set(entry.sources) - {"cli"})
    return cli_rows, manifest_entries


def _cache_catalog(cache: ImageCache, current: float, warnings: list[str]) -> ImageCatalog:
    """两个在线源都不可用时的缓存兜底；明确标注过期，不静默返回空列表。"""
    age = max(0.0, current - cache.fetched_at)
    warnings.append(
        f"在线源均不可用，使用缓存（获取于 {_age_text(age)}前，已过期）"
        if age > CACHE_TTL_SECONDS
        else f"在线源均不可用，使用缓存（获取于 {_age_text(age)}前）"
    )
    return ImageCatalog(
        entries=cache.entries,
        sources=("cache",),
        fetched_at=cache.fetched_at,
        from_cache=True,
        stale=True,
        warnings=tuple(warnings),
    )


def mark_local_state(
    entries: Sequence[ImageEntry],
    *,
    installed: Sequence[str],
    backups: Sequence[str],
) -> tuple[ImageEntry, ...]:
    """按已安装名称与备份名称标注条目（大小写不敏感）。"""
    installed_keys = {name.casefold() for name in installed}
    backup_keys = {name.casefold() for name in backups}
    return tuple(
        replace(
            entry,
            installed=entry.name.casefold() in installed_keys,
            backed_up=entry.name.casefold() in backup_keys,
        )
        for entry in entries
    )
