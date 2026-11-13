"""fonttool - 推荐字体按需安装与管理（CLI）。

fcmd 的 Tk 窗口需要好看的中英文字体，但把字体打进发行包会让体积明显变大。
本工具改为按需下载并安装到当前用户字体目录，不随 fcmd 分发字体文件：

- Windows：``%LOCALAPPDATA%\\Microsoft\\Windows\\Fonts``（写入 HKCU 字体注册表）
- macOS：``~/Library/Fonts``
- Linux / WSL：``${XDG_DATA_HOME:-~/.local/share}/fonts``，随后调用 ``fc-cache``

默认安装两款 SIL OFL 1.1 字体，下载后校验 SHA256 并保存许可证：

- ``cascadia-code``：Cascadia Code（拉丁等宽，适合日志与代码）
- ``noto-sans-sc``：Noto Sans SC（中文黑体，8 MB 级，兼顾覆盖与体积）

安装记录保存在 ``${FCMD_HOME:-~/.fcmd}/fonts/manifest.json``，``remove`` 依据
记录精确删除字体与许可证文件。

注意：Windows / macOS 与启用 Xft 的 Linux Python（系统 ``python3-tk``）可直接
使用这些字体。uv 独立构建的 Python 自带 Tk 未启用 Xft，只识别 X11 核心字体，
安装 TTF 后仍可能看不到；这类环境请改用系统 Python，或安装 ``xfonts-wqy``
作为兜底（见 README「WSL / Linux 中文乱码」）。

示例
----
    fcmd fonttool list
    fcmd fonttool status
    fcmd fonttool install
    fcmd fonttool install --font noto-sans-sc
    fcmd fonttool install --plan
    fcmd fonttool remove --all-managed
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, cast

import fcmd
from fcmd.console import print_error

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

__all__ = [
    "DEFAULT_FONT_IDS",
    "FONTS",
    "KIND_LABELS",
    "FontSpec",
    "FontStatus",
    "FontToolError",
    "InstallOutcome",
    "InstallReport",
    "InstalledFont",
    "RemoveOutcome",
    "RemoveReport",
    "collect_status",
    "fcmd_home",
    "file_matches",
    "format_font_table",
    "format_size",
    "format_status_report",
    "install_fonts",
    "license_path",
    "load_manifest",
    "manifest_path",
    "platform_hint",
    "refresh_font_cache",
    "register_installed_fonts",
    "remove_fonts",
    "resolve_fonts",
    "save_manifest",
    "sha256_file",
    "unregister_installed_fonts",
    "user_font_dir",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "推荐字体按需安装与管理（list/status/install/remove）：默认 Cascadia Code + Noto Sans SC，"
    "下载后校验 SHA256 并保存 OFL 许可证，安装在当前用户字体目录，不内置进 fcmd"
)

_USER_AGENT: Final[str] = "fcmd-fonttool/0.1 (+https://github.com/fcmd)"
_DOWNLOAD_TIMEOUT: Final[float] = 120.0
_CHUNK_SIZE: Final[int] = 1 << 20


class FontToolError(ValueError):
    """字体安装 / 移除的业务错误（CLI 直接打印，不抛堆栈）。"""


@dataclass(frozen=True, slots=True)
class FontSpec:
    """一款可安装字体的完整描述（下载源固定版本 + SHA256）。"""

    font_id: str
    family: str
    label: str
    kind: str
    filename: str
    url: str
    sha256: str
    size: int
    license_name: str
    license_url: str


KIND_LABELS: Final[Mapping[str, str]] = MappingProxyType({"mono": "等宽", "cjk": "中文"})
"""字体类型显示名。"""

FONTS: Final[Mapping[str, FontSpec]] = MappingProxyType(
    {
        "cascadia-code": FontSpec(
            font_id="cascadia-code",
            family="Cascadia Code",
            label="Cascadia Code",
            kind="mono",
            filename="CascadiaCode-Regular.ttf",
            url="https://fonts.gstatic.com/s/cascadiacode/v5/qWc_B6-zq5zxD57cT5s916v3QjfzRuG9AIUcXuUMOaDP.ttf",
            sha256="3dabedb6fc4d0e7d7e7d6e8369f8315c8d2a0a5c70767a19e3673973f4f074f6",
            size=454416,
            license_name="SIL Open Font License 1.1",
            license_url="https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/cascadiacode/OFL.txt",
        ),
        "noto-sans-sc": FontSpec(
            font_id="noto-sans-sc",
            family="Noto Sans SC",
            label="Noto Sans SC",
            kind="cjk",
            filename="NotoSansSC-Regular.ttf",
            url="https://fonts.gstatic.com/s/notosanssc/v41/k3kCo84MPvpLmixcA63oeAL7Iqp5IZJF9bmaG9_FnYw.ttf",
            sha256="450625c8d46ab3df97b7904ded955ec2746d17ec76740cb1e91d1ba63a0f89af",
            size=10540644,
            license_name="SIL Open Font License 1.1",
            license_url="https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/notosanssc/OFL.txt",
        ),
    }
)
"""内置字体清单：固定 URL + SHA256，Google Fonts 的 gstatic 地址按版本不可变。"""

DEFAULT_FONT_IDS: Final[tuple[str, ...]] = ("cascadia-code", "noto-sans-sc")
"""不带 ``--font`` 时安装的字体。"""


@dataclass(frozen=True, slots=True)
class InstalledFont:
    """清单中的一条安装记录。"""

    font_id: str
    family: str
    path: str
    sha256: str
    license_name: str
    license_path: str
    installed_at: str


@dataclass(frozen=True, slots=True)
class FontStatus:
    """一款字体的安装状态。"""

    spec: FontSpec
    path: Path
    managed: bool
    state: str


@dataclass(frozen=True, slots=True)
class InstallOutcome:
    """单款字体的安装结果（``installed`` / ``skipped`` / ``planned``）。"""

    font_id: str
    family: str
    path: Path
    state: str


@dataclass(frozen=True, slots=True)
class InstallReport:
    """安装汇总：逐字体结果 + 字体缓存刷新提示。"""

    outcomes: tuple[InstallOutcome, ...]
    cache_message: str = ""


@dataclass(frozen=True, slots=True)
class RemoveOutcome:
    """单款字体的移除结果（``removed`` / ``planned`` / ``missing``）。"""

    font_id: str
    path: Path
    state: str


@dataclass(frozen=True, slots=True)
class RemoveReport:
    """移除汇总：逐字体结果 + 字体缓存刷新提示。"""

    outcomes: tuple[RemoveOutcome, ...]
    cache_message: str = ""


# ---------------------------------------------------------------------- #
# 路径与字体清单
# ---------------------------------------------------------------------- #
def fcmd_home(env: Mapping[str, str] | None = None) -> Path:
    """返回 ``FCMD_HOME``（默认 ``~/.fcmd``）。"""
    values = os.environ if env is None else env
    raw = values.get("FCMD_HOME")
    return Path(raw) if raw else Path.home() / ".fcmd"


def manifest_path() -> Path:
    """返回安装清单路径：``${FCMD_HOME:-~/.fcmd}/fonts/manifest.json``。"""
    return fcmd_home() / "fonts" / "manifest.json"


def user_font_dir(
    system: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """返回当前用户字体目录（按平台规则计算，不创建目录）。

    Parameters
    ----------
    system:
        平台标识，默认 ``sys.platform``；测试可显式传入 ``linux`` / ``darwin`` / ``win32``
    env:
        环境变量映射，默认 ``os.environ``
    home:
        用户主目录，默认 ``Path.home()``
    """
    plat = system or sys.platform
    values = os.environ if env is None else env
    home_dir = Path.home() if home is None else home
    if plat.startswith("win"):
        local = values.get("LOCALAPPDATA")
        base = Path(local) if local else home_dir / "AppData" / "Local"
        return base / "Microsoft" / "Windows" / "Fonts"
    if plat == "darwin":
        return home_dir / "Library" / "Fonts"
    xdg = values.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else home_dir / ".local" / "share"
    return base / "fonts"


def resolve_fonts(ids: Sequence[str] | None = None) -> list[FontSpec]:
    """把字体 ID 序列解析为 :class:`FontSpec` 列表（默认两款，去重保序）。

    支持逗号分隔写法（``cascadia-code,noto-sans-sc``）。

    Raises
    ------
    FontToolError
        字体 ID 未知或全部为空。
    """
    if not ids:
        return [FONTS[font_id] for font_id in DEFAULT_FONT_IDS]
    specs: list[FontSpec] = []
    seen: set[str] = set()
    for raw in ids:
        for part in raw.split(","):
            key = part.strip().lower()
            if not key:
                continue
            spec = FONTS.get(key)
            if spec is None:
                raise FontToolError(f"未知字体: {part.strip()}；可用: {', '.join(FONTS)}")
            if spec.font_id not in seen:
                seen.add(spec.font_id)
                specs.append(spec)
    if not specs:
        raise FontToolError(f"未指定有效字体；可用: {', '.join(FONTS)}")
    return specs


def _report_font_error(exc: FontToolError) -> None:
    """呈现字体工具错误：字体名非法 → 3，其余（下载/校验/写入）→ 1。"""
    message = str(exc)
    if message.startswith(("未知字体", "未指定有效字体", "请用 --font")):
        print_error(message, code="INVALID_ARGUMENT", hint="运行 fcmd fonttool list 查看可用字体名")
        return
    print_error(
        message,
        code="RUNTIME_ERROR",
        hint="检查网络与目标目录权限后重试；本地文件内容不一致时加 --force 覆盖",
    )


def format_size(size: int) -> str:
    """把字节数格式化为 ``N B`` / ``N.N KB`` / ``N.N MB``。"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def license_path(target_dir: Path, spec: FontSpec) -> Path:
    """返回随字体保存的许可证文件路径。"""
    return target_dir / f"{spec.font_id}.LICENSE.txt"


