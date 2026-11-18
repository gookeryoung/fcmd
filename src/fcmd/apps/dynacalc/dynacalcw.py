"""dynacalcw - dynacalc 的 Qt 窗口入口与设置。

界面按「输入 → 生成 → 执行 → 结果」四段组织：选择参数表 / 模型描述 / 输出目录，
预览工况清单，触发 dry-run 或执行，查看作业状态、汇总与门控结论。窗口实现在
:mod:`fcmd.apps.dynacalc._dynacalcw_qt`，本模块保留设置读写与行格式化纯函数。

设置保存在 ``${FCMD_HOME:-~/.fcmd}/ui/dynacalcw.json``。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.apps.dynacalc._solver import load_jobs
from fcmd.console import print_error
from fcmd.infras.system import fcmd_home

__all__ = [
    "DynacalcSettings",
    "case_rows",
    "config_path",
    "generation_summary",
    "job_rows",
    "load_settings",
    "main",
    "parse_codes",
    "save_settings",
]

_DESCRIPTION = "dynacalc 的 Qt 窗口：选参数/模型、预览清单、生成 deck、dry-run、执行、查看作业与门控"


@dataclass
class DynacalcSettings:
    """窗口记忆的设置（全部为可序列化标量）。"""

    params: str = ""
    model: str = ""
    out: str = ""
    template: str = ""
    solver: str = ""
    mesh_root: str = ""
    mesh_level: str = ""
    ncpu: int = 4
    concurrency: int = 1
    timeout_s: int = 3600
    retries: int = 0
    fake: bool = True
    geometry: str | None = None


def config_path() -> Path:
    """``${FCMD_HOME:-~/.fcmd}/ui/dynacalcw.json``。"""
    return fcmd_home() / "ui" / "dynacalcw.json"


_TEXT_FIELDS = ("params", "model", "out", "template", "solver", "mesh_root", "mesh_level")
_INT_FIELDS = {"ncpu": 1, "concurrency": 1, "timeout_s": 1, "retries": 0}


def load_settings(path: Path | None = None) -> DynacalcSettings:
    """读取设置；文件缺失、损坏或含非法类型时整体回到默认值（窗口仍可打开）。"""
    target = path or config_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DynacalcSettings()
    return _settings_from(raw) or DynacalcSettings()


def _settings_from(raw: Any) -> DynacalcSettings | None:
    """校验设置映射；任一字段类型非法返回 ``None``（整体回落默认值）。"""
    if not isinstance(raw, dict):
        return None
    if any(name in raw and not isinstance(raw[name], str) for name in _TEXT_FIELDS):
        return None
    if any(
        name in raw and (isinstance(raw[name], bool) or not isinstance(raw[name], int) or raw[name] < minimum)
        for name, minimum in _INT_FIELDS.items()
    ):
        return None
    if "fake" in raw and not isinstance(raw["fake"], bool):
        return None
    if "geometry" in raw and raw["geometry"] is not None and not isinstance(raw["geometry"], str):
        return None
    settings = DynacalcSettings()
    for name in _TEXT_FIELDS:
        if name in raw:
            setattr(settings, name, raw[name])
    for name in _INT_FIELDS:
        if name in raw:
            setattr(settings, name, raw[name])
    if "fake" in raw:
        settings.fake = raw["fake"]
    if raw.get("geometry") is not None:
        settings.geometry = raw["geometry"]
    return settings


def save_settings(settings: DynacalcSettings, path: Path | None = None) -> None:
    """原子写入设置；失败只报错不抛出。"""
    target = path or config_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(asdict(settings), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(text, encoding="utf-8", newline="\n")
        temporary.replace(target)
    except OSError as exc:
        print_error(f"保存 dynacalcw 设置失败：{exc}")


def parse_codes(text: str) -> tuple[str, ...]:
    """把 ``M001, M002`` / ``M001 M002`` 解析成去重编码列表。"""
    return tuple(dict.fromkeys(part for part in re.split(r"[,\s]+", text.strip()) if part))


def case_rows(manifest: Any) -> list[tuple[str, str, str, str]]:
    """生成清单 → 表格行：编码 / 分组 / 模型 / TBD 数。"""
    return [
        (
            item.code,
            item.group,
            f"{item.model_id}@{item.model_digest[:8]}" if item.model_id else "-",
            str(len(item.tbd)),
        )
        for item in manifest.cases
    ]


def job_rows(root: Path) -> list[tuple[str, str, str, str, str]]:
    """``jobs.json`` → 表格行：编码 / 状态 / 尝试 / 耗时 / 错误。"""
    try:
        jobs = load_jobs(root)
    except Exception as exc:
        return [("-", "读取失败", "-", "-", str(exc))]
    return [
        (job.code, job.status, str(job.attempts), f"{job.duration_s:.3f}s", job.error)
        for job in (jobs[code] for code in sorted(jobs))
    ]


def generation_summary(result: Any) -> str:
    """生成结果的短摘要。"""
    manifest = result.manifest
    lines = [
        f"已生成 {manifest.total} 个工况：主矩阵 "
        f"{sum(1 for item in manifest.cases if item.group == 'main')} 组，"
        f"补充组 {sum(1 for item in manifest.cases if item.group != 'main')} 组",
        f"TBD 工况 {manifest.tbd_cases} 个 / 字段 {manifest.tbd_fields} 处",
    ]
    if manifest.models:
        lines.append(f"模型绑定 {manifest.model_bound} 个工况，回落矩阵 {manifest.model_fallback} 个工况")
    return "\n".join(lines)


# ---------------------------------------------------------------------- #
# 入口
# ---------------------------------------------------------------------- #
def run_gui() -> None:
    """启动 Qt 窗口（高 DPI 与主题由 ``gui.qt.app`` 统一处理）。"""
    try:
        from fcmd.apps.dynacalc._dynacalcw_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 安装完整时不会触发
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc

    run_qt()


@fcmd.tool("dynacalcw", help="打开 dynacalc 窗口（Qt）", description=_DESCRIPTION)
def launch_command() -> None:
    """打开 dynacalc 主窗口。"""
    run_gui()


@fcmd.main("dynacalcw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
