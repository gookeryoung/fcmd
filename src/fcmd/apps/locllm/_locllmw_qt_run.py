"""locllmw（Qt）运行页：启动参数、计划预览、服务启停与模型选择。

页面把 Tk 版运行页的表单与动作原样搬到 Qt：参数校验、调优策略联动、
显存余量检查与启动计划文本都放在模块级纯函数里（``parse_int`` /
``memory_guard`` / ``plan_lines``），GUI 类只负责取值与展示，便于单测。

后台动作（启动 / 停止）通过 :func:`fcmd.gui.qt.workers.submit` 执行，
按钮状态由共享状态的 ``busyChanged`` 统一驱动，避免任务期间重复点击。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.locllm import _llmtune, llmbase, llmenv, llmmodel, llmserve
from fcmd.apps.locllm._llmgguf import detect_precision
from fcmd.apps.locllm._locllmw_monitor import _busy_advice, _fit_advice, _probe_web_ui
from fcmd.apps.locllm._locllmw_ui import _HINT_PLACEHOLDER, service_name_from_model
from fcmd.apps.locllm.llmbase import (
    DEFAULT_API_KEY,
    DEFAULT_HOST,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_PROFILE,
    DEFAULT_THREADS,
    KV_SCALE,
    PROFILES,
    LaunchPlan,
    ProfileName,
    ServeError,
    ServeState,
    StopResult,
    format_command,
    http_base_url,
    human_size,
)
from fcmd.gui.qt.widgets import Card, Collapsible, FormGrid, HintLabel, button
from fcmd.gui.qt.workers import submit

if TYPE_CHECKING:
    from fcmd.apps.locllm._locllmw_qt_state import LocllmState

__all__ = [
    "RunPage",
    "build_plan_from",
    "default_service_name",
    "memory_guard",
    "parse_int",
    "parse_optional_int",
    "plan_lines",
]


# ---------------------------------------------------------------------- #
# 纯逻辑：参数解析 / 资源检查 / 计划文本
# ---------------------------------------------------------------------- #
def parse_int(text: str, label: str) -> int:
    """把输入框文本解析为整数；非法值抛 :class:`ServeError`。"""
    raw = text.strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise ServeError(f"{label} 必须是整数: {raw!r}") from exc


def parse_optional_int(text: str, label: str) -> int | None:
    """留空返回 ``None``，否则按 :func:`parse_int` 解析。"""
    if not text.strip():
        return None
    return parse_int(text, label)


def memory_guard(plan: LaunchPlan, usage: tuple[int, int] | None, available_memory: int | None = None) -> str:
    """启动前检查显存 / 内存余量，返回「通过」日志行，不足时抛 :class:`ServeError`。

    区分两类不足：整卡显存都放不下（硬件限制，清理显存也无解）与整卡够用但
    当前余量不足（需先停止其他服务）。``available_memory`` 省略时用 psutil 采样。
    """
    estimate = llmmodel.plan_vram(plan)
    if usage is not None:
        used, total = usage
        if estimate > total * 1024 * 1024:
            raise ServeError(
                f"整卡显存不足（硬件限制），清理显存也无法运行：预计需要 {human_size(estimate)}，"
                f"整卡仅 {human_size(total * 1024 * 1024)}。"
                "请改用更小量化或更小参数量的模型，或减小上下文"
            )
        available = max(total - used, 0) * 1024 * 1024
        if estimate > available:
            raise ServeError(
                f"当前可用显存不足（整卡 {total} MiB 够用，可能被其他服务占用）："
                f"预计需要 {human_size(estimate)}，当前可用 {human_size(available)}。"
                "请先停止其他服务 / 实例，或改用更小档位、减小上下文"
            )
        return f"资源检查通过：预计占用 {human_size(estimate)} / 显存可用 {human_size(available)}"
    if available_memory is None:
        import psutil

        available_memory = int(psutil.virtual_memory().available)
    if estimate > available_memory:
        raise ServeError(
            f"可用内存不足：预计需要 {human_size(estimate)}，当前可用 {human_size(available_memory)}。"
            "请改用更小档位、减小上下文，或先停止其他服务"
        )
    return f"资源检查通过：预计占用 {human_size(estimate)} / 可用内存 {human_size(available_memory)}"


def plan_lines(  # noqa: PLR0913 - 计划摘要由多段来源拼成，显式传入避免隐式全局
    plan: LaunchPlan,
    *,
    estimate: int,
    tags: tuple[str, ...] = (),
    precision_detail: str = "",
    advice: str = "",
    total: llmmodel.VramFit | None = None,
    free: llmmodel.VramFit | None = None,
    base_url: str = "",
) -> list[str]:
    """生成「启动计划」预览文本（纯函数，便于单测与复制命令）。"""
    tuning = plan.tuning
    lines = [
        "启动计划:",
        f"  服务名: {plan.name}",
        f"  引擎: {plan.engine.tag} · {plan.engine.exe}",
        f"  模型: {plan.model}",
        f"  档位: {plan.profile.name} / {plan.profile.quant} / kv {plan.effective_kv_type} / "
        f"ctx {plan.context} / ub {plan.effective_ubatch}",
        f"  调优: {tuning.label} / b{tuning.batch} / np{plan.effective_parallel} / "
        f"th{plan.effective_threads_http or '-'} / cr{tuning.cache_reuse} / fa {tuning.flash_attn}",
        f"  说明: {tuning.detail}",
        f"  监听: {base_url or http_base_url(plan.host, plan.port)}",
    ]
    if tags:
        lines.append(f"  标签: {' · '.join(tags)}")
    if precision_detail:
        lines.append(f"  精度: {precision_detail}")
    if advice:
        lines.append(f"  建议: {advice}")
    lines.append(f"  显存估算: {human_size(estimate)}")
    lines.append("  命令: " + format_command(plan.argv()))
    if total is not None and not total.ok:
        lines.append(
            f"  警告: 估算 {human_size(estimate)} 超过整卡显存 {human_size(total.available)}，无法运行；"
            f"建议{_fit_advice(total, plan.context, plan.effective_kv_type)}"
        )
    elif free is not None and not free.ok:
        lines.append(
            f"  警告: 估算 {human_size(estimate)} 超过当前可用显存 {human_size(free.available)}，可能 OOM；"
            f"建议{_busy_advice(free, plan.context, plan.effective_kv_type)}"
        )
    return lines


def default_service_name() -> str:
    """唯一运行中的服务优先作为默认服务名，避免与实际服务不一致。"""
    try:
        states = llmenv.list_states()
    except ServeError:
        return DEFAULT_NAME
    alive = [state.name for state in states if llmserve.process_alive(state.pid, state.create_time)]
    return alive[0] if len(alive) == 1 else DEFAULT_NAME


def build_plan_from(values: dict[str, Any]) -> LaunchPlan:
    """按表单取值构造启动计划；参数非法时抛 :class:`ServeError`。

    ``values`` 由页面按控件名组装（见 :meth:`RunPage._form_values`），集中
    在一处做类型转换，便于单测直接构造计划而不依赖控件。
    """
    name = str(values.get("name", "")).strip()
    if not name:
        raise ServeError("服务名不能为空")
    profile = str(values.get("profile", DEFAULT_PROFILE))
    return llmserve.make_plan(
        llmbase.validate_name(name),
        cast("ProfileName", profile),
        model=values.get("model"),
        port=parse_int(str(values.get("port", DEFAULT_PORT)), "端口"),
        host=str(values.get("host", "")).strip() or DEFAULT_HOST,
        ctx=parse_optional_int(str(values.get("ctx", "")), "上下文"),
        kv_type=str(values.get("kv", "")).strip() or None,
        ubatch=parse_optional_int(str(values.get("ubatch", "")), "ubatch"),
        parallel=parse_optional_int(str(values.get("parallel", "")), "并发槽"),
        threads_http=parse_optional_int(str(values.get("threads_http", "")), "HTTP 线程数"),
        alias=str(values.get("alias", "")).strip() or None,
        api_key=str(values.get("api_key", "")).strip() or DEFAULT_API_KEY,
        threads=parse_int(str(values.get("threads", DEFAULT_THREADS)), "线程数"),
        tune=str(values.get("tune", "auto")),
        gpu_mib=values.get("gpu_mib"),
    )


class RunPage(QWidget):  # pragma: no cover - Qt 控件
    """运行页：参数表单 + 模型列表 + 计划预览 + 启停动作。"""

    def __init__(self, state: LocllmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._syncing_tune = False
        self._tune_auto = {"kv": False, "ubatch": False, "parallel": False, "threads_http": False}
        self._plan_command = ""
        self._health_target = "settings"
        self._build()
        self._wire()
        self.on_models_changed()
        self.refresh_service_names()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        health_row = QHBoxLayout()
        self.health_label = HintLabel("正在检测运行环境…")
        self.health_button = button("去处理")
        self.health_button.setEnabled(False)
        self.health_button.clicked.connect(lambda: self.state.navigate(self._health_target))
        health_row.addWidget(self.health_label, 1)
        health_row.addWidget(self.health_button)
        layout.addLayout(health_row)

        self.params_card = Card("启动参数")
        form = FormGrid(self.params_card)
        self.name_combo = QComboBox(self.params_card)
        self.name_combo.setEditable(True)
        self.name_combo.setCurrentText(default_service_name())
        self.profile_combo = QComboBox(self.params_card)
        self.profile_combo.addItems(list(PROFILES))
        self.profile_combo.setCurrentText(DEFAULT_PROFILE)
        form.add_pair("服务名", self.name_combo, "档位", self.profile_combo)
        self.host_edit = QLineEdit(DEFAULT_HOST, self.params_card)
        self.port_edit = QLineEdit(str(DEFAULT_PORT), self.params_card)
        form.add_pair("监听地址", self.host_edit, "端口", self.port_edit)
        self.threads_edit = QLineEdit(str(DEFAULT_THREADS), self.params_card)
        self.tune_combo = QComboBox(self.params_card)
        self.tune_combo.addItems(list(_llmtune.TUNE_LABELS.values()))
        form.add_pair("线程数", self.threads_edit, "调优策略", self.tune_combo)
        self.params_card.body.addWidget(form)

        self.advanced = Collapsible("高级参数（留空回落档位默认）", expanded=False, parent=self.params_card)
        adv_form = FormGrid(self.advanced.content)
        self.ctx_edit = QLineEdit("", self.advanced.content)
        self.kv_combo = QComboBox(self.advanced.content)
        self.kv_combo.addItems(["", *KV_SCALE])
        adv_form.add_pair("上下文", self.ctx_edit, "KV", self.kv_combo)
        self.ubatch_edit = QLineEdit("", self.advanced.content)
        self.alias_edit = QLineEdit("", self.advanced.content)
        adv_form.add_pair("ubatch", self.ubatch_edit, "模型别名（留空自动）", self.alias_edit)
        self.parallel_edit = QLineEdit("", self.advanced.content)
        self.threads_http_edit = QLineEdit("", self.advanced.content)
        adv_form.add_pair("并发槽（-np）", self.parallel_edit, "HTTP 线程", self.threads_http_edit)
        self.api_key_edit = QLineEdit(DEFAULT_API_KEY, self.advanced.content)
        self.wait_edit = QLineEdit("180", self.advanced.content)
        adv_form.add_pair("API Key", self.api_key_edit, "等待就绪（秒）", self.wait_edit)
        self.advanced.body.addWidget(adv_form)
        self.params_card.body.addWidget(self.advanced)
        layout.addWidget(self.params_card)

        self.models_card = Card("运行模型（模型目录中的 GGUF）")
        from fcmd.apps.locllm._locllmw_qt_models import ModelTree

        self.tree = ModelTree(self.state, self.models_card)
        self.tree.set_fit_resolver(self._fit_text)
        self.models_card.body.addWidget(self.tree)
        pick_row = QHBoxLayout()
        self.model_label = QLabel("未选择（按档位自动在模型目录中查找）", self.models_card)
        self.model_label.setObjectName("Hint")
        self.model_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        pick_row.addWidget(self.model_label, 1)
        self.browse_button = button("浏览…")
        self.clear_button = button("清除选择")
        pick_row.addWidget(self.browse_button)
        pick_row.addWidget(self.clear_button)
        self.models_card.body.addLayout(pick_row)
        layout.addWidget(self.models_card, 1)

        self.plan_card = Card("启动计划")
        self.plan_view = QPlainTextEdit(self.plan_card)
        self.plan_view.setObjectName("Log")
        self.plan_view.setReadOnly(True)
        self.plan_view.setFixedHeight(120)
        self.plan_view.setPlainText("尚未生成：点击「预览计划」查看命令、显存估算与建议。")
        copy_row = QHBoxLayout()
        copy_row.addStretch(1)
        self.copy_plan_button = button("复制命令", kind="ghost")
        copy_row.addWidget(self.copy_plan_button)
        self.plan_card.body.addWidget(self.plan_view)
        self.plan_card.body.addLayout(copy_row)
        layout.addWidget(self.plan_card)

        actions = QHBoxLayout()
        self.preview_button = button("预览计划")
        self.start_button = button("启动服务", kind="primary")
        self.stop_selected_button = button("停止选中服务")
        self.stop_all_button = button("停止全部服务", kind="danger")
        self.status_button = button("刷新进程状态", kind="ghost")
        for widget in (
            self.preview_button,
            self.start_button,
            self.stop_selected_button,
            self.stop_all_button,
            self.status_button,
        ):
            actions.addWidget(widget)
        actions.addStretch(1)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(160)
        self.progress.setVisible(False)
        actions.addWidget(self.progress)
        layout.addLayout(actions)

        view_actions = QHBoxLayout()
        self.browser_button = button("打开浏览器", kind="ghost")
        self.logs_button = button("查看日志", kind="ghost")
        view_actions.addWidget(self.browser_button)
        view_actions.addWidget(self.logs_button)
        view_actions.addStretch(1)
        layout.addLayout(view_actions)

    def _wire(self) -> None:
        self.profile_combo.currentTextChanged.connect(lambda _text: self._on_profile_changed())
        self.tune_combo.currentTextChanged.connect(lambda _text: self._on_tune_changed())
        self.name_combo.currentTextChanged.connect(self._on_service_name_changed)
        self.kv_combo.currentTextChanged.connect(lambda _text: self._mark_manual("kv"))
        self.ubatch_edit.textChanged.connect(lambda _text: self._mark_manual("ubatch"))
        self.parallel_edit.textChanged.connect(lambda _text: self._mark_manual("parallel"))
        self.threads_http_edit.textChanged.connect(lambda _text: self._mark_manual("threads_http"))
        self.preview_button.clicked.connect(self.preview)
        self.start_button.clicked.connect(self.start)
        self.stop_selected_button.clicked.connect(self.stop_selected)
        self.stop_all_button.clicked.connect(self.stop_all)
        self.status_button.clicked.connect(self.refresh_status)
        self.browser_button.clicked.connect(self.open_browser)
        self.logs_button.clicked.connect(lambda: self.state.navigate("monitor"))
        self.copy_plan_button.clicked.connect(self.copy_plan_command)
        self.browse_button.clicked.connect(self._browse)
        self.clear_button.clicked.connect(self._clear_selection)
        self.tree.modelSelected.connect(self._on_model_picked)
        self.state.modelsChanged.connect(self._on_models_changed_signal)
        self.state.servicesChanged.connect(self._on_services_changed)
        self.state.selectionChanged.connect(self._on_selection_changed)
        self.state.busyChanged.connect(self._on_busy_changed)

    # ------------------------------------------------------------------ #
    # 参数与调优
    # ------------------------------------------------------------------ #
    def _workflow(self) -> dict[str, Any]:
        return {
            "name": self.name_combo.currentText(),
            "profile": self.profile_combo.currentText(),
            "host": self.host_edit.text(),
            "port": self.port_edit.text(),
            "threads": self.threads_edit.text(),
            "tune": _llmtune.LABEL_TO_TUNE.get(self.tune_combo.currentText().strip(), "auto"),
            "ctx": self.ctx_edit.text(),
            "kv": self.kv_combo.currentText(),
            "ubatch": self.ubatch_edit.text(),
            "alias": self.alias_edit.text(),
            "parallel": self.parallel_edit.text(),
            "threads_http": self.threads_http_edit.text(),
            "api_key": self.api_key_edit.text(),
            "model": self.state.selected_model,
            "gpu_mib": self.state.gpu_mib,
        }

    def build_plan(self) -> LaunchPlan:
        """按当前表单构造启动计划。"""
        return build_plan_from(self._workflow())

    def profile_context(self) -> tuple[str, int, str]:
        """当前表单下的档位名、上下文与 KV 类型（输入无效时回退档位默认值）。"""
        name = self.profile_combo.currentText().strip() or DEFAULT_PROFILE
        profile = PROFILES.get(name, PROFILES[DEFAULT_PROFILE])
        try:
            context = parse_optional_int(self.ctx_edit.text(), "上下文") or profile.context
        except ServeError:
            context = profile.context
        kv_type = self.kv_combo.currentText().strip() or profile.kv_type
        return name, context, kv_type if kv_type in KV_SCALE else profile.kv_type

    def tuning(self) -> llmbase.Tuning:
        """按当前档位 / 上下文 / GPU 解析调优策略。"""
        _, context, _ = self.profile_context()
        size = self._selected_size()
        tune = _llmtune.LABEL_TO_TUNE.get(self.tune_combo.currentText().strip(), "auto")
        return _llmtune.resolve(tune, context=context, model_bytes=size, gpu_mib=self.state.gpu_mib)

    def fit_estimate(self, size: int) -> tuple[llmmodel.VramFit | None, llmmodel.VramFit | None]:
        """返回（整卡能力, 当前可用）两套显存估算；未检测到 GPU 时均为 ``None``。"""
        if self.state.gpu_mib is None:
            return None, None
        _, context, kv_type = self.profile_context()
        tuning = self.tuning()
        kv_type = tuning.kv_type or kv_type
        total = llmmodel.fit_vram(size, self.state.gpu_mib * 1024 * 1024, context, kv_type)
        free = None
        if self.state.gpu_free_mib is not None:
            free = llmmodel.fit_vram(size, self.state.gpu_free_mib * 1024 * 1024, context, kv_type)
        return total, free

    def _selected_size(self) -> int:
        path = self.state.selected_model
        if path is None:
            return 0
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def _fit_text(self, entry: Any) -> str:
        """模型行的硬件状态文本（运行中优先）。"""
        from fcmd.apps.locllm._locllmw_qt_models import fit_state_for

        total, free = self.fit_estimate(entry.size)
        return fit_state_for(running=self.state.is_running(entry.path), total=total, free=free)

    def _mark_manual(self, key: str) -> None:
        if not self._syncing_tune:
            self._tune_auto[key] = False

    def _apply_tuning(self) -> None:
        """把调优策略值同步到高级参数，保留用户手填的非策略值。"""
        tuning = self.tuning()
        synced = _llmtune.sync_advanced_fields(
            tuning,
            kv_value=self.kv_combo.currentText().strip(),
            ubatch_value=self.ubatch_edit.text().strip(),
            parallel_value=self.parallel_edit.text().strip(),
            threads_http_value=self.threads_http_edit.text().strip(),
            kv_auto=self._tune_auto["kv"],
            ubatch_auto=self._tune_auto["ubatch"],
            parallel_auto=self._tune_auto["parallel"],
            threads_http_auto=self._tune_auto["threads_http"],
        )
        self._tune_auto = {
            "kv": synced.kv_auto,
            "ubatch": synced.ubatch_auto,
            "parallel": synced.parallel_auto,
            "threads_http": synced.threads_http_auto,
        }
        self._syncing_tune = True
        try:
            self.kv_combo.setCurrentText(synced.kv_value)
            self.ubatch_edit.setText(synced.ubatch_value)
            self.parallel_edit.setText(synced.parallel_value)
            self.threads_http_edit.setText(synced.threads_http_value)
        finally:
            self._syncing_tune = False

    def effective_tuning_text(self, tuning: llmbase.Tuning) -> str:
        """提示区展示的生效高级参数（输入框 > 策略 > 档位）。"""
        _, _, profile_kv = self.profile_context()
        profile = PROFILES.get(self.profile_combo.currentText().strip())
        kv_type = self.kv_combo.currentText().strip() or tuning.kv_type or profile_kv
        ubatch = self.ubatch_edit.text().strip() or str(tuning.ubatch or (profile.ubatch if profile else ""))
        parallel = self.parallel_edit.text().strip() or str(tuning.parallel)
        threads_http = self.threads_http_edit.text().strip() or str(tuning.threads_http or "-")
        return (
            f"生效: b{tuning.batch} / ub {ubatch} / np {parallel} / th {threads_http} / "
            f"cr {tuning.cache_reuse} / kv {kv_type} / fa {tuning.flash_attn}"
        )

    def _on_profile_changed(self) -> None:
        """切换档位后重算模型列表的显存适配标注与提示。"""
        self._refresh_fit()
        path = self.state.selected_model
        if path is not None:
            self.show_model_hint(path)
        else:
            self.tree.set_hint(self._basis_hint())

    def _on_tune_changed(self) -> None:
        """切换调优策略后同步高级参数，并刷新硬件适配列与说明。"""
        self._apply_tuning()
        self.tree.refresh()
        path = self.state.selected_model
        if path is not None:
            self.show_model_hint(path)
            return
        tuning = self.tuning()
        self.tree.set_hint(f"调优: {tuning.label} · {tuning.detail}\n{self.effective_tuning_text(tuning)}")

    def _refresh_fit(self) -> None:
        self.tree.set_fit_resolver(self._fit_text)

    def _basis_hint(self) -> str:
        """未选中模型时展示硬件评估口径。"""
        available = self.state.gpu_free_mib if self.state.gpu_free_mib is not None else self.state.gpu_mib
        if available is None:
            return "未检测到可用 NVIDIA GPU，无法评估显存适配（CPU 推理会很慢）"
        _, context, kv_type = self.profile_context()
        tuning = self.tuning()
        kv_type = tuning.kv_type or kv_type
        total = f"{self.state.gpu_mib} MiB" if self.state.gpu_mib is not None else "-"
        return (
            f"硬件评估口径：整卡 {total}，当前可用 {available} MiB，上下文 {context}，KV {kv_type}，"
            f"调优 {tuning.label}；红色=硬件不足（清理显存也放不下），橙色=需释放显存（整卡够用），"
            "选择模型可查看具体建议"
        )

    # ------------------------------------------------------------------ #
    # 模型选择与提示
    # ------------------------------------------------------------------ #
    def refresh_service_names(self) -> None:
        """按最新状态文件刷新服务名下拉选项（保留当前输入）。"""
        current = self.name_combo.currentText()
        try:
            names = [state.name for state in llmenv.list_states()]
        except ServeError:
            names = []
        self.name_combo.blockSignals(True)
        self.name_combo.clear()
        self.name_combo.addItems(names)
        self.name_combo.setCurrentText(current or default_service_name())
        self.name_combo.blockSignals(False)

    def _on_service_name_changed(self, name: str) -> None:
        """选中服务名后同步定位其模型，便于停止已加载的模型。"""
        state = self._load_state_safe(name.strip())
        if state is None:
            return
        path = Path(state.model)
        self.state.select_model(path, update_name=False)
        self.tree.select_path(path)

    @staticmethod
    def _load_state_safe(name: str) -> ServeState | None:
        if not name:
            return None
        try:
            return llmenv.load_state(name, missing_ok=True)
        except ServeError:
            return None

    def _on_model_picked(self, path: Path) -> None:
        self.state.select_model(path, update_name=True)

    def _on_selection_changed(self, path: object, update_name: bool) -> None:
        if path is None:
            self.model_label.setText(_HINT_PLACEHOLDER)
            self.tree.set_hint(self._basis_hint())
            return
        selected = Path(path)  # type: ignore[arg-type]
        self.model_label.setText(str(selected))
        if update_name:
            self.name_combo.setCurrentText(service_name_from_model(selected))
        self.tree.select_path(selected)
        self.show_model_hint(selected)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 GGUF 模型", "", "GGUF 文件 (*.gguf);;所有文件 (*.*)")
        if path:
            self.state.select_model(Path(path).resolve())

    def _clear_selection(self) -> None:
        self.tree.tree.clearSelection()
        self.state.selected_model = None
        self.state.selectionChanged.emit(None, False)

    def _precision_detail(self, path: Path) -> str:
        precision = self.state.precisions.get(llmenv.path_key(path))
        if precision is not None:
            return precision.detail
        try:
            return detect_precision(path).detail
        except OSError:
            return "未知"

    def show_model_hint(self, path: Path) -> None:
        """展示推荐档位、最佳实践、精度与显存适配建议。"""
        tags = llmmodel.detect_model_tags(path.name)
        advice = llmmodel.model_advice(tags)
        try:
            port = parse_int(self.port_edit.text(), "端口")
        except ServeError:
            port = DEFAULT_PORT
        base = http_base_url(self.host_edit.text().strip() or DEFAULT_HOST, port)
        alias = self.alias_edit.text().strip() or path.stem
        tuning = self.tuning()
        lines = [
            f"标签: {' · '.join(tags) or '未识别'} | {advice}",
            f"精度: {self._precision_detail(path)}",
            f"接入: {base}/v1，model={alias}",
            f"调优: {tuning.label} · {tuning.detail}",
            self.effective_tuning_text(tuning),
        ]
        note = self._hardware_note(path)
        if note:
            lines.append(note)
        self.tree.set_hint("\n".join(lines))
        self.state.log(f"已选择模型: {path}")
        self.state.log(f"  标签: {' · '.join(tags) or '未识别'}")
        self.state.log(f"  建议: {advice}")

    def _hardware_note(self, path: Path) -> str:
        """生成硬件适配说明，区分「硬件不足」与「需释放显存」两类情况。"""
        if self.state.is_running(path):
            return "硬件: 该模型已在运行中的服务里加载"
        if self.state.gpu_mib is None:
            return "硬件: 未检测到可用 NVIDIA GPU，只能 CPU 推理（速度很慢，显存适配无法判断）"
        size = self._selected_size() if self.state.selected_model == path else self._size_of(path)
        total, free = self.fit_estimate(size)
        if total is None:
            return ""
        name, context, kv_type = self.profile_context()
        tuning = self.tuning()
        kv_type = tuning.kv_type or kv_type
        detail = f"{name} 档（ctx {context} / kv {kv_type} / {tuning.label}）"
        if not total.ok:
            return (
                f"硬件不足（已标红）: {detail}预计需 {human_size(total.needed)}，整卡显存仅 "
                f"{human_size(total.available)}，清理显存也无法运行；建议{_fit_advice(total, context, kv_type)}"
            )
        if free is not None and not free.ok:
            return (
                f"需释放显存（已标橙）: {detail}预计需 {human_size(total.needed)}，整卡 "
                f"{human_size(total.available)} 够用，但当前可用仅 {human_size(free.available)}；"
                f"建议{_busy_advice(free, context, kv_type)}"
            )
        available = free.available if free is not None else total.available
        return f"硬件: {detail}预计占 {human_size(total.needed)} / 可用显存 {human_size(available)}"

    @staticmethod
    def _size_of(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #
    def preview(self) -> None:
        """生成启动计划并展示命令、显存估算与建议。"""
        try:
            plan = self.build_plan()
        except ServeError as exc:
            self._error("参数错误", str(exc))
            return
        estimate = llmmodel.plan_vram(plan)
        tags = llmmodel.detect_model_tags(plan.model.name)
        total, free = self.fit_estimate(self._size_of(plan.model))
        lines = plan_lines(
            plan,
            estimate=estimate,
            tags=tags,
            precision_detail=self._precision_detail(plan.model),
            advice=llmmodel.model_advice(tags),
            total=total,
            free=free,
        )
        self._set_plan(lines)
        self.state.status(f"启动计划已生成：{plan.name} · {plan.profile.name} · {http_base_url(plan.host, plan.port)}")

    def _set_plan(self, lines: list[str]) -> None:
        """把计划写入预览区并同步日志（记录命令行便于复制）。"""
        self.plan_view.setPlainText("\n".join(lines))
        self._plan_command = ""
        for line in lines:
            self.state.log(line)
            if line.startswith("  命令: "):
                self._plan_command = line[len("  命令: ") :]

    def copy_plan_command(self) -> None:
        """复制当前预览的启动命令，便于在终端复现。"""
        if not self._plan_command:
            self.state.status("先点「预览计划」生成命令")
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._plan_command)
        self.state.status("已复制启动命令")

    def start(self) -> None:
        """校验参数与资源后，后台启动服务。"""
        if self.state.busy:
            self.state.log("已有任务正在进行，请等待完成")
            return
        try:
            plan = self.build_plan()
            wait_s = parse_int(self.wait_edit.text(), "等待就绪秒数")
            if wait_s <= 0:
                raise ServeError(f"等待就绪秒数必须为正整数: {wait_s}")
        except ServeError as exc:
            self._error("参数错误", str(exc))
            return
        from fcmd.apps.locllm._locllmw_qt_state import gpu_usage

        try:
            note = memory_guard(plan, gpu_usage())
        except ServeError as exc:
            self.state.log("已取消启动：" + str(exc))
            self._error("资源不足", str(exc))
            return
        self.state.log(note)
        self.state.log(f"正在启动 {plan.name}，等待 /health（最多 {wait_s}s）…")
        self.state.set_busy(True)
        submit(
            llmserve.start_server,
            plan,
            wait_s=float(wait_s),
            on_done=self._on_started,
            on_error=self._on_start_failed,
            on_finished=lambda: self.state.set_busy(False),
        )

    def _on_started(self, state: ServeState) -> None:
        self.state.log(f"服务已就绪: {state.base_url}（OpenAI: {state.base_url}/v1）")
        self.state.log(f"  模型: {state.model}")
        self.state.log(f"  档位: {state.profile} / ctx {state.context}")
        self.state.log(f"  日志: {state.log}")
        self.state.status(f"{state.name} · 运行中 · {state.base_url} · pid {state.pid}")
        self.name_combo.setCurrentText(state.name)
        self.state.refresh_services()
        self.state.refresh_models(self._port_value())

    def _on_start_failed(self, message: str) -> None:
        self.state.log(f"启动失败: {message}")
        self._error("启动失败", message)
        self.state.refresh_services()
        self.state.refresh_models(self._port_value())

    def _port_value(self) -> int | None:
        try:
            return parse_int(self.port_edit.text(), "端口")
        except ServeError:
            return None

    def stop_all(self) -> None:
        """停止所有已登记的服务；有多台时先确认。"""
        if self.state.busy:
            self.state.log("已有任务正在进行，请等待完成")
            return
        try:
            names = tuple(state.name for state in llmenv.list_states())
        except ServeError as exc:
            self._error("停止失败", str(exc))
            return
        if not names:
            self.state.status("无已登记服务")
            self.state.log("没有已登记的服务，无需停止")
            self.state.refresh_services()
            return
        answer = QMessageBox.question(
            self,
            "停止全部服务",
            f"将停止 {len(names)} 个已登记的服务：\n{'、'.join(names)}\n\n确定继续？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.state.log(f"正在停止 {len(names)} 个服务…")
        self.state.set_busy(True)
        submit(
            self._stop_worker,
            names,
            on_done=self._on_stopped,
            on_error=lambda message: self._error("停止失败", message),
            on_finished=lambda: self.state.set_busy(False),
        )

    def stop_selected(self) -> None:
        """停止当前服务名对应的服务，不影响其他已登记服务。"""
        if self.state.busy:
            self.state.log("已有任务正在进行，请等待完成")
            return
        name = self.name_combo.currentText().strip()
        if not name:
            self._error("参数错误", "服务名不能为空")
            return
        state = self._load_state_safe(name)
        if state is None:
            self.state.status(f"{name} · 未启动")
            self.state.log(f"服务 {name} 未启动，无需停止")
            return
        alive = llmserve.process_alive(state.pid, state.create_time)
        action = "停止服务" if alive else "服务已退出，清理残留状态"
        answer = QMessageBox.question(self, "停止选中服务", f"{action}：{name}？\n其他服务不受影响。")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.state.set_busy(True)
        submit(
            self._stop_worker,
            (name,),
            on_done=self._on_stopped,
            on_error=lambda message: self._error("停止失败", message),
            on_finished=lambda: self.state.set_busy(False),
        )

    @staticmethod
    def _stop_worker(names: tuple[str, ...]) -> tuple[list[StopResult], list[str]]:
        """后台线程：依次停止服务，收集成功结果与失败原因。"""
        results: list[StopResult] = []
        failures: list[str] = []
        for name in names:
            try:
                results.append(llmserve.stop_server(name))
            except ServeError as exc:
                failures.append(f"{name}: {exc}")
        return results, failures

    def _on_stopped(self, payload: tuple[list[StopResult], list[str]]) -> None:
        results, failures = payload
        stopped = [result.state.name for result in results if result.was_running]
        cleaned = [result.state.name for result in results if not result.was_running]
        for name in stopped:
            self.state.log(f"已停止服务 {name}")
        if cleaned:
            self.state.log(f"服务未在运行，已清理残留状态: {', '.join(cleaned)}")
        for failure in failures:
            self.state.log(f"停止失败: {failure}")
        if failures:
            self.state.status(f"停止完成 · {len(stopped)} 个已停止 · {len(failures)} 个失败")
            self._error("停止失败", "\n".join(failures))
        elif stopped:
            self.state.status(f"已停止 {len(stopped)} 个服务")
        else:
            self.state.status("无运行中的服务（已清理残留状态）")
        self.state.refresh_services()
        self.refresh_service_names()
        self.state.refresh_models(self._port_value())

    def refresh_status(self) -> None:
        """读取状态文件并刷新状态 / 日志（不改变正在进行的任务）。"""
        self.state.refresh_services()
        self.refresh_service_names()
        name = self.name_combo.currentText().strip()
        if not name:
            self._error("参数错误", "服务名不能为空")
            return
        state = self._load_state_safe(name)
        if state is None:
            self.state.status(f"{name} · 未启动")
            self.state.log(f"服务 {name} 未启动（尚无状态文件，持续显示系统负载）")
            return
        alive = llmserve.process_alive(state.pid, state.create_time)
        self.state.status(f"{state.name} · {'运行中' if alive else '已退出'} · {state.base_url} · pid {state.pid}")
        self.state.log(f"服务 {state.name}: {'运行中' if alive else '已退出'}")
        self.state.log(f"  地址: {state.base_url}")
        self.state.log(f"  模型: {state.model}")
        self.state.log(f"  档位: {state.profile} / ctx {state.context}")
        self.state.log(f"  启动时间: {state.started_at}")
        self.state.log(f"  日志: {state.log}")

    def open_browser(self) -> None:
        """在默认浏览器中打开服务地址（优先运行中服务的实际地址）。"""
        import webbrowser

        name = self.name_combo.currentText().strip()
        state = self._load_state_safe(name) if name else None
        engine = state.engine if state is not None else "（未登记）"
        if state is not None and llmserve.process_alive(state.pid, state.create_time):
            url = state.base_url
            api_key = state.api_key
        else:
            try:
                url = http_base_url(
                    self.host_edit.text().strip() or DEFAULT_HOST, parse_int(self.port_edit.text(), "端口")
                )
            except ServeError as exc:
                self._error("参数错误", str(exc))
                return
            api_key = self.api_key_edit.text().strip() or DEFAULT_API_KEY
        status = _probe_web_ui(url, api_key)
        if status == 404:
            message = (
                f"当前服务的引擎未内置 Web UI（GET / 返回 404）: {engine}\n"
                "LM Studio 版 llama.cpp 会移除 Web UI；请切换到带 Web UI 的官方构建后重启服务：\n"
                "  fcmd llmengine use <tag>（如 b12212）\n"
                f"也可直接用 OpenAI 兼容客户端访问 {url}/v1"
            )
            self.state.log(message)
            self._error("无 Web UI", message)
            return
        if status is None:
            message = f"服务未就绪，无法打开 Web UI: {url}"
            self.state.log(message)
            self._error("打开失败", message)
            return
        if status != 200:
            message = f"服务返回 HTTP {status}，Web UI 不可用: {url}"
            self.state.log(message)
            self._error("打开失败", message)
            return
        if webbrowser.open(url):
            self.state.log(f"已在浏览器中打开: {url}")
        else:
            self.state.log(f"无法打开浏览器，请手动访问: {url}")
            self._error("打开失败", f"无法打开浏览器，请手动访问: {url}")

    def show_logs(self) -> None:
        """切到监测页并附上日志尾部。"""
        self.state.navigate("monitor")
        name = self.name_combo.currentText().strip()
        try:
            content = llmserve.read_log(name, 50)
        except ServeError as exc:
            self._error("日志不可用", str(exc))
            return
        self.state.log(f"日志尾部（{name}）:")
        self.state.log(content)

    # ------------------------------------------------------------------ #
    # 共享状态回调
    # ------------------------------------------------------------------ #
    def _on_models_changed_signal(self, _outcome: object) -> None:
        """``modelsChanged`` 槽：忽略扫描结果，直接刷新列表（结果已在共享状态里）。"""
        self.on_models_changed()

    def on_models_changed(self) -> None:
        """扫描完成后刷新模型列表与服务名（保持当前选择不变）。"""
        self.tree.refresh()
        self.refresh_service_names()
        if self.state.selected_model is not None:
            self.tree.select_path(self.state.selected_model)

    def _on_services_changed(self, _states: list[ServeState]) -> None:
        """运行中服务变化：刷新服务名下拉与模型行的「运行中」标注。"""
        self.refresh_service_names()
        self.tree.refresh()

    def _on_busy_changed(self, busy: bool) -> None:
        for widget in (
            self.preview_button,
            self.start_button,
            self.stop_selected_button,
            self.stop_all_button,
            self.status_button,
            self.browse_button,
            self.clear_button,
        ):
            widget.setEnabled(not busy)
        self.progress.setVisible(busy)

    def update_health(self, issue: str | None, engine_missing: bool, port_busy: bool) -> None:
        """把最近一次扫描结果汇总为一条状态；异常时提供跳转入口。"""
        target = "settings"
        parts: list[str] = []
        if engine_missing:
            parts.append("引擎未安装（设置 → 引擎）")
        if issue:
            parts.append(issue)
            if issue.startswith("模型目录中未找到"):
                target = "models"
        if self.state.gpu_mib is None:
            parts.append("未检测到 NVIDIA GPU，显存适配不可用")
        if port_busy:
            parts.append(f"默认端口 {self.port_edit.text()} 已被占用")
            target = "run"
        if parts:
            self._health_target = target
            self.health_label.setText("注意：" + "；".join(parts))
            label = {"settings": "去设置", "models": "去模型页", "run": "去运行页"}[target]
            self.health_button.setText(label)
            self.health_button.setEnabled(True)
        else:
            self.health_label.setText("环境检查通过：引擎、模型目录、GPU 与默认端口均可用。")
            self.health_button.setText("去处理")
            self.health_button.setEnabled(False)

    def apply_recommendation(self, recommendation: Any) -> None:
        """把推荐行的模型与启动配置带入运行页。"""
        self.profile_combo.setCurrentText(recommendation.profile)
        self.tune_combo.setCurrentText(_llmtune.TUNE_LABELS["auto"])
        self.ctx_edit.setText(str(recommendation.context))
        self._on_tune_changed()
        self.kv_combo.setCurrentText(recommendation.kv_type)
        self.state.select_model(recommendation.model.path, update_name=True)
        self._on_profile_changed()
        self.state.navigate("run")
        self.state.status(f"已带入推荐配置：{recommendation.model.path.name} · {recommendation.config_text}")

    # ------------------------------------------------------------------ #
    # 杂项
    # ------------------------------------------------------------------ #
    def _error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def update_service_and_fit(self) -> None:
        """外部（启动 / 停止 / 显存采样）变化后刷新适配列与服务名。"""
        self.tree.refresh()
        self.refresh_service_names()

    def set_available_models_hint(self, text: str) -> None:
        """外部（模型页扫描问题）直接更新提示。"""
        self.tree.set_hint(text)