# ---------------------------------------------------------------------- #
# 下载与校验
# ---------------------------------------------------------------------- #
def sha256_file(path: Path) -> str:
    """分块计算文件 SHA256（支持大字体文件，不整体载入内存）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_matches(path: Path, spec: FontSpec) -> bool:
    """判断文件是否为期望字体：大小一致且 SHA256 一致。"""
    try:
        if path.stat().st_size != spec.size:
            return False
        return sha256_file(path) == spec.sha256
    except OSError:
        return False


def _download(url: str, target: Path) -> None:
    """下载 ``url`` 到 ``target``（流式写入，失败由调用方清理残留文件）。"""
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT) as response, target.open("wb") as handle:
        shutil.copyfileobj(response, handle, length=_CHUNK_SIZE)


def _fetch_text(url: str) -> str:
    """下载文本内容（许可证），超时与字体下载不同，失败交由调用方兜底。"""
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


def _verify_font(path: Path, spec: FontSpec) -> None:
    """校验下载文件的大小与 SHA256，不一致时抛 :class:`FontToolError`。"""
    try:
        actual_size = path.stat().st_size
    except OSError as exc:
        raise FontToolError(f"读取下载文件失败: {path}: {exc}") from exc
    if actual_size != spec.size:
        raise FontToolError(f"{spec.label} 下载大小不符：期望 {spec.size} 字节，实际 {actual_size} 字节")
    actual_hash = sha256_file(path)
    if actual_hash != spec.sha256:
        raise FontToolError(f"{spec.label} SHA256 校验失败：期望 {spec.sha256}，实际 {actual_hash}")


# ---------------------------------------------------------------------- #
# 安装清单
# ---------------------------------------------------------------------- #
def load_manifest(path: Path | None = None) -> dict[str, InstalledFont]:
    """读取安装清单；文件缺失、JSON 损坏或条目非法时返回空字典 / 跳过该条。"""
    target = path or manifest_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    fonts = raw.get("fonts")
    if not isinstance(fonts, dict):
        return {}
    manifest: dict[str, InstalledFont] = {}
    for font_id, entry in fonts.items():
        if not isinstance(font_id, str) or not isinstance(entry, dict):
            continue
        try:
            manifest[font_id] = InstalledFont(
                font_id=font_id,
                family=str(entry["family"]),
                path=str(entry["path"]),
                sha256=str(entry["sha256"]),
                license_name=str(entry["license_name"]),
                license_path=str(entry["license_path"]),
                installed_at=str(entry["installed_at"]),
            )
        except KeyError:
            continue
    return manifest


def save_manifest(manifest: Mapping[str, InstalledFont], path: Path | None = None) -> None:
    """原子写入安装清单（临时文件 + ``os.replace``）。"""
    target = path or manifest_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "fonts": {font_id: asdict(entry) for font_id, entry in manifest.items()}}
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
    except OSError as exc:
        raise FontToolError(f"写入安装清单失败: {target}: {exc}") from exc


def _record_installed(
    manifest: dict[str, InstalledFont],
    spec: FontSpec,
    path: Path,
    license_file: Path,
) -> bool:
    """登记一条安装记录；内容未变化时返回 ``False``。"""
    existing = manifest.get(spec.font_id)
    if (
        existing is not None
        and existing.path == str(path)
        and existing.sha256 == spec.sha256
        and existing.license_path == str(license_file)
    ):
        return False
    manifest[spec.font_id] = InstalledFont(
        font_id=spec.font_id,
        family=spec.family,
        path=str(path),
        sha256=spec.sha256,
        license_name=spec.license_name,
        license_path=str(license_file),
        installed_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    return True


def _save_license(target_dir: Path, spec: FontSpec, fetcher: Callable[[str], str]) -> Path:
    """保存许可证全文；下载失败时写入许可证名称与链接（不阻断字体安装）。"""
    target = license_path(target_dir, spec)
    try:
        text = fetcher(spec.license_url)
    except Exception:
        text = ""
    if not text.strip():
        text = f"{spec.family} 使用 {spec.license_name} 许可证。\n许可证全文: {spec.license_url}\n"
    try:
        target.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    except OSError as exc:
        raise FontToolError(f"写入许可证失败: {target}: {exc}") from exc
    return target


# ---------------------------------------------------------------------- #
# 安装 / 移除
# ---------------------------------------------------------------------- #
def install_fonts(  # noqa: PLR0913 - 安装选项显式展开，避免打包配置对象
    specs: Sequence[FontSpec],
    *,
    font_dir: Path | None = None,
    force: bool = False,
    plan: bool = False,
    downloader: Callable[[str, Path], None] | None = None,
    license_fetcher: Callable[[str], str] | None = None,
) -> InstallReport:
    """下载并安装字体到用户字体目录，更新清单并刷新字体缓存。

    Parameters
    ----------
    specs:
        待安装字体（一般来自 :func:`resolve_fonts`）
    font_dir:
        自定义字体目录；缺省按平台规则（:func:`user_font_dir`）
    force:
        目标文件已存在但校验不一致时是否覆盖
    plan:
        仅生成执行计划，不创建目录、不下载、不写清单
    downloader:
        下载实现，签名 ``(url, target) -> None``；测试可注入
    license_fetcher:
        许可证下载实现，签名 ``(url) -> str``；测试可注入

    Raises
    ------
    FontToolError
        目标文件冲突、下载失败或 SHA256 校验失败。
    """
    target_dir = font_dir or user_font_dir()
    download = downloader or _download
    fetch_license = license_fetcher or _fetch_text
    manifest = load_manifest()
    outcomes: list[InstallOutcome] = []
    changed = False
    try:
        if not plan:
            target_dir.mkdir(parents=True, exist_ok=True)
        for spec in specs:
            destination = target_dir / spec.filename
            license_file = license_path(target_dir, spec)
            if destination.is_file() and file_matches(destination, spec):
                changed = _record_installed(manifest, spec, destination, license_file) or changed
                outcomes.append(InstallOutcome(spec.font_id, spec.family, destination, "skipped"))
                continue
            if destination.exists() and not force:
                raise FontToolError(f"目标文件已存在且内容不一致: {destination}；如需覆盖请加 --force")
            if plan:
                outcomes.append(InstallOutcome(spec.font_id, spec.family, destination, "planned"))
                continue
            partial = destination.with_name(destination.name + ".part")
            try:
                download(spec.url, partial)
                _verify_font(partial, spec)
                partial.replace(destination)
            except Exception:
                with contextlib.suppress(OSError):
                    partial.unlink(missing_ok=True)
                raise
            _save_license(target_dir, spec, fetch_license)
            changed = _record_installed(manifest, spec, destination, license_file) or changed
            outcomes.append(InstallOutcome(spec.font_id, spec.family, destination, "installed"))
    finally:
        if changed and not plan:
            save_manifest(manifest)

    entries = [(outcome.family, outcome.path) for outcome in outcomes if outcome.state == "installed"]
    cache_message = ""
    if entries:
        register_installed_fonts(entries)
        cache_message = refresh_font_cache(target_dir)
    return InstallReport(tuple(outcomes), cache_message)


def remove_fonts(
    ids: Sequence[str] | None = None,
    *,
    all_managed: bool = False,
    font_dir: Path | None = None,
    plan: bool = False,
) -> RemoveReport:
    """移除已安装字体与许可证文件，并更新清单。

    Parameters
    ----------
    ids:
        待移除字体 ID；支持逗号分隔
    all_managed:
        移除清单中的全部字体（忽略 ``ids``）
    font_dir:
        自定义字体目录；清单缺失时据此定位默认文件名
    plan:
        仅打印计划，不删除文件、不写清单

    Raises
    ------
    FontToolError
        未指定字体、ID 未知，或写清单失败。
    """
    manifest = load_manifest()
    target_dir = font_dir or user_font_dir()
    font_ids = list(manifest) if all_managed else _normalize_remove_ids(ids)
    outcomes: list[RemoveOutcome] = []
    changed = False
    removed_entries: list[tuple[str, Path]] = []
    for font_id in font_ids:
        entry = manifest.get(font_id)
        if entry is not None:
            path = Path(entry.path)
            license_file = Path(entry.license_path)
            family = entry.family
        else:
            spec = FONTS.get(font_id)
            if spec is None:
                raise FontToolError(f"未知字体: {font_id}；可用: {', '.join(FONTS)}")
            path = target_dir / spec.filename
            license_file = license_path(target_dir, spec)
            family = spec.family
        exists = path.exists() or license_file.exists()
        if not exists:
            if entry is not None:
                manifest.pop(font_id, None)
                changed = True
            outcomes.append(RemoveOutcome(font_id, path, "missing"))
            continue
        if plan:
            outcomes.append(RemoveOutcome(font_id, path, "planned"))
            continue
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        with contextlib.suppress(FileNotFoundError):
            license_file.unlink()
        manifest.pop(font_id, None)
        changed = True
        outcomes.append(RemoveOutcome(font_id, path, "removed"))
        removed_entries.append((family, path))
    if changed and not plan:
        save_manifest(manifest)
    cache_message = ""
    if removed_entries:
        unregister_installed_fonts(removed_entries)
        cache_message = refresh_font_cache(target_dir)
    return RemoveReport(tuple(outcomes), cache_message)


def _normalize_remove_ids(ids: Sequence[str] | None) -> list[str]:
    """展开并去重待移除 ID；为空时抛业务错误。"""
    if not ids:
        raise FontToolError("请用 --font 指定字体，或 --all-managed 移除全部已安装字体")
    result: list[str] = []
    seen: set[str] = set()
    for raw in ids:
        for part in raw.split(","):
            key = part.strip().lower()
            if key and key not in seen:
                seen.add(key)
                result.append(key)
    if not result:
        raise FontToolError("未指定有效字体")
    return result


# ---------------------------------------------------------------------- #
# 状态查询与格式化
# ---------------------------------------------------------------------- #
def collect_status(
    specs: Sequence[FontSpec],
    *,
    font_dir: Path | None = None,
    manifest: Mapping[str, InstalledFont] | None = None,
) -> tuple[FontStatus, ...]:
    """计算每款字体的安装状态（已安装 / 未登记 / 校验不一致 / 文件缺失 / 未安装）。"""
    target_dir = font_dir or user_font_dir()
    entries = load_manifest() if manifest is None else manifest
    statuses: list[FontStatus] = []
    for spec in specs:
        entry = entries.get(spec.font_id)
        path = Path(entry.path) if entry is not None else target_dir / spec.filename
        if not path.is_file():
            state = "文件缺失" if entry is not None else "未安装"
        elif not file_matches(path, spec):
            state = "校验不一致"
        elif entry is None:
            state = "已安装（未登记）"
        else:
            state = "已安装"
        statuses.append(FontStatus(spec=spec, path=path, managed=entry is not None, state=state))
    return tuple(statuses)


def _display_width(text: str) -> int:
    """按终端显示宽度计数（中文等宽字符按 2 列计）。"""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _pad(text: str, width: int) -> str:
    """按终端显示宽度左对齐填充。"""
    return text + " " * max(0, width - _display_width(text))


def format_font_table(specs: Sequence[FontSpec]) -> str:
    """格式化可安装字体表格（ID / 字体 / 类型 / 大小 / 许可证）。"""
    header = _pad("ID", 16) + _pad("字体", 18) + _pad("类型", 8) + _pad("大小", 12) + "许可证"
    lines = [header, "-" * _display_width(header)]
    for spec in specs:
        lines.append(
            _pad(spec.font_id, 16)
            + _pad(spec.label, 18)
            + _pad(KIND_LABELS.get(spec.kind, spec.kind), 8)
            + _pad(format_size(spec.size), 12)
            + spec.license_name
        )
    return "\n".join(lines)


def format_status_report(
    statuses: Sequence[FontStatus],
    *,
    target_dir: Path,
    manifest_file: Path,
    system: str | None = None,
) -> str:
    """格式化状态报告：平台、字体目录、清单路径与逐字体状态。"""
    lines = [
        f"平台: {system or sys.platform}",
        f"字体目录: {target_dir}",
        f"清单位置: {manifest_file}",
        "字体状态:",
    ]
    for status in statuses:
        lines.append(f"  [{status.state}] {status.spec.font_id}  {status.spec.label}  -> {status.path}")
    return "\n".join(lines)


def platform_hint(system: str | None = None) -> str:
    """返回平台相关的使用提示；无提示时返回空字符串。"""
    plat = system or sys.platform
    if plat.startswith("linux"):
        return (
            "提示：uv 独立构建的 Python 自带 Tk 未启用 Xft，安装 TTF 后窗口仍可能看不到新字体；"
            "请改用系统 Python（python3-tk，启用 Xft），或安装 xfonts-wqy 作为兜底。"
        )
    return ""


# ---------------------------------------------------------------------- #
# 系统集成
# ---------------------------------------------------------------------- #
def register_installed_fonts(entries: Sequence[tuple[str, Path]]) -> None:
    """把新装字体注册到当前会话（仅 Windows 有实际操作，其余平台空实现）。"""
    if not entries or not sys.platform.startswith("win"):
        return
    _register_windows_fonts(entries)  # pragma: no cover - 仅 Windows 分支


def unregister_installed_fonts(entries: Sequence[tuple[str, Path]]) -> None:
    """从当前会话注销字体（仅 Windows 有实际操作，其余平台空实现）。"""
    if not entries or not sys.platform.startswith("win"):
        return
    _unregister_windows_fonts(entries)  # pragma: no cover - 仅 Windows 分支


def _register_windows_fonts(entries: Sequence[tuple[str, Path]]) -> None:  # pragma: no cover - 仅 Windows
    """复制后的字体写入 HKCU 注册表并通知其他进程字体变化。"""
    import ctypes
    import winreg

    windll = cast(Any, ctypes).windll
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows NT\CurrentVersion\Fonts")
    try:
        for family, path in entries:
            windll.gdi32.AddFontResourceExW(str(path), 0, 0)
            winreg.SetValueEx(key, f"{family} (TrueType)", 0, winreg.REG_SZ, path.name)
    finally:
        winreg.CloseKey(key)
    windll.user32.SendMessageTimeoutW(0xFFFF, 0x001D, 0, 0, 0, 1000, None)


def _unregister_windows_fonts(entries: Sequence[tuple[str, Path]]) -> None:  # pragma: no cover - 仅 Windows
    """从 HKCU 注册表删除字体记录并通知其他进程字体变化。"""
    import ctypes
    import winreg

    windll = cast(Any, ctypes).windll
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows NT\CurrentVersion\Fonts")
    try:
        for family, path in entries:
            windll.gdi32.RemoveFontResourceExW(str(path), 0, 0)
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(key, f"{family} (TrueType)")
    finally:
        winreg.CloseKey(key)
    windll.user32.SendMessageTimeoutW(0xFFFF, 0x001D, 0, 0, 0, 1000, None)


def refresh_font_cache(
    target_dir: Path,
    *,
    which: Callable[[str], str | None] | None = None,
    runner: Callable[..., Any] | None = None,
) -> str:
    """刷新字体缓存并返回中文提示。

    Linux 调用 ``fc-cache -f``；Windows / macOS 只需通知系统，无额外输出。
    刷新失败只返回提示，不抛异常（字体文件已正确落盘）。
    """
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return ""
    finder = which or shutil.which
    execute = runner or subprocess.run
    executable = finder("fc-cache")
    if executable is None:
        return "未找到 fc-cache；请手动执行 fc-cache -f 或重新登录后生效。"
    try:
        # pyrefly 把 Windows 上的 sys.platform 视为字面量，前面的平台判断使其后的代码
        # 被判为不可达，从而跳过 None 收窄，故此处显式 cast（运行期已由上面的判断保证）。
        args = [cast(str, executable), "-f", str(target_dir)]
        execute(args, capture_output=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"刷新字体缓存失败: {exc}"
    return "已刷新系统字体缓存。"


# ---------------------------------------------------------------------- #
# CLI 子命令
# ---------------------------------------------------------------------- #
@fcmd.tool("fonttool", subcommand="list", help="列出可安装字体")
def list_fonts_cmd() -> None:
    """列出内置字体及其来源、体积与许可证。"""
    print(format_font_table(list(FONTS.values())))
    print()
    print(f"默认安装: {', '.join(DEFAULT_FONT_IDS)}")
    print("安装命令: fcmd fonttool install [--font ID ...] [--plan]")


@fcmd.tool("fonttool", subcommand="status", help="查看字体安装状态")
def status_cmd() -> None:
    """查看本机字体目录、安装清单与逐字体状态。"""
    target_dir = user_font_dir()
    statuses = collect_status(list(FONTS.values()), font_dir=target_dir)
    print(
        format_status_report(
            statuses,
            target_dir=target_dir,
            manifest_file=manifest_path(),
        )
    )
    hint = platform_hint()
    if hint:
        print()
        print(hint)


@fcmd.tool("fonttool", subcommand="install", help="下载并安装字体")
def install_cmd(
    font: list[str] | None = None,
    font_dir: Path | None = None,
    force: bool = False,
    plan: bool = False,
) -> None:
    """下载并安装推荐字体。

    Parameters
    ----------
    font:
        字体 ID，可传多个或逗号分隔；缺省安装 Cascadia Code + Noto Sans SC
    font_dir:
        自定义字体目录；缺省为当前用户字体目录
    force:
        目标文件已存在且校验不一致时覆盖
    plan:
        仅打印执行计划，不下载、不写入
    """
    try:
        specs = resolve_fonts(font)
        report = install_fonts(specs, font_dir=font_dir, force=force, plan=plan)
    except FontToolError as exc:
        _report_font_error(exc)
        return
    labels = {"installed": "已安装", "skipped": "已是最新", "planned": "计划安装"}
    for outcome in report.outcomes:
        print(f"[{labels.get(outcome.state, outcome.state)}] {outcome.family} -> {outcome.path}")
    if plan:
        print("以上为执行计划，未下载或写入任何文件。")
        return
    if report.cache_message:
        print(report.cache_message)
    hint = platform_hint()
    if hint:
        print(hint)


@fcmd.tool("fonttool", subcommand="remove", help="移除已安装字体")
def remove_cmd(
    font: list[str] | None = None,
    all_managed: bool = False,
    font_dir: Path | None = None,
    plan: bool = False,
) -> None:
    """移除已安装字体与随字体保存的许可证文件。

    Parameters
    ----------
    font:
        字体 ID，可传多个或逗号分隔
    all_managed:
        移除清单中的全部字体
    font_dir:
        自定义字体目录；缺省为当前用户字体目录
    plan:
        仅打印执行计划，不删除文件
    """
    try:
        report = remove_fonts(font, all_managed=all_managed, font_dir=font_dir, plan=plan)
    except FontToolError as exc:
        _report_font_error(exc)
        return
    labels = {"removed": "已移除", "planned": "计划移除", "missing": "文件不存在"}
    if not report.outcomes:
        print("没有需要移除的字体。")
        return
    for outcome in report.outcomes:
        print(f"[{labels.get(outcome.state, outcome.state)}] {outcome.font_id} -> {outcome.path}")
    if plan:
        print("以上为执行计划，未删除任何文件。")
        return
    if report.cache_message:
        print(report.cache_message)


@fcmd.main("fonttool")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
