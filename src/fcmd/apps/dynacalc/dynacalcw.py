"""dynacalcw - dynacalc 的 Tk 窗口（复用 fcmd.gui 主题）。

界面按「输入 → 生成 → 执行 → 结果」四段组织：选择参数表 / 模型描述 / 输出目录，
预览工况清单，触发 dry-run 或执行，查看作业状态、汇总与门控结论。

窗口类标 ``pragma: no cover``（CI 无显示环境）；设置读写与行格式化是纯函数，
由单元测试覆盖。
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.apps.dynacalc._gates import evaluate, format_gate_report
from fcmd.apps.dynacalc._mesh import scan_mesh_root
from fcmd.apps.dynacalc._model import load_models
from fcmd.apps.dynacalc._params import build_cases, load_params
from fcmd.apps.dynacalc._results import collect_results, format_text_report
from fcmd.apps.dynacalc._solver import config_for_manifest, load_jobs, run_jobs
from fcmd.apps.dynacalc._template import generate_decks, load_manifest, load_template
from fcmd.console import print_error
from fcmd.gui import window as _w

__all__ = [
    "DynacalcSettings",
    "DynacalcWindow",
    "case_rows",
    "config_path",
    "generation_summary",
    "job_rows",
    "load_settings",
    "main",
    "parse_codes",
    "save_settings",
]

__version__ = "0.1.0"

_DESCRIPTION = "dynacalc 的 Tk 窗口：选参数/模型、预览清单、生成 deck、dry-run、执行、查看作业与门控"
_POLL_MS = 80


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
    home = os.environ.get("FCMD_HOME") or str(Path.home() / ".fcmd")
    return Path(home) / "ui" / "dynacalcw.json"


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


class DynacalcWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖
    """dynacalc 主窗口。"""

    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self._settings = load_settings()
        self._results: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.root, self._theme = _w.create_root(
            title=f"dynacalc v{__version__} - 装药密度/尺寸/缺陷 → 爆轰传递",
            saved_geometry=self._settings.geometry,
            default_geometry="1180x780",
            min_width=980,
            min_height=640,
            on_close=self.close,
            theme=None,
        )
        _w.build_menu(self.root, on_close=self.close, on_about=self._about)
        self._vars: dict[str, Any] = {
            "params": tk.StringVar(value=self._settings.params),
            "model": tk.StringVar(value=self._settings.model),
            "out": tk.StringVar(value=self._settings.out),
            "template": tk.StringVar(value=self._settings.template),
            "solver": tk.StringVar(value=self._settings.solver),
            "mesh_root": tk.StringVar(value=self._settings.mesh_root),
            "mesh_level": tk.StringVar(value=self._settings.mesh_level),
            "ncpu": tk.StringVar(value=str(self._settings.ncpu)),
            "concurrency": tk.StringVar(value=str(self._settings.concurrency)),
            "timeout_s": tk.StringVar(value=str(self._settings.timeout_s)),
            "retries": tk.StringVar(value=str(self._settings.retries)),
            "fake": tk.BooleanVar(value=self._settings.fake),
            "only": tk.StringVar(value=""),
        }
        self._status = tk.StringVar(value="就绪：先选择参数文件。")
        self._build_form(ttk)
        self._build_tabs(ttk)
        self.root.after(_POLL_MS, self._poll)

    # ------------------------------------------------------------------ #
    # 布局
    # ------------------------------------------------------------------ #
    def _build_form(self, ttk: Any) -> None:
        space = self._theme.spacing
        form = ttk.Frame(self.root, padding=space.md)
        form.pack(fill="x")
        for column in (1, 3):
            form.columnconfigure(column, weight=1)
        rows = [
            ("参数文件", "params", self._choose_params),
            ("模型描述", "model", self._choose_model),
        ]
        for index, (label, key, command) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", padx=(0, space.sm))
            ttk.Entry(form, textvariable=self._vars[key]).grid(row=index, column=1, sticky="ew", pady=space.xxs)
            ttk.Button(form, text="选择…", command=command).grid(row=index, column=2, padx=space.sm)
        ttk.Label(form, text="输出目录").grid(row=0, column=3, sticky="w", padx=(space.md, space.sm))
        ttk.Entry(form, textvariable=self._vars["out"]).grid(row=0, column=4, sticky="ew", pady=space.xxs)
        ttk.Button(form, text="选择…", command=self._choose_out).grid(row=0, column=5, padx=space.sm)
        ttk.Label(form, text="模板").grid(row=1, column=3, sticky="w", padx=(space.md, space.sm))
        ttk.Entry(form, textvariable=self._vars["template"]).grid(row=1, column=4, sticky="ew", pady=space.xxs)
        ttk.Button(form, text="选择…", command=self._choose_template).grid(row=1, column=5, padx=space.sm)
        ttk.Label(form, text="网格根目录").grid(row=2, column=0, sticky="w", padx=(0, space.sm))
        ttk.Entry(form, textvariable=self._vars["mesh_root"]).grid(row=2, column=1, sticky="ew", pady=space.xxs)
        ttk.Button(form, text="选择…", command=self._choose_mesh_root).grid(row=2, column=2, padx=space.sm)
        ttk.Label(form, text="网格档位").grid(row=2, column=3, sticky="w", padx=(space.md, space.sm))
        ttk.Entry(form, textvariable=self._vars["mesh_level"]).grid(row=2, column=4, sticky="ew", pady=space.xxs)

        solver_row = ttk.Frame(form)
        solver_row.grid(row=3, column=0, columnspan=6, sticky="ew", pady=(space.xs, 0))
        ttk.Label(solver_row, text="求解器").pack(side="left")
        ttk.Entry(solver_row, textvariable=self._vars["solver"], width=34).pack(side="left", padx=space.sm)
        ttk.Button(solver_row, text="选择…", command=self._choose_solver).pack(side="left")
        for label, key, width in (
            ("核数", "ncpu", 5),
            ("并发", "concurrency", 5),
            ("超时(s)", "timeout_s", 7),
            ("重试", "retries", 4),
            ("仅工况", "only", 18),
        ):
            ttk.Label(solver_row, text=label).pack(side="left", padx=(space.md, space.xs))
            ttk.Entry(solver_row, textvariable=self._vars[key], width=width).pack(side="left")
        ttk.Checkbutton(solver_row, text="假求解器", variable=self._vars["fake"]).pack(side="left", padx=space.md)

        actions = ttk.Frame(self.root, padding=(self._theme.spacing.md, 0))
        actions.pack(fill="x")
        buttons = [
            ("预览清单", self._preview),
            ("生成 deck", self._generate),
            ("dry-run", self._plan_only),
            ("执行", self._run),
            ("刷新作业", self._refresh_jobs),
            ("汇总结果", self._report),
            ("门控检查", self._gates),
        ]
        for text, command in buttons:
            ttk.Button(actions, text=text, command=command).pack(side="left", padx=(0, self._theme.spacing.sm))

    def _build_tabs(self, ttk: Any) -> None:
        space = self._theme.spacing
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=space.md, pady=(space.sm, 0))
        self._notebook = notebook

        cases = ttk.Frame(notebook)
        self._case_tree = ttk.Treeview(cases, columns=("code", "group", "model", "tbd"), show="headings")
        for name, title, width in (
            ("code", "工况", 120),
            ("group", "分组", 120),
            ("model", "模型", 220),
            ("tbd", "TBD 数", 80),
        ):
            self._case_tree.heading(name, text=title)
            self._case_tree.column(name, width=width, anchor="w")
        self._case_tree.pack(fill="both", expand=True)
        notebook.add(cases, text="工况清单")

        jobs = ttk.Frame(notebook)
        self._job_tree = ttk.Treeview(
            jobs, columns=("code", "status", "attempts", "duration", "error"), show="headings"
        )
        for name, title, width in (
            ("code", "工况", 110),
            ("status", "状态", 100),
            ("attempts", "尝试", 60),
            ("duration", "耗时", 90),
            ("error", "错误", 420),
        ):
            self._job_tree.heading(name, text=title)
            self._job_tree.column(name, width=width, anchor="w")
        self._job_tree.pack(fill="both", expand=True)
        notebook.add(jobs, text="作业状态")

        log_frame = ttk.Frame(notebook)
        self._log = _w.create_log(log_frame, height=18)
        notebook.add(log_frame, text="日志 / 汇总")

        ttk.Label(self.root, textvariable=self._status, anchor="w").pack(
            fill="x", padx=space.md, pady=(space.xs, space.sm)
        )

    # ------------------------------------------------------------------ #
    # 选择文件
    # ------------------------------------------------------------------ #
    def _choose(self, key: str, title: str, pattern: str) -> None:
        from tkinter import filedialog

        chosen = filedialog.askopenfilename(
            title=title, filetypes=[("YAML", pattern), ("所有文件", "*.*")], parent=self.root
        )
        if chosen:
            self._vars[key].set(chosen)

    def _choose_params(self) -> None:
        self._choose("params", "选择参数文件", "*.yaml *.yml")

    def _choose_model(self) -> None:
        self._choose("model", "选择模型描述", "*.yaml *.yml")

    def _choose_template(self) -> None:
        self._choose("template", "选择 deck 模板", "*.k *.txt")

    def _choose_solver(self) -> None:
        self._choose("solver", "选择 LS-DYNA 可执行文件", "*.exe")

    def _choose_mesh_root(self) -> None:
        from tkinter import filedialog

        chosen = filedialog.askdirectory(title="选择网格根目录（meshes）", parent=self.root)
        if chosen:
            self._vars["mesh_root"].set(chosen)

    def _choose_out(self) -> None:
        from tkinter import filedialog

        chosen = filedialog.askdirectory(title="选择输出根目录", parent=self.root)
        if chosen:
            self._vars["out"].set(chosen)

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #
    def _paths(self) -> tuple[Path, Path, Path | None, list[Path], list[str], Path | None, str]:
        params = Path(self._vars["params"].get().strip())
        out = Path(self._vars["out"].get().strip())
        if not params.is_file():
            raise ValueError("请选择存在的参数文件")
        if not self._vars["out"].get().strip():
            raise ValueError("请选择输出根目录")
        template = self._vars["template"].get().strip()
        model = self._vars["model"].get().strip()
        mesh_root = self._vars["mesh_root"].get().strip()
        return (
            params,
            out,
            Path(template) if template else None,
            [Path(model)] if model else [],
            list(parse_codes(self._vars["only"].get())),
            Path(mesh_root) if mesh_root else None,
            self._vars["mesh_level"].get().strip(),
        )

    def _preview(self) -> None:
        try:
            params, _, _, _, only, _, _ = self._paths()
            table = load_params(params)
            cases = build_cases(table) if not only else [c for c in build_cases(table) if c.code in set(only)]
        except Exception as exc:
            self._fail(exc)
            return
        self._fill_cases(
            "preview",
            [(case.code, case.group, "-", str(len(case.tbd))) for case in cases],
        )
        self._status.set(f"预览：{len(cases)} 个工况（未落盘）")
        self._notebook.select(0)

    def _generate(self) -> None:
        def work() -> str:
            params, out, template, models, only, mesh_root, mesh_level = self._paths()
            table = load_params(params)
            model_set = load_models(models) if models else None
            text = load_template(template) if template is not None else None
            meshes = scan_mesh_root(mesh_root) if mesh_root is not None else None
            result = generate_decks(
                table,
                out,
                template=text,
                template_name=template.name if template is not None else None,
                only=only or None,
                models=model_set,
                meshes=meshes,
                mesh_level=mesh_level or None,
            )
            self._results.put(("cases", result.manifest))
            return generation_summary(result)

        self._submit(work, "生成")

    def _plan_only(self) -> None:
        self._run(dry_run=True)

    def _run(self, dry_run: bool = False) -> None:
        def work() -> str:
            out = Path(self._vars["out"].get().strip())
            manifest = load_manifest(out)
            overrides = {
                "solver_path": self._vars["solver"].get().strip() or None,
                "ncpu": _as_int(self._vars["ncpu"].get()),
                "concurrency": _as_int(self._vars["concurrency"].get()),
                "timeout_s": _as_int(self._vars["timeout_s"].get()),
                "retries": _as_int(self._vars["retries"].get()),
                "fake": True if self._vars["fake"].get() else None,
            }
            config = config_for_manifest(manifest, overrides)
            only = parse_codes(self._vars["only"].get())
            report = run_jobs(manifest, config, root=out, only=only or None, dry_run=dry_run)
            self._results.put(("jobs", out))
            head = "dry-run 计划" if dry_run else "执行"
            return f"{head}：{len(report.jobs)} 个作业，失败 {len(report.failed())}"

        self._submit(work, "dry-run" if dry_run else "执行")

    def _refresh_jobs(self) -> None:
        out = Path(self._vars["out"].get().strip())
        if not self._vars["out"].get().strip():
            self._fail(ValueError("请选择输出根目录"))
            return
        self._fill_jobs(out)
        self._notebook.select(1)

    def _report(self) -> None:
        def work() -> str:
            out = Path(self._vars["out"].get().strip())
            manifest = load_manifest(out)
            result_set = collect_results(out, manifest)
            return format_text_report(result_set)

        self._submit(work, "汇总")

    def _gates(self) -> None:
        def work() -> str:
            out = Path(self._vars["out"].get().strip())
            manifest = load_manifest(out)
            result_set = collect_results(out, manifest)
            return format_gate_report(evaluate(result_set, manifest.settings))

        self._submit(work, "门控", show_in_log=True)

    # ------------------------------------------------------------------ #
    # 后台执行与轮询
    # ------------------------------------------------------------------ #
    def _submit(self, work: Callable[[], str], label: str, *, show_in_log: bool = False) -> None:
        self._status.set(f"{label}中…")
        self._log_target = show_in_log

        def runner() -> None:
            try:
                self._results.put(("text", work()))
            except Exception as exc:
                self._results.put(("error", exc))

        threading.Thread(target=runner, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._results.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        self.root.after(_POLL_MS, self._poll)

    def _handle(self, kind: str, payload: Any) -> None:
        if kind == "error":
            self._fail(payload)
        elif kind == "cases":
            self._fill_cases("manifest", case_rows(payload))
            self._notebook.select(0)
        elif kind == "jobs":
            self._fill_jobs(payload)
            self._status.set("作业状态已刷新")
        else:
            text = str(payload)
            if getattr(self, "_log_target", False):
                _w.clear_log(self._log)
            _w.append_log(self._log, text)
            self._notebook.select(2)
            self._status.set("完成" if not text.startswith("[不通过]") else "存在不通过项")

    def _fill_cases(self, tag: str, rows: list[tuple[str, str, str, str]]) -> None:
        self._case_tree.delete(*self._case_tree.get_children())
        for row in rows:
            self._case_tree.insert("", "end", values=row)
        if tag == "manifest":
            self._status.set(f"清单：{len(rows)} 个工况")

    def _fill_jobs(self, root: Path) -> None:
        rows = job_rows(root)
        self._job_tree.delete(*self._job_tree.get_children())
        for row in rows:
            self._job_tree.insert("", "end", values=row)
        failed = sum(1 for row in rows if row[1] == "failed")
        self._status.set(f"作业 {len(rows)} 个，失败 {failed} 个")

    def _fail(self, exc: BaseException) -> None:
        from tkinter import messagebox

        self._status.set(f"失败：{exc}")
        messagebox.showerror("dynacalc", str(exc), parent=self.root)

    def _about(self) -> None:
        from tkinter import messagebox

        messagebox.showinfo(
            "关于 dynacalc",
            f"dynacalcw v{__version__}\n\n"
            "装药密度 / 尺寸 / 工艺缺陷 → 爆轰传递的参数化算例。\n"
            "只生成 deck、调度求解与汇总；不含任何真实配方或材料参数，未填项一律 TBD。",
            parent=self.root,
        )

    def close(self) -> None:
        try:
            self._settings.params = self._vars["params"].get()
            self._settings.model = self._vars["model"].get()
            self._settings.out = self._vars["out"].get()
            self._settings.template = self._vars["template"].get()
            self._settings.solver = self._vars["solver"].get()
            self._settings.ncpu = _as_int(self._vars["ncpu"].get()) or 4
            self._settings.concurrency = _as_int(self._vars["concurrency"].get()) or 1
            self._settings.timeout_s = _as_int(self._vars["timeout_s"].get()) or 3600
            self._settings.retries = _as_int(self._vars["retries"].get()) or 0
            self._settings.fake = bool(self._vars["fake"].get())
            self._settings.geometry = self.root.winfo_geometry()
            save_settings(self._settings)
        finally:
            self.root.destroy()


def _as_int(text: str) -> int | None:
    """文本 → 正整数；非法或非正返回 ``None``（由配置层回落默认值）。"""
    try:
        value = int(text.strip())
    except ValueError:
        return None
    return value if value > 0 else None


def run_gui() -> None:
    """启动 Tk 窗口；tkinter 缺失时由 :func:`fcmd.gui.window.run_window` 提示。"""
    _w.run_window(DynacalcWindow, " dynacalc 窗口")


@fcmd.tool("dynacalcw", help="打开 dynacalc 窗口（Tk）", description=_DESCRIPTION)
def launch_command() -> None:
    """打开 dynacalc 主窗口。"""
    run_gui()


@fcmd.main("dynacalcw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
