"""mirrortoolw —— 镜像搜索与配置窗口版（Qt）。

复用 ``envdev mirror`` / ``mirrortool`` 的候选镜像表、实测下载吞吐探测与配置
写入实现：按服务（pip/conda/rustup/cargo/go/maven/npm）测速排序并一键应用，
或探测 MirrorZ 教育网镜像站点；结果可按镜像名/地址过滤。

设置（模式、服务、超时、来源、搜索词、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/mirrortoolw.json``，下次启动自动恢复。

示例
----
    fcmd mirrortoolw      # 打开镜像搜索与配置窗口
    mirrortoolw           # 全局命令等价入口
"""

from __future__ import annotations

import contextlib
import io
from dataclasses import dataclass
from pathlib import Path

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import window as _guiwindow

__all__ = [
    "DEFAULT_GEOMETRY",
    "DEFAULT_TIMEOUT",
    "MAX_TIMEOUT",
    "MIN_TIMEOUT",
    "MODE_SERVICE",
    "MODE_SITES",
    "SERVICE_LABELS",
    "MirrorResult",
    "MirrorToolSettings",
    "apply_service_mirror",
    "config_location",
    "config_path",
    "filter_results",
    "load_settings",
    "normalize_timeout",
    "probe_service",
    "probe_sites",
    "recommend_mirror",
    "resolve_geometry",
    "run_gui",
    "save_settings",
    "service_candidates",
    "valid_geometry",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "镜像搜索与配置窗口版（Qt）：按服务（pip/conda/rustup/cargo/go/maven/npm）"
    "实测下载吞吐排序并一键应用镜像，或探测 MirrorZ 教育网镜像站点；"
    "结果支持按镜像名/地址过滤，设置持久化到 ~/.fcmd/ui/mirrortoolw.json"
)

MODE_SERVICE = "service"
MODE_SITES = "sites"
MODES: tuple[str, ...] = (MODE_SERVICE, MODE_SITES)

SERVICE_LABELS: dict[str, str] = {
    "pip": "Python (pip/uv)",
    "conda": "Python (conda)",
    "rustup": "Rust (rustup)",
    "cargo": "Rust (cargo/crates.io)",
    "go": "Go (GOPROXY)",
    "maven": "Java (Maven)",
    "npm": "Node.js (npm/yarn/pnpm)",
}

SOURCE_LABELS: dict[str, str] = {
    "auto": "自动拉取 MirrorZ",
    "builtin": "内置站点列表",
}

DEFAULT_TIMEOUT = 3.0
MIN_TIMEOUT = 0.5
MAX_TIMEOUT = 30.0
DEFAULT_GEOMETRY = "1040x720+180+100"
MIN_WIDTH = 900
MIN_HEIGHT = 620

_ENV_ONLY_LOCATION = "环境变量（重开终端后生效）"


# ---------------------------------------------------------------------- #
# 结果模型
# ---------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class MirrorResult:
    """单条镜像 / 站点探测结果。"""

    name: str
    url: str
    ok: bool
    latency_ms: float
    throughput_kbps: float = 0.0
    reason: str = ""

    @property
    def status_text(self) -> str:
        """可读状态：可访问，或不可访问并附原因。"""
        if self.ok:
            return "可访问"
        return f"不可访问（{self.reason or '未知原因'}）"

    @property
    def latency_text(self) -> str:
        """可读延迟；不可达时用 ``—`` 占位。"""
        return f"{self.latency_ms:.0f} ms" if self.ok else "—"

    @property
    def throughput_text(self) -> str:
        """可读下载吞吐；站点探测与不可达项用 ``—`` 占位。"""
        if not self.ok or self.throughput_kbps <= 0:
            return "—"
        return f"{self.throughput_kbps:.0f} KB/s"


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/mirrortoolw.json``。"""
    return _guiconfig.config_path("mirrortoolw")


@dataclass
class MirrorToolSettings:
    """镜像搜索与配置窗口的持久化设置。"""

    mode: str = MODE_SERVICE
    service: str = "pip"
    timeout: float = DEFAULT_TIMEOUT
    source: str = "auto"
    search: str = ""
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


def normalize_timeout(value: object) -> float:
    """校验并规范化超时秒数（``0.5``–``30``），非法时抛出 ``ValueError``。"""
    message = f"超时必须是 {MIN_TIMEOUT:g}–{MAX_TIMEOUT:g} 秒之间的数字"
    if isinstance(value, bool):
        raise ValueError(message)
    if isinstance(value, (int, float)):
        timeout = float(value)
    elif isinstance(value, str):
        try:
            timeout = float(value.strip())
        except ValueError as exc:
            raise ValueError(message) from exc
    else:
        raise ValueError(message)
    if not MIN_TIMEOUT <= timeout <= MAX_TIMEOUT:
        raise ValueError(message)
    return timeout


def load_settings(path: Path | None = None) -> MirrorToolSettings:
    """读取设置文件；缺失、损坏或字段非法时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return MirrorToolSettings()

    settings = MirrorToolSettings()
    mode = raw.get("mode")
    service = raw.get("service")
    source = raw.get("source")
    search = raw.get("search")
    geometry = raw.get("geometry")
    if isinstance(mode, str) and mode in MODES:
        settings.mode = mode
    if isinstance(service, str) and service in SERVICE_LABELS:
        settings.service = service
    if isinstance(source, str) and source in SOURCE_LABELS:
        settings.source = source
    if isinstance(search, str):
        settings.search = search
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    if "timeout" in raw:
        with contextlib.suppress(ValueError):
            settings.timeout = normalize_timeout(raw["timeout"])
    return settings


