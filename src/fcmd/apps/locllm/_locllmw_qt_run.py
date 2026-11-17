"""locllmw（Qt）运行页：启动参数、计划预览、服务启停与模型选择。

运行页的表单与动作迁移自原窗口：参数校验、调优策略联动、
显存余量检查与启动计划文本都放在模块级纯函数里（``parse_int`` /
``memory_guard`` / ``plan_lines``），GUI 类只负责取值与展示，便于单测。

后台动作（启动 / 停止）通过 :func:`fcmd.gui.qt.workers.submit` 执行，
按钮状态由共享状态的 ``busyChanged`` 统一驱动，避免任务期间重复点击。
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.locllm import _llmtune, _locllmw_recommend, llmbase, llmenv, llmmodel, llmserve
from fcmd.apps.locllm._locllmw_monitor import _busy_advice, _fit_advice, _probe_web_ui
from fcmd.apps.locllm._locllmw_ui import service_name_from_model
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
    ModelEntry,
    ProfileName,
    ServeError,
    ServeState,
    StopResult,
    format_command,
    http_base_url,
    human_size,
)
from fcmd.gui.qt.widgets import Card, Collapsible, FormGrid, HintLabel, PageScroll, button
from fcmd.gui.qt.workers import submit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fcmd.apps.locllm._locllmw_qt_state import LocllmState

__all__ = [
    "RunPage",
    "build_plan_from",
    "default_service_name",
    "memory_guard",
    "model_combo_text",
    "parse_custom_args",
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


def parse_custom_args(text: str) -> tuple[str, ...]:
    """按 shell 规则拆分自定义启动参数；空串返回空元组。"""
    return tuple(shlex.split(text.strip())) if text.strip() else ()


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


def pick_model(
    selected: Path | None,
    models: Sequence[ModelEntry],
    quant: str,
) -> Path | None:
    """选择启动用的模型文件：显式选择优先，否则按档位量化匹配、再退回最大文件。

    界面上的模型列表可能来自任意量化（Q4_K_XL / IQ4_XS 等），而档位默认量化
    （如 Q5_K_M）不一定存在。未选中模型时直接交给 ``make_plan`` 会因「未找到量化
    Q5_K_M」而启动失败，因此这里给出确定性的兜底：先找文件名含档位量化的最大
    文件，没有就取最大的 GGUF，保证「列表里有可用模型就能启动」。
    """
    if selected is not None:
        return selected
    if not models:
        return None
    wanted = quant.lower()
    matched = [entry for entry in models if wanted in entry.path.name.lower()]
    pool = matched or list(models)
    return max(pool, key=lambda entry: entry.size).path


def model_combo_text(entry: ModelEntry, limit: int = 30) -> str:
    """模型下拉项的紧凑文本：截断长文件名，避免撑宽运行页。"""
    name = entry.path.name
    if len(name) > limit:
        name = name[: limit - 1] + "…"
    return f"{name}  ({human_size(entry.size)})"


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
        extra_args=parse_custom_args(str(values.get("extra_args", ""))),
        gpu_mib=values.get("gpu_mib"),
    )


class _ChoiceCard(QPushButton):  # pragma: no cover - Qt 控件
    """可选项方块：标题 + 下方说明，整块可点击。"""

    def __init__(self, title: str, description: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ChoiceCard")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(72)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)
        title_label = QLabel(title, self)
        title_label.setObjectName("ChoiceTitle")
        title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        desc_label = QLabel(description, self)
        desc_label.setObjectName("ChoiceHint")
        desc_label.setWordWrap(True)
        desc_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(title_label)
        layout.addWidget(desc_label)


class _ChoiceGroup(QWidget):  # pragma: no cover - Qt 控件
    """一组互斥的方块选项，``key()`` / ``set_key()`` 读写当前值。"""

    changed = Signal(str)

    def __init__(
        self,
        options: Sequence[tuple[str, str, str]],
        *,
        current: str = "",
        columns: int = 4,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._current = current
        self._buttons: dict[str, _ChoiceCard] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        for index, (key, title, description) in enumerate(options):
            card = _ChoiceCard(title, description, self)
            card.clicked.connect(lambda _checked=False, key=key: self._select(key))
            self._group.addButton(card)
            self._buttons[key] = card
            row, column = divmod(index, columns)
            grid.addWidget(card, row, column)
        for column in range(columns):
            grid.setColumnStretch(column, 1)
        self.set_key(current, emit=False)

    def key(self) -> str:
        return self._current

    def set_key(self, key: str, *, emit: bool = False) -> None:
        card = self._buttons.get(key)
        if card is None:
            return
        self._current = key
        card.setChecked(True)
        if emit:
            self.changed.emit(key)

    def _select(self, key: str) -> None:
        if key == self._current:
            return
        self._current = key
        self.changed.emit(key)


def _profile_options() -> list[tuple[str, str, str]]:
    titles = {
        "speed": "速度优先",
        "balanced": "均衡",
        "long": "长上下文",
        "max-context": "最大上下文",
    }
    return [(name, titles.get(name, name), profile.note) for name, profile in PROFILES.items()]


def _tune_options() -> list[tuple[str, str, str]]:
    options: list[tuple[str, str, str]] = []
    for name in _llmtune.TUNE_NAMES:
        description = "按显存与上下文自动选择" if name == "auto" else _llmtune.preset(name).detail
        options.append((name, _llmtune.TUNE_LABELS[name], description))
    return options


def _kv_options() -> list[tuple[str, str, str]]:
    return [
        ("", "档位默认", "跟随档位与调优策略自动选择"),
        ("f16", "F16", "精度最高，KV 显存占用最大"),
        ("q8_0", "Q8_0", "约省一半 KV 显存，质量略降"),
        ("q4_0", "Q4_0", "最省显存，长上下文质量损失更明显"),
    ]


class RunPage(QWidget):  # pragma: no cover - Qt 控件
    """运行页：参数表单 + 模型列表 + 计划预览 + 启停动作。"""

    def __init__(self, state: LocllmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._syncing_tune = False
        self._tune_auto = {"kv": False, "ubatch": False, "parallel": False, "threads_http": False}
        self._health_target = "settings"
        self._build()
        self._wire()
        self.on_models_changed()
        self.refresh_service_names()
        self._refresh_command_preview()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self.scroll = PageScroll(self)
        layout = self.scroll.body
        outer.addWidget(self.scroll, 1)

        health_row = QHBoxLayout()
        self.health_label = HintLabel("正在检测运行环境…")
        self.health_button = button("去处理")
        self.health_button.setEnabled(False)
        self.health_button.clicked.connect(lambda: self.state.navigate(self._health_target))
        health_row.addWidget(self.health_label, 1)
        health_row.addWidget(self.health_button)
        layout.addLayout(health_row)

        self.model_card = Card("选择模型")
        model_row = QHBoxLayout()
        self.model_combo = QComboBox(self.model_card)
        self.model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model_combo.setMinimumContentsLength(8)
        self.model_combo.addItem("（未选择，按档位自动查找）", None)
        self.model_combo.currentIndexChanged.connect(self._on_model_combo_changed)
        model_row.addWidget(self.model_combo, 1)
        self.manage_models_button = button("模型管理")
        self.manage_models_button.clicked.connect(lambda: self.state.navigate("models"))
        model_row.addWidget(self.manage_models_button)
        self.model_card.body.addLayout(model_row)
        layout.addWidget(self.model_card)

        self.params_card = Card("启动参数")
        form = FormGrid(self.params_card)
        self.name_combo = QComboBox(self.params_card)
        self.name_combo.setEditable(True)
        self.name_combo.setCurrentText(default_service_name())
        form.add_row("服务名", self.name_combo)
        self.host_edit = QLineEdit(DEFAULT_HOST, self.params_card)
        self.port_edit = QLineEdit(str(DEFAULT_PORT), self.params_card)
        form.add_pair("监听地址", self.host_edit, "端口", self.port_edit)
        self.threads_edit = QLineEdit(str(DEFAULT_THREADS), self.params_card)
        form.add_row("线程数", self.threads_edit)
        self.params_card.body.addWidget(form)

        self.params_card.body.addWidget(QLabel("档位", self.params_card))
        self.profile_choice = _ChoiceGroup(
            _profile_options(), current=DEFAULT_PROFILE, columns=4, parent=self.params_card
        )
        self.params_card.body.addWidget(self.profile_choice)
        self.params_card.body.addWidget(QLabel("调优策略", self.params_card))
        self.tune_choice = _ChoiceGroup(_tune_options(), current="auto", columns=3, parent=self.params_card)
        self.params_card.body.addWidget(self.tune_choice)

        self.advanced = Collapsible("高级参数（留空回落档位默认）", expanded=False, parent=self.params_card)
        adv_form = FormGrid(self.advanced.content)
        self.ctx_edit = QLineEdit("", self.advanced.content)
        adv_form.add_row("上下文", self.ctx_edit)
        self.kv_choice = _ChoiceGroup(_kv_options(), current="", columns=4, parent=self.advanced.content)
        adv_form.add_row("KV", self.kv_choice)
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

        self.params_card.body.addWidget(QLabel("启动命令预览", self.params_card))
        self.command_preview = QPlainTextEdit(self.params_card)
        self.command_preview.setObjectName("Log")
        self.command_preview.setReadOnly(True)
        self.command_preview.setFixedHeight(64)
        self.params_card.body.addWidget(self.command_preview)

        self.params_card.body.addWidget(QLabel("自定义启动参数", self.params_card))
        self.extra_args_edit = QPlainTextEdit(self.params_card)
        self.extra_args_edit.setFixedHeight(56)
        self.extra_args_edit.setPlaceholderText("每行或空格分隔，例如：--no-mmap --flash-attn on")
        self.params_card.body.addWidget(self.extra_args_edit)
        params_actions = QHBoxLayout()
        self.save_params_button = button("保存参数")
        self.save_params_button.clicked.connect(self._save_params)
        self.fill_params_button = button("填入当前参数")
        self.fill_params_button.clicked.connect(self._fill_params)
        params_actions.addWidget(self.save_params_button)
        params_actions.addWidget(self.fill_params_button)
        params_actions.addStretch(1)
        self.params_card.body.addLayout(params_actions)
        layout.addWidget(self.params_card)

        layout.addStretch(1)

        outer.addWidget(self._build_action_bar())

    def _build_action_bar(self) -> QFrame:
        """固定底部动作栏：只保留启动 / 停止选中 / 停止全部三个大按钮。"""
        bar = QFrame(self)
        bar.setObjectName("ActionBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 8, 0, 0)
        row.setSpacing(8)
        self.start_button = button("启动服务", kind="primary")
        self.start_button.setObjectName("RunPrimary")
        self.stop_selected_button = button("停止选中服务")
        self.stop_selected_button.setObjectName("RunAction")
        self.stop_all_button = button("停止全部服务", kind="danger")
        self.stop_all_button.setObjectName("RunDanger")
        for widget in (self.start_button, self.stop_selected_button, self.stop_all_button):
            row.addWidget(widget)
        row.addStretch(1)
        return bar

    def _wire(self) -> None:
        self.tune_choice.changed.connect(lambda _key: self._on_tune_changed())
        self.name_combo.currentTextChanged.connect(self._on_service_name_changed)
        self.kv_choice.changed.connect(lambda _key: self._mark_manual("kv"))
        self.profile_choice.changed.connect(lambda _key: self._refresh_command_preview())
        self.tune_choice.changed.connect(lambda _key: self._refresh_command_preview())
        self.kv_choice.changed.connect(lambda _key: self._refresh_command_preview())
        self.ubatch_edit.textChanged.connect(lambda _text: self._mark_manual("ubatch"))
        self.parallel_edit.textChanged.connect(lambda _text: self._mark_manual("parallel"))
        self.threads_http_edit.textChanged.connect(lambda _text: self._mark_manual("threads_http"))
        self.start_button.clicked.connect(self.start)
        self.stop_selected_button.clicked.connect(self.stop_selected)
        self.stop_all_button.clicked.connect(self.stop_all)
        for widget in (
            self.name_combo,
            self.host_edit,
            self.port_edit,
            self.threads_edit,
            self.ctx_edit,
            self.ubatch_edit,
            self.alias_edit,
            self.parallel_edit,
            self.threads_http_edit,
            self.api_key_edit,
            self.wait_edit,
        ):
            signal = getattr(widget, "textChanged", None) or getattr(widget, "currentTextChanged", None)
            if signal is not None:
                signal.connect(lambda *_args: self._refresh_command_preview())
        self.extra_args_edit.textChanged.connect(self._refresh_command_preview)
        self.model_combo.currentIndexChanged.connect(self._refresh_command_preview)
        self.state.modelsChanged.connect(self._on_models_changed_signal)
        self.state.servicesChanged.connect(self._on_services_changed)
        self.state.selectionChanged.connect(self._on_selection_changed)
        self.state.busyChanged.connect(self._on_busy_changed)

    # ------------------------------------------------------------------ #
    # 参数与调优
    # ------------------------------------------------------------------ #
    def _resolved_model(self) -> Path | None:
        """表单实际使用的模型：显式选择，或按档位量化 / 大小兜底。"""
        profile = PROFILES.get(self.profile_choice.key(), PROFILES[DEFAULT_PROFILE])
        return pick_model(self.state.selected_model, self.state.models, profile.quant)

    def _workflow(self, *, extra_args: str | None = None) -> dict[str, Any]:
        return {
            "name": self.name_combo.currentText(),
            "profile": self.profile_choice.key(),
            "host": self.host_edit.text(),
            "port": self.port_edit.text(),
            "threads": self.threads_edit.text(),
            "tune": self.tune_choice.key(),
            "ctx": self.ctx_edit.text(),
            "kv": self.kv_choice.key(),
            "ubatch": self.ubatch_edit.text(),
            "alias": self.alias_edit.text(),
            "parallel": self.parallel_edit.text(),
            "threads_http": self.threads_http_edit.text(),
            "api_key": self.api_key_edit.text(),
            "extra_args": self.extra_args_edit.toPlainText() if extra_args is None else extra_args,
            "model": self._resolved_model(),
            "gpu_mib": self.state.gpu_mib,
        }

    def _param_values(self) -> dict[str, str]:
        """可持久化的表单值（不含模型路径与硬件快照）。"""
        return {
            "name": self.name_combo.currentText(),
            "profile": self.profile_choice.key(),
            "host": self.host_edit.text(),
            "port": self.port_edit.text(),
            "threads": self.threads_edit.text(),
            "tune": self.tune_choice.key(),
            "ctx": self.ctx_edit.text(),
            "kv": self.kv_choice.key(),
            "ubatch": self.ubatch_edit.text(),
            "alias": self.alias_edit.text(),
            "parallel": self.parallel_edit.text(),
            "threads_http": self.threads_http_edit.text(),
            "api_key": self.api_key_edit.text(),
            "wait": self.wait_edit.text(),
            "extra_args": self.extra_args_edit.toPlainText(),
        }

    def _save_params(self) -> None:
        path = self.state.selected_model or self._resolved_model()
        key = llmenv.path_key(path) if path is not None else ""
        saved = dict(self.state.settings.run_params or {})
        saved[key] = self._param_values()
        self.state.settings.run_params = saved
        self.state.persist()
        self.state.status("启动参数已保存")
        self.state.log(f"已保存启动参数: {path or '（未选择模型）'}")

    def _fill_params(self) -> None:
        """把当前表单生成的启动参数填入自定义参数框，与命令预览保持一致。"""
        try:
            base = build_plan_from(self._workflow(extra_args=""))
        except ServeError as exc:
            self._error("参数错误", str(exc))
            return
        tail = base.argv()[2:]
        self.extra_args_edit.setPlainText(shlex.join(tail))
        self._refresh_command_preview()
        self.state.status("已填入当前启动参数")

    def _refresh_command_preview(self) -> None:
        try:
            plan = self.build_plan()
        except ServeError as exc:
            self.command_preview.setPlainText(f"参数未就绪：{exc}")
            return
        self.command_preview.setPlainText(format_command(plan.argv()))

    def build_plan(self) -> LaunchPlan:
        """按当前表单构造启动计划；重复填入的参数不二次追加。"""
        base = build_plan_from(self._workflow(extra_args=""))
        custom = parse_custom_args(self.extra_args_edit.toPlainText())
        if custom and custom == tuple(base.argv()[2:]):
            return base
        return build_plan_from(self._workflow())

    def profile_context(self) -> tuple[str, int, str]:
        """当前表单下的档位名、上下文与 KV 类型（输入无效时回退档位默认值）。"""
        name = self.profile_choice.key() or DEFAULT_PROFILE
        profile = PROFILES.get(name, PROFILES[DEFAULT_PROFILE])
        try:
            context = parse_optional_int(self.ctx_edit.text(), "上下文") or profile.context
        except ServeError:
            context = profile.context
        kv_type = self.kv_choice.key() or profile.kv_type
        return name, context, kv_type if kv_type in KV_SCALE else profile.kv_type

    def tuning(self) -> llmbase.Tuning:
        """按当前档位 / 上下文 / GPU 解析调优策略。"""
        _, context, _ = self.profile_context()
        size = self._selected_size()
        tune = self.tune_choice.key()
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

    def _mark_manual(self, key: str) -> None:
        if not self._syncing_tune:
            self._tune_auto[key] = False

    def _apply_tuning(self) -> None:
        """把调优策略值同步到高级参数，保留用户手填的非策略值。"""
        tuning = self.tuning()
        synced = _llmtune.sync_advanced_fields(
            tuning,
            kv_value=self.kv_choice.key(),
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
            self.kv_choice.set_key(synced.kv_value)
            self.ubatch_edit.setText(synced.ubatch_value)
            self.parallel_edit.setText(synced.parallel_value)
            self.threads_http_edit.setText(synced.threads_http_value)
        finally:
            self._syncing_tune = False

    def effective_tuning_text(self, tuning: llmbase.Tuning) -> str:
        """提示区展示的生效高级参数（输入框 > 策略 > 档位）。"""
        _, _, profile_kv = self.profile_context()
        profile = PROFILES.get(self.profile_choice.key())
        kv_type = self.kv_choice.key() or tuning.kv_type or profile_kv
        ubatch = self.ubatch_edit.text().strip() or str(tuning.ubatch or (profile.ubatch if profile else ""))
        parallel = self.parallel_edit.text().strip() or str(tuning.parallel)
        threads_http = self.threads_http_edit.text().strip() or str(tuning.threads_http or "-")
        return (
            f"生效: b{tuning.batch} / ub {ubatch} / np {parallel} / th {threads_http} / "
            f"cr {tuning.cache_reuse} / kv {kv_type} / fa {tuning.flash_attn}"
        )

    def _on_tune_changed(self) -> None:
        """切换调优策略后同步高级参数。"""
        self._apply_tuning()

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
        self._select_model_combo(path)

    @staticmethod
    def _load_state_safe(name: str) -> ServeState | None:
        if not name:
            return None
        try:
            return llmenv.load_state(name, missing_ok=True)
        except ServeError:
            return None

    def _on_model_combo_changed(self, index: int) -> None:
        path = self.model_combo.itemData(index)
        if path:
            self.state.select_model(Path(str(path)), update_name=True)
        else:
            self.state.selected_model = None
            self.state.selectionChanged.emit(None, False)

    def _populate_model_combo(self) -> None:
        """把当前扫描到的 GGUF 填入顶部下拉框，保留已选模型。"""
        current = self.state.selected_model
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItem("（未选择，按档位自动查找）", None)
        for entry in self.state.models:
            self.model_combo.addItem(model_combo_text(entry), str(entry.path))
            self.model_combo.setItemData(self.model_combo.count() - 1, str(entry.path), Qt.ToolTipRole)
        self.model_combo.blockSignals(False)
        if current is not None:
            self._select_model_combo(current)

    def _select_model_combo(self, path: Path) -> None:
        target = str(path)
        index = self.model_combo.findData(target)
        if index < 0:
            self.model_combo.blockSignals(True)
            label = model_combo_text(ModelEntry(root=path.parent, path=path, size=self._size_of(path)))
            self.model_combo.addItem(label, target)
            self.model_combo.setItemData(self.model_combo.count() - 1, target, Qt.ToolTipRole)
            index = self.model_combo.count() - 1
            self.model_combo.blockSignals(False)
        self.model_combo.blockSignals(True)
        self.model_combo.setCurrentIndex(index)
        self.model_combo.blockSignals(False)

    def _on_selection_changed(self, path: object, update_name: bool) -> None:
        if path is None:
            self.model_combo.blockSignals(True)
            self.model_combo.setCurrentIndex(0)
            self.model_combo.blockSignals(False)
            return
        selected = Path(path)  # type: ignore[arg-type]
        self._select_model_combo(selected)
        if update_name:
            self.name_combo.setCurrentText(service_name_from_model(selected))
        self._apply_model_recommendation(selected)

    def _apply_model_recommendation(self, path: Path) -> None:
        """选中模型后自动套用该模型在当前硬件上的推荐配置。"""
        limit = max(1, len(self.state.models))
        rows = _locllmw_recommend.recommend(self.state.models, self.state.hardware, self.state.measured, limit=limit)
        rec = next((row for row in rows if row.model.path == path), None)
        if rec is None:
            return
        self.profile_choice.set_key(rec.profile)
        self.ctx_edit.setText(str(rec.context))
        self.tune_choice.set_key("auto")
        self._apply_tuning()
        self.kv_choice.set_key(rec.kv_type)
        self.ubatch_edit.setText(str(rec.ubatch))
        self.parallel_edit.setText(str(rec.parallel))
        self._refresh_command_preview()
        self.state.log(
            f"已套用推荐配置: {path.name} · {rec.profile} / ctx {rec.context} / kv {rec.kv_type} "
            f"/ ub {rec.ubatch} / np {rec.parallel}"
        )

    @staticmethod
    def _size_of(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #
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
        self.state.status(f"正在启动 {plan.name} · {plan.profile.name} · ctx {plan.context}…")
        self.state.set_busy(True)
        submit(
            llmserve.start_server,
            plan,
            wait_s=float(wait_s),
            on_done=self._on_started,
            on_error=self._on_start_failed,
            on_finished=self._on_start_finished,
        )

    def _on_started(self, state: ServeState) -> None:
        self.state.log(f"服务已就绪: {state.base_url}（OpenAI: {state.base_url}/v1）")
        self.state.log(f"  模型: {state.model}")
        self.state.log(f"  档位: {state.profile} / ctx {state.context}")
        self.state.log(f"  日志: {state.log}")
        self.state.status(f"{state.name} · 运行中 · {state.base_url} · pid {state.pid}")
        self.name_combo.setCurrentText(state.name)
        self.state.refresh_services()

    def _on_start_failed(self, message: str) -> None:
        self.state.log(f"启动失败: {message}")
        self._error("启动失败", message)
        self.state.refresh_services()

    def _on_start_finished(self) -> None:
        """启动任务收尾：解除 busy 后再刷新模型列表（刷新会跳过 busy 状态）。"""
        self.state.set_busy(False)
        self.state.refresh_models(self._port_value())
        self._on_profile_changed()

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
        """扫描完成后刷新模型下拉与服务名；未选择时自动带入一个可启动的模型。"""
        self._populate_model_combo()
        self.refresh_service_names()
        if self.state.selected_model is None and self.state.models:
            chosen = self._resolved_model()
            if chosen is not None:
                self.state.log(f"未选择模型，自动使用: {chosen}")
                self.state.select_model(chosen, update_name=True)
                return
        if self.state.selected_model is not None:
            self._select_model_combo(self.state.selected_model)

    def _on_services_changed(self, _states: list[ServeState]) -> None:
        """运行中服务变化：刷新服务名下拉。"""
        self.refresh_service_names()

    def _on_busy_changed(self, busy: bool) -> None:
        for widget in (
            self.start_button,
            self.stop_selected_button,
            self.stop_all_button,
            self.save_params_button,
            self.fill_params_button,
        ):
            widget.setEnabled(not busy)

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
        self.state.select_model(recommendation.model.path, update_name=True)
        self.profile_choice.set_key(recommendation.profile)
        self.tune_choice.set_key("auto")
        self.ctx_edit.setText(str(recommendation.context))
        self._on_tune_changed()
        self.kv_choice.set_key(recommendation.kv_type)
        self.ubatch_edit.setText(str(recommendation.ubatch))
        self.parallel_edit.setText(str(recommendation.parallel))
        self.state.navigate("run")
        self.state.status(f"已带入推荐配置：{recommendation.model.path.name} · {recommendation.config_text}")

    # ------------------------------------------------------------------ #
    # 杂项
    # ------------------------------------------------------------------ #
    def _error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def update_service_and_fit(self) -> None:
        """外部（启动 / 停止 / 显存采样）变化后刷新服务名。"""
        self.refresh_service_names()
