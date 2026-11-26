"""dynacalcw 的 Qt 主窗口与入口。

界面按「输入 → 生成 → 执行 → 结果」四段组织：选择参数表 / 模型描述 / 输出
目录，预览工况清单，触发 dry-run 或执行，查看作业状态、汇总与门控结论。
生成与求解都在后台线程运行，事件经信号桥回到 GUI 线程，界面不阻塞。

布局口径：路径一律整行展示并在下方用 :class:`ElidedLabel` 中间省略；动作行
按钮横向铺开；工况 / 作业两张表把「模型」「错误」列设为拉伸列以吃满宽度；
窗口缩小时由表格与日志页签吸收高度，不压扁表单。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from fcmd import __version__
from fcmd.apps.dynacalc._gates import evaluate, format_gate_report
from fcmd.apps.dynacalc._mesh import scan_mesh_root
from fcmd.apps.dynacalc._model import load_models
from fcmd.apps.dynacalc._params import build_cases, load_params
from fcmd.apps.dynacalc._results import collect_results, format_text_report
from fcmd.apps.dynacalc._solver import config_for_manifest, run_jobs
from fcmd.apps.dynacalc._template import generate_decks, load_manifest, load_template
from fcmd.apps.dynacalc.dynacalcw import (
    DynacalcSettings,
    case_rows,
    generation_summary,
    job_rows,
    load_settings,
    parse_codes,
    save_settings,
)
from fcmd.gui.app import run_window
from fcmd.gui.toolwindow import QtToolWindow
from fcmd.gui.widgets import (
    Card,
    FormGrid,
    PathField,
    button,
    make_table,
    make_table_item,
    messages,
    tab_widget,
)
from fcmd.gui.workers import EventBridge, submit

__all__ = ["DynacalcWindow", "run_qt"]

DEFAULT_SIZE = (1180, 780)

MIN_SIZE = (980, 640)


def _work_generate(  # noqa: PLR0913, PLR0917 - 输入在 GUI 线程一次性收集，逐项显式传入
    params: Path,
    out: Path,
    template: Path | None,
    models: list[Path],
    only: list[str],
    mesh_root: Path | None,
    mesh_level: str,
    bridge: EventBridge,
) -> None:
    """加载输入并生成 deck，结果与摘要经信号桥回传。"""
    try:
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
    except Exception as exc:  # 后台线程需统一回传给界面
        bridge.event.emit(("error", str(exc)))
        return
    bridge.event.emit(("cases", result.manifest))
    bridge.event.emit(("text", generation_summary(result), False))


def _work_run(  # noqa: PLR0913, PLR0917 - 求解参数在 GUI 线程一次性收集
    out: Path,
    solver: str,
    ncpu: int,
    concurrency: int,
    timeout_s: int,
    retries: int,
    fake: bool,
    only: list[str],
    dry_run: bool,
    bridge: EventBridge,
) -> None:
    """按输出目录执行或预演作业调度。"""
    try:
        manifest = load_manifest(out)
        overrides = {
            "solver_path": solver or None,
            "ncpu": ncpu,
            "concurrency": concurrency,
            "timeout_s": timeout_s,
            "retries": retries,
            "fake": True if fake else None,
        }
        config = config_for_manifest(manifest, overrides)
        report = run_jobs(manifest, config, root=out, only=only or None, dry_run=dry_run)
    except Exception as exc:  # 后台线程需统一回传给界面
        bridge.event.emit(("error", str(exc)))
        return
    head = "dry-run 计划" if dry_run else "执行"
    bridge.event.emit(("jobs", out))
    bridge.event.emit(("text", f"{head}：{len(report.jobs)} 个作业，失败 {len(report.failed())}", False))


def _work_report(out: Path, bridge: EventBridge) -> None:
    """汇总全部结果并回传文本报告。"""
    try:
        manifest = load_manifest(out)
        text = format_text_report(collect_results(out, manifest))
    except Exception as exc:  # 后台线程需统一回传给界面
        bridge.event.emit(("error", str(exc)))
        return
    bridge.event.emit(("text", text, False))


def _work_gates(out: Path, bridge: EventBridge) -> None:
    """执行工程门控检查并回传报告（日志页先清空）。"""
    try:
        manifest = load_manifest(out)
        text = format_gate_report(evaluate(collect_results(out, manifest), manifest.settings))
    except Exception as exc:  # 后台线程需统一回传给界面
        bridge.event.emit(("error", str(exc)))
        return
    bridge.event.emit(("text", text, True))


class DynacalcWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """dynacalc 主窗口。"""

    log_hint = "汇总生成、门控检查与求解报告；复制、清空、导出见日志工具条。"

    def format_log_line(self, message: str) -> str:
        """任务日志前缀：``[HH:MM:SS]``。"""
        return f"[{time.strftime('%H:%M:%S')}] {message}"

    def __init__(self) -> None:
        self._settings = load_settings()
        self._bridge: EventBridge | None = None
        super().__init__(
            title=f"dynacalc v{__version__} - 装药密度/尺寸/缺陷 → 爆轰传递",
            config_name="dynacalcw",
            default_size=DEFAULT_SIZE,
            min_size=MIN_SIZE,
            persist=self._persist,
            status="就绪：先选择参数文件。",
        )
        self._build()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        """表单 → 动作行 → 页签（工况 / 作业）；日志统一在「帮助 → 任务日志…」。"""
        self.add(self._build_form())
        self.add_layout(self._build_actions())
        self._tabs = tab_widget(self)
        self._case_table = make_table(
            ["工况", "分组", "模型", "TBD 数"],
            widths=[120, 120, 220, 80],
            stretch_columns=(2,),
            parent=self._tabs,
        )
        self._tabs.addTab(self._case_table, "工况清单")
        self._job_table = make_table(
            ["工况", "状态", "尝试", "耗时", "错误"],
            widths=[110, 100, 60, 90, 420],
            stretch_columns=(4,),
            parent=self._tabs,
        )
        self._tabs.addTab(self._job_table, "作业状态")
        self.add(self._tabs, 1)

    def _build_form(self) -> Card:
        """七行路径 + 一行求解参数；长路径各有省略提示。"""
        card = Card("输入", self)
        grid = FormGrid(card)
        self._params = PathField(
            value=self._settings.params,
            placeholder="参数表 YAML",
            pattern="*.yaml *.yml",
            title="选择参数文件",
            directory=False,
            browse_text="选择…",
            parent=card,
        )
        grid.add_row("参数文件", self._params)
        self._model = PathField(
            value=self._settings.model,
            placeholder="模型描述 YAML（可选）",
            pattern="*.yaml *.yml",
            title="选择模型描述",
            directory=False,
            browse_text="选择…",
            parent=card,
        )
        grid.add_row("模型描述", self._model)
        self._out = PathField(
            value=self._settings.out,
            placeholder="输出根目录",
            title="选择输出根目录",
            browse_text="选择…",
            parent=card,
        )
        grid.add_row("输出目录", self._out)
        self._template = PathField(
            value=self._settings.template,
            placeholder="deck 模板（可选）",
            pattern="*.k *.txt",
            title="选择 deck 模板",
            directory=False,
            browse_text="选择…",
            parent=card,
        )
        grid.add_row("模板", self._template)
        self._mesh_root = PathField(
            value=self._settings.mesh_root,
            placeholder="网格根目录（可选）",
            title="选择网格根目录（meshes）",
            browse_text="选择…",
            parent=card,
        )
        grid.add_row("网格根目录", self._mesh_root)
        self._mesh_level = QLineEdit(self._settings.mesh_level, card)
        self._mesh_level.setPlaceholderText("网格档位（可选，如 coarse）")
        grid.add_row("网格档位", self._mesh_level)
        self._solver = PathField(
            value=self._settings.solver,
            placeholder="LS-DYNA 可执行文件（可选）",
            pattern="*.exe",
            title="选择 LS-DYNA 可执行文件",
            directory=False,
            browse_text="选择…",
            parent=card,
        )
        grid.add_row("求解器", self._solver)
        grid.add_row("求解参数", self._build_numbers(card))
        card.body.addWidget(grid)
        return card

    def _build_numbers(self, parent: QWidget) -> QWidget:
        """核数 / 并发 / 超时 / 重试 / 仅工况 / 假求解器。"""
        row = QWidget(parent)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._ncpu = self._spin(layout, parent, "核数", self._settings.ncpu, 1, 256)
        self._concurrency = self._spin(layout, parent, "并发", self._settings.concurrency, 1, 64)
        self._timeout = self._spin(layout, parent, "超时(s)", self._settings.timeout_s, 1, 86400)
        self._retries = self._spin(layout, parent, "重试", self._settings.retries, 0, 10)
        layout.addWidget(QLabel("仅工况", parent))
        self._only = QLineEdit(parent)
        self._only.setPlaceholderText("M001, M002（留空为全部）")
        self._only.setClearButtonEnabled(True)
        layout.addWidget(self._only, 1)
        self._fake = QCheckBox("假求解器", parent)
        self._fake.setChecked(self._settings.fake)
        layout.addWidget(self._fake)
        return row

    def _spin(  # noqa: PLR0913, PLR0917 - 控件构造参数
        self,
        layout: QHBoxLayout,
        parent: QWidget,
        label: str,
        value: int,
        minimum: int,
        maximum: int,
    ) -> QSpinBox:
        layout.addWidget(QLabel(label, parent))
        spin = QSpinBox(parent)
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        layout.addWidget(spin)
        return spin

    def _build_actions(self) -> QHBoxLayout:
        """七个动作按钮横向铺开。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for text, slot, kind in (
            ("预览清单", self._preview, ""),
            ("生成 deck", self._generate, ""),
            ("dry-run", self._plan_only, ""),
            ("执行", self._run, "primary"),
            ("刷新作业", self._refresh_jobs, ""),
            ("汇总结果", self._report, ""),
            ("门控检查", self._gates, ""),
        ):
            widget = button(text, kind=kind)
            widget.clicked.connect(slot)
            row.addWidget(widget)
        row.addStretch(1)
        return row

    # ------------------------------------------------------------------ #
    # 输入与动作
    # ------------------------------------------------------------------ #
    def _collect_paths(self) -> tuple[Path, Path, Path | None, list[Path], list[str], Path | None, str]:
        """校验并收集路径参数（GUI 线程内调用，异常由动作层转换成提示）。"""
        params_text = self._params.value()
        out_text = self._out.value()
        if not params_text or not Path(params_text).is_file():
            raise ValueError("请选择存在的参数文件")
        if not out_text:
            raise ValueError("请选择输出根目录")
        template = self._template.value()
        model = self._model.value()
        mesh_root = self._mesh_root.value()
        return (
            Path(params_text),
            Path(out_text),
            Path(template) if template else None,
            [Path(model)] if model else [],
            list(parse_codes(self._only.text())),
            Path(mesh_root) if mesh_root else None,
            self._mesh_level.text().strip(),
        )

    def _require_out(self) -> Path:
        text = self._out.value()
        if not text:
            raise ValueError("请选择输出根目录")
        return Path(text)

    def _bridge_for_task(self) -> EventBridge:
        """每个任务用独立信号桥，任务结束即可随引用释放。"""
        bridge = EventBridge(self)
        bridge.event.connect(self._on_event)
        self._bridge = bridge
        return bridge

    def _preview(self) -> None:
        try:
            params, _out, _template, _models, only, _mesh_root, _level = self._collect_paths()
            table = load_params(params)
            cases = build_cases(table) if not only else [c for c in build_cases(table) if c.code in set(only)]
        except Exception as exc:
            self._fail(exc)
            return
        self._fill_cases([(case.code, case.group, "-", str(len(case.tbd))) for case in cases])
        self.set_status(f"预览：{len(cases)} 个工况（未落盘）")
        self._tabs.setCurrentIndex(0)

    def _generate(self) -> None:
        try:
            params, out, template, models, only, mesh_root, mesh_level = self._collect_paths()
        except ValueError as exc:
            self._fail(exc)
            return
        self.set_status("生成中…")
        submit(
            _work_generate,
            params,
            out,
            template,
            models,
            only,
            mesh_root,
            mesh_level,
            self._bridge_for_task(),
        )

    def _plan_only(self) -> None:
        self._run(dry_run=True)

    def _run(self, dry_run: bool = False) -> None:
        try:
            out = self._require_out()
        except ValueError as exc:
            self._fail(exc)
            return
        self.set_status("dry-run 中…" if dry_run else "执行中…")
        submit(
            _work_run,
            out,
            self._solver.value(),
            self._ncpu.value(),
            self._concurrency.value(),
            self._timeout.value(),
            self._retries.value(),
            self._fake.isChecked(),
            list(parse_codes(self._only.text())),
            dry_run,
            self._bridge_for_task(),
        )

    def _refresh_jobs(self) -> None:
        try:
            out = self._require_out()
        except ValueError as exc:
            self._fail(exc)
            return
        self._fill_jobs(out)
        self._tabs.setCurrentIndex(1)

    def _report(self) -> None:
        try:
            out = self._require_out()
        except ValueError as exc:
            self._fail(exc)
            return
        self.set_status("汇总中…")
        submit(_work_report, out, self._bridge_for_task())

    def _gates(self) -> None:
        try:
            out = self._require_out()
        except ValueError as exc:
            self._fail(exc)
            return
        self.set_status("门控检查中…")
        submit(_work_gates, out, self._bridge_for_task())

    # ------------------------------------------------------------------ #
    # 事件与表格
    # ------------------------------------------------------------------ #
    def _on_event(self, event: tuple[Any, ...]) -> None:
        """消费后台事件：错误、清单、作业或文本报告。"""
        kind = event[0]
        if kind == "error":
            self._fail(event[1])
        elif kind == "cases":
            self._fill_cases(case_rows(event[1]))
            self._tabs.setCurrentIndex(0)
            self.set_status(f"清单：{len(event[1].cases)} 个工况")
        elif kind == "jobs":
            self._fill_jobs(event[1])
            self.set_status("作业状态已刷新")
        else:
            _, text, show_in_log = event
            if show_in_log:
                self.clear_log()
            self.log(str(text))
            self.set_status("存在不通过项" if str(text).startswith("[不通过]") else "完成")

    def _fill_cases(self, rows: list[tuple[str, str, str, str]]) -> None:
        """重填工况清单表。"""
        self._case_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for column, value in enumerate(row):
                self._case_table.setItem(index, column, make_table_item(value))

    def _fill_jobs(self, root: Path) -> None:
        """读取 jobs.json 并重填作业状态表。"""
        rows = job_rows(root)
        self._job_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for column, value in enumerate(row):
                self._job_table.setItem(index, column, make_table_item(value))
        failed = sum(1 for row in rows if row[1] == "failed")
        self.set_status(f"作业 {len(rows)} 个，失败 {failed} 个")

    def _fail(self, exc: object) -> None:
        self.set_status(f"失败：{exc}")
        messages.error(self, "dynacalc", str(exc))

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #
    def _persist(self, geometry: str) -> None:
        """关闭时写回路径、求解参数、假求解器开关与 geometry。"""
        save_settings(
            DynacalcSettings(
                params=self._params.value(),
                model=self._model.value(),
                out=self._out.value(),
                template=self._template.value(),
                solver=self._solver.value(),
                ncpu=self._ncpu.value(),
                concurrency=self._concurrency.value(),
                timeout_s=self._timeout.value(),
                retries=self._retries.value(),
                fake=self._fake.isChecked(),
                geometry=geometry,
            )
        )


def run_qt() -> None:
    """创建并运行 Qt 主窗口。"""
    run_window(DynacalcWindow, app_name="dynacalcw")