def save_settings(settings: MirrorToolSettings, path: Path | None = None) -> None:
    """保存设置；写入失败时静默忽略（不影响窗口关闭）。"""
    payload = {
        "mode": settings.mode,
        "service": settings.service,
        "timeout": settings.timeout,
        "source": settings.source,
        "search": settings.search,
        "geometry": settings.geometry,
    }
    _guiconfig.write_settings(payload, path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认 geometry（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


# ---------------------------------------------------------------------- #
# 镜像候选与探测（与 CLI 共用同一份候选表与实现）
# ---------------------------------------------------------------------- #
def service_candidates(service: str) -> dict[str, str]:
    """返回指定服务的 ``{镜像名: 测速地址}`` 候选表副本。

    Raises
    ------
    ValueError
        服务名不在 ``pip`` / ``conda`` / ``rustup`` / ``cargo`` / ``go`` /
        ``maven`` / ``npm`` 中。
    """
    from fcmd.tools.dev._envdev_mirrors import _MIRROR_SERVICE_CANDIDATES

    if service not in _MIRROR_SERVICE_CANDIDATES:
        raise ValueError(f"未知服务: {service}")
    return dict(_MIRROR_SERVICE_CANDIDATES[service])


def probe_service(service: str, timeout: float = DEFAULT_TIMEOUT) -> list[MirrorResult]:
    """探测单个服务的全部候选镜像，返回按实测吞吐与延迟排序的结果。"""
    from fcmd.infras.net import probe_mirrors
    from fcmd.tools.dev.envdev_core import _MIRROR_DEFAULT_UA, _MIRROR_USER_AGENTS

    candidates = service_candidates(service)
    name_by_url = {url: name for name, url in candidates.items()}
    probes = probe_mirrors(
        list(candidates.values()),
        timeout=timeout,
        user_agent=_MIRROR_USER_AGENTS.get(service, _MIRROR_DEFAULT_UA),
        allow_html=service == "pip",
    )
    return [
        MirrorResult(
            name=name_by_url[probe.url],
            url=probe.url,
            ok=probe.ok,
            latency_ms=probe.latency_ms,
            throughput_kbps=probe.throughput_kbps,
            reason=probe.reason,
        )
        for probe in probes
        if probe.url in name_by_url
    ]


def probe_sites(timeout: float = DEFAULT_TIMEOUT, source: str = "auto") -> tuple[list[MirrorResult], str]:
    """探测教育网镜像站点，返回 ``(按速度排序的站点, 列表来源说明)``。"""
    from fcmd.infras.net import check_urls
    from fcmd.tools.dev._envdev_mirrors import _CERNET_MIRROR_SITES, fetch_mirrorz_sites

    sites = dict(_CERNET_MIRROR_SITES)
    origin = "help.mirrors.cernet.edu.cn 内置列表"
    if source == "auto":
        fetched = fetch_mirrorz_sites(timeout)
        if fetched is None:
            origin = "MirrorZ 拉取失败，已回退内置列表"
        else:
            sites = fetched
            origin = "mirrors.cernet.edu.cn 动态列表"

    name_by_url = {url: name for name, url in sites.items()}
    results = check_urls(list(sites.values()), timeout=timeout)
    rows = [
        MirrorResult(name=name_by_url[url], url=url, ok=ok, latency_ms=latency)
        for url, ok, latency in results
        if url in name_by_url
    ]
    return rows, origin


def recommend_mirror(results: list[MirrorResult]) -> str | None:
    """返回排序结果中第一个可达镜像名；全部不可达返回 ``None``。"""
    return next((row.name for row in results if row.ok), None)


def filter_results(results: list[MirrorResult], keyword: str) -> list[MirrorResult]:
    """按镜像名或地址做大小写不敏感过滤；关键词为空时返回原列表副本。"""
    text = keyword.strip().lower()
    if not text:
        return list(results)
    return [row for row in results if text in row.name.lower() or text in row.url.lower()]


def apply_service_mirror(service: str, mirror: str) -> str:
    """把镜像写入对应服务配置，返回被捕获的命令输出。

    Raises
    ------
    ValueError
        服务或镜像名不在候选表中。
    """
    from fcmd.tools.dev._envdev_mirrors import _MIRROR_SERVICE_APPLY

    candidates = service_candidates(service)
    if mirror not in candidates:
        raise ValueError(f"未知 {service} 镜像: {mirror}")
    applier = _MIRROR_SERVICE_APPLY[service]
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        applier(mirror)
    return buffer.getvalue().strip()


def config_location(service: str) -> str:
    """返回服务配置位置：配置文件路径；仅环境变量的服务返回提示文案。"""
    if service not in SERVICE_LABELS:
        raise ValueError(f"未知服务: {service}")
    from fcmd.tools.dev.envdev_core import pip_config_path

    if service == "pip":
        return str(pip_config_path())
    if service == "conda":
        return str(Path.home() / ".condarc")
    if service == "cargo":
        return str(Path.home() / ".cargo" / "config.toml")
    if service == "maven":
        return str(Path.home() / ".m2" / "settings.xml")
    return _ENV_ONLY_LOCATION


# ---------------------------------------------------------------------- #
# GUI 入口
# ---------------------------------------------------------------------- #
def run_gui(settings_path: Path | None = None) -> None:
    """启动 Qt 窗口（高 DPI 与主题由 ``gui.qt.app`` 统一处理）。"""
    try:
        from fcmd.tools.dev._mirrortoolw_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 安装完整时不会触发
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc

    run_qt(settings_path)


@fcmd.tool("mirrortoolw", help="打开镜像搜索与配置窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开镜像搜索与配置窗口。"""
    run_gui()


@fcmd.main("mirrortoolw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
