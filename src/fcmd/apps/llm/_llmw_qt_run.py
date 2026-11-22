"""llmw（Qt）运行页：对话 / 编程 / 画图三个用途页签的启动参数与服务启停。

对话 / 编程页签共用同一套 llama.cpp 启动参数表单（模型候选按能力标签区分），
画图页签配置 sd.cpp 的扩散模型与启动参数；引擎安装 / 设为默认统一在设置页。
参数校验、调优策略联动、显存余量检查与启动计划文本都放在模块级纯函数里
（``parse_int`` / ``memory_guard`` / ``plan_lines``），GUI 类只负责取值与展示，
便于单测。

后台动作（启动 / 停止）通过 :func:`fcmd.gui.workers.submit` 执行，
按钮状态由共享状态的 ``busyChanged`` 统一驱动，避免任务期间重复点击。
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionComboBox,
    QStyleOptionViewItem,
    QStylePainter,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.llm import (
    _llmpaint,
    _llmpaint_server,
    _llmtune,
    _llmw_models,
    _llmw_recommend,
    llmbase,
    llmenv,
    llmmodel,
    llmserve,
)
from fcmd.apps.llm._llmquant import matches_profile
from fcmd.apps.llm._llmw_monitor import _busy_advice, _fit_advice, _probe_web_ui
from fcmd.apps.llm._llmw_qt_paint import (
    MODEL_RUNNING_ROLE,
    PaintEngineSection,
    components_from_settings,
    parse_paint_settings,
    start_paint_service,
)
from fcmd.apps.llm._llmw_ui import service_name_from_model
from fcmd.apps.llm.llmbase import (
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
from fcmd.gui import style as _qt_theme
from fcmd.gui.page import AppPage
from fcmd.gui.widgets import (
    Card,
    ChoiceGroup,
    Collapsible,
    FormGrid,
    PageScroll,
    button,
    set_button_text,
    tab_widget,
)
from fcmd.gui.workers import submit, submit_daemon

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fcmd.apps.llm._llmw_qt_state import LlmState

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
    "sanitize_custom_args",
    "startup_args_tail",
]

_PAINT_TAB = 2
"""运行页页签顺序：对话 / 编程 / 画图 / 视频 / 音频。"""
_USAGE_BY_TAB: dict[int, str] = {0: "chat", 1: "code", 3: "video", 4: "audio"}
_USAGE_LABELS = {"chat": "对话", "code": "编程", "video": "视频", "audio": "音频"}


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


def sanitize_custom_args(args: Sequence[str]) -> tuple[str, ...]:
    """移除自定义参数里误带入的模型 / mmproj 参数，避免与内置 ``-m`` 冲突。"""
    cleaned: list[str] = []
    skip_next = False
    for token in args:
        if skip_next:
            skip_next = False
            continue
        lowered = token.casefold()
        if lowered in ("-m", "--model", "--mmproj"):
            skip_next = True
            continue
        if not cleaned and not lowered.startswith("-") and lowered.endswith(".gguf"):
            continue
        cleaned.append(token)
    return tuple(cleaned)


def startup_args_tail(argv: Sequence[str]) -> tuple[str, ...]:
    """去掉可执行文件、``-m <模型>`` 与可选的 ``--mmproj <视觉塔>``，只保留启动参数。"""
    rest = list(argv[3:] if len(argv) >= 3 and argv[1] == "-m" else argv[1:])
    args: list[str] = []
    index = 0
    while index < len(rest):
        if rest[index] == "--mmproj":
            index += 2
            continue
        args.append(rest[index])
        index += 1
    return tuple(args)


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
    matched = [entry for entry in models if matches_profile(entry.path, quant)]
    pool = matched or list(models)
    return max(pool, key=lambda entry: entry.size).path


def model_combo_text(entry: ModelEntry, *, status: str = "", limit: int | None = None) -> str:
    """模型下拉项文本：默认不预截断，由控件按实际宽度省略；``limit`` 可显式截断。"""
    name = entry.path.name
    if limit is not None and len(name) > limit:
        name = name[: limit - 1] + "…"
    text = f"{name}  ({human_size(entry.size)})"
    return f"{text}  · {status}" if status else text


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


_MODEL_RUNNING_ROLE = MODEL_RUNNING_ROLE


class _ModelItemDelegate(QStyledItemDelegate):  # pragma: no cover - Qt 控件
    """模型下拉项：仅运行中的项绘制绿色文字，避免整框被染绿。"""

    def paint(self, painter: Any, option: QStyleOptionViewItem, index: Any) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        theme = _qt_theme.load_theme()
        running = bool(index.data(_MODEL_RUNNING_ROLE))
        if selected:
            color = QColor(theme.palette.on_accent)
        elif running:
            color = QColor(theme.palette.success)
        else:
            color = QColor(theme.palette.text)
        painter.save()
        if running:
            pen = QPen(QColor(theme.palette.success), 1)
            painter.setPen(pen)
            painter.drawRect(opt.rect.adjusted(1, 1, -2, -2))
        painter.setFont(opt.font)
        painter.setPen(color)
        rect = opt.rect.adjusted(8, 0, -6, 0)
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            opt.fontMetrics.elidedText(text, Qt.TextElideMode.ElideRight, rect.width()),
        )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: Any) -> QSize:
        size = super().sizeHint(option, index)
        # 长文件名不再把弹出层撑到屏幕外；宽度不够时由 paint 里的省略号处理。
        return QSize(min(size.width(), 640), 34)


class _ModelCombo(QComboBox):  # pragma: no cover - Qt 控件
    """模型下拉框：当前项按实际宽度省略，宽度足够时完整显示。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setItemDelegate(_ModelItemDelegate(self))

    def paintEvent(self, _event: QPaintEvent) -> None:
        """自绘当前项：把完整文本按编辑区宽度做 ElideRight，不再固定截断。"""
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        text = option.currentText
        option.currentText = ""
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        edit_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxEditField,
            self,
        )
        elided = self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(edit_rect.width(), 0))
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(
            edit_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            elided,
        )
        painter.end()


_MODE_PROFILE: dict[str, str] = {
    "speed": "speed",
    "balanced": "balanced",
    "long": "long",
    "max-context": "max-context",
    "low-vram": "balanced",
}
_MODE_TUNE: dict[str, str] = {
    "speed": "throughput",
    "balanced": "auto",
    "long": "long-context",
    "max-context": "long-context",
    "low-vram": "low-vram",
}
_MODE_TITLES: dict[str, str] = {
    "speed": "速度优先",
    "balanced": "均衡",
    "long": "长上下文 128K",
    "max-context": "超长上下文 192K",
    "low-vram": "省显存",
}
_MODE_TONE: dict[str, str] = {
    "speed": "danger",
    "balanced": "accent",
    "long": "success",
    "max-context": "warning",
    "low-vram": "muted",
}
_MODE_NOTES: dict[str, str] = {
    "speed": "Q4_K_M / 64K，并发槽优先，适合持续吞吐",
    "balanced": "Q5_K_M / 64K，按显存与上下文自动调优",
    "long": "Q5_K_M / 128K，质量优先，适合长文档",
    "max-context": "Q4_K_M / 192K，容量优先，显存更紧张",
    "low-vram": "KV q4_0 / 小 batch，显存紧张时使用",
}


def _mode_options() -> list[tuple[str, str, str]]:
    return [(key, _MODE_TITLES[key], _MODE_NOTES[key]) for key in _MODE_PROFILE]


def _kv_options() -> list[tuple[str, str, str]]:
    return [
        ("", "档位默认", "跟随档位与调优策略自动选择"),
        ("f16", "F16", "精度最高，KV 显存占用最大"),
        ("q8_0", "Q8_0", "约省一半 KV 显存，质量略降"),
        ("q4_0", "Q4_0", "最省显存，长上下文质量损失更明显"),
    ]


def _mode_for_profile(profile: str) -> str:
    """把推荐档位映射到对应的运行模式（保持均衡 / 长上下文等语义）。"""
    for mode, mode_profile in _MODE_PROFILE.items():
        if mode_profile == profile and mode != "low-vram":
            return mode
    return "balanced"


class RunPage(AppPage):  # pragma: no cover - Qt 控件
    """运行页：参数表单 + 模型列表 + 计划预览 + 启停动作。"""

    PAGE_KEY = "run"

    runningChanged = Signal(object)
    """运行中模型的数量变化：``{"chat": 1, "paint": 1, ...}``（侧边栏徽标用）。"""

    def __init__(self, state: LlmState, parent: QWidget | None = None) -> None:
        super().__init__(state, parent, spacing=6)
        self._syncing_tune = False
        self._active_param_key: str | None = None
        self._tune_auto = {"kv": False, "ubatch": False, "parallel": False, "threads_http": False}
        self._usage = "chat"
        self._running_counts: dict[str, int] = dict.fromkeys(_llmw_models.USAGE_KEYS, 0)
        self._paint_busy = False
        self._paint_probe: _llmpaint_server.PaintProbe | None = None
        self._paint_probe_seq = 0
        self._paint_save_timer = QTimer(self)
        self._paint_save_timer.setSingleShot(True)
        self._paint_save_timer.setInterval(400)
        self._paint_save_timer.timeout.connect(self._persist_paint)
        self._build()
        self._wire()
        self.on_models_changed()
        self._refresh_command_preview()
        self._refresh_action_states()
        self.refresh_paint()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        outer = self.body

        # 运行页按用途分页：对话 / 编程共用同一套 llama.cpp 启动参数（模型候选按
        # 能力标签区分），画图页签配 sd.cpp；引擎安装 / 默认引擎统一归设置页。
        # 药丸式页签（共享 tab_widget）：去掉 QTabBar 的整条基线（「多余横条」），
        # 透明底 + 圆角选中块，页签与内容同底色衔接；不参与等宽分配，靠左紧凑排列。
        self.tabs = tab_widget(self)
        outer.addWidget(self.tabs, 1)

        self.service_page = QWidget(self.tabs)
        self.scroll = PageScroll(self.service_page)
        service_layout = QVBoxLayout(self.service_page)
        service_layout.setContentsMargins(0, 0, 0, 0)
        service_layout.setSpacing(6)
        service_layout.addWidget(self.scroll, 1)
        layout = self.scroll.body

        self.chat_tab = QWidget(self.tabs)
        self.chat_tab_layout = QVBoxLayout(self.chat_tab)
        self.chat_tab_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_tab_layout.setSpacing(6)
        self.code_tab = QWidget(self.tabs)
        self.code_tab_layout = QVBoxLayout(self.code_tab)
        self.code_tab_layout.setContentsMargins(0, 0, 0, 0)
        self.code_tab_layout.setSpacing(6)
        self.tabs.addTab(self.chat_tab, "对话")
        self.tabs.addTab(self.code_tab, "编程")
        self._service_layouts = {"chat": self.chat_tab_layout, "code": self.code_tab_layout}

        self.model_card = Card("选择模型")
        model_row = QHBoxLayout()
        self.model_combo = _ModelCombo(self.model_card)
        self.model_combo.setObjectName("ModelCombo")
        self.model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model_combo.setMinimumContentsLength(8)
        self.model_combo.addItem("（未选择，按档位自动查找）", None)
        self.model_combo.currentIndexChanged.connect(self._on_model_combo_changed)
        model_row.addWidget(self.model_combo, 1)
        self.model_action_button = button("启动服务", kind="primary")
        self.model_action_button.setObjectName("RunPrimary")
        self.model_action_button.clicked.connect(self._on_model_action)
        model_row.addWidget(self.model_action_button)
        self.restart_button = button("重启服务", kind="ghost", tooltip="按当前参数停止并重新启动服务")
        self.restart_button.setObjectName("RunAction")
        self.restart_button.setEnabled(False)
        self.restart_button.clicked.connect(self.restart_selected)
        model_row.addWidget(self.restart_button)
        self.model_card.body.addLayout(model_row)
        layout.addWidget(self.model_card)

        self.params_card = Card("启动参数")
        # 参数卡内容做成可替换的插槽：当前四个 LLM 用途装同一份 llama.cpp 启动
        # 表单；视频 / 音频的本地推理后端落地后，只需在 _param_form_for 里返回
        # 各自的引擎表单，页签 / 卡片结构与下方持久化入口都不用动。
        self.param_host = QVBoxLayout()
        self.param_host.setContentsMargins(0, 0, 0, 0)
        self.param_host.setSpacing(6)
        self.params_card.body.addLayout(self.param_host)
        self.launch_form = self._build_launch_form(self.params_card)
        self._install_param_form(self.launch_form)
        layout.addWidget(self.params_card)

        layout.addStretch(1)

        paint_page = QWidget(self.tabs)
        paint_layout = QVBoxLayout(paint_page)
        paint_layout.setContentsMargins(0, 0, 0, 0)
        paint_layout.setSpacing(6)
        self.tabs.addTab(paint_page, "画图")
        self.paint_scroll = PageScroll(paint_page)
        paint_layout.addWidget(self.paint_scroll, 1)
        self._build_paint_tab(self.paint_scroll.body)
        # 视频 / 音频与对话 / 编程共用同一套启动参数表单（两张卡的组织与画图页
        # 一致），只是模型候选按分类筛选；参数卡内容由 _param_form_for 决定，
        # 后端落地后换成独立引擎表单即可。
        for usage, label in (("video", "视频"), ("audio", "音频")):
            page = QWidget(self.tabs)
            tab_layout = QVBoxLayout(page)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            tab_layout.setSpacing(6)
            self.tabs.addTab(page, label)
            setattr(self, f"{usage}_tab", page)
            self._service_layouts[usage] = tab_layout
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._mount_service_page("chat")

    def _build_launch_form(self, parent: QWidget) -> QWidget:
        """构建 llama.cpp 启动参数表单（四个 LLM 用途共用一份实例）。"""
        form_root = QWidget(parent)
        form_layout = QVBoxLayout(form_root)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(6)
        form = FormGrid(form_root)
        self.host_edit = QLineEdit(DEFAULT_HOST, form_root)
        self.port_edit = QLineEdit(str(DEFAULT_PORT), form_root)
        form.add_pair("监听地址", self.host_edit, "端口", self.port_edit)
        self.threads_edit = QLineEdit(str(DEFAULT_THREADS), form_root)
        form.add_row("线程数", self.threads_edit)
        form_layout.addWidget(form)

        form_layout.addWidget(QLabel("运行模式（档位 + 调优）", form_root))
        self.mode_choice = ChoiceGroup(
            _mode_options(),
            current="balanced",
            columns=3,
            tones=_MODE_TONE,
            parent=form_root,
        )
        form_layout.addWidget(self.mode_choice)

        self.advanced = Collapsible("高级参数（留空回落档位默认）", expanded=False, parent=form_root)
        adv_form = FormGrid(self.advanced.content)
        self.ctx_edit = QLineEdit("", self.advanced.content)
        adv_form.add_row("上下文", self.ctx_edit)
        self.kv_choice = ChoiceGroup(_kv_options(), current="", columns=4, parent=self.advanced.content)
        adv_form.add_row("KV", self.kv_choice)
        self.ubatch_edit = QLineEdit("", self.advanced.content)
        self.alias_edit = QLineEdit("", self.advanced.content)
        adv_form.add_pair("ubatch", self.ubatch_edit, "模型别名（留空自动）", self.alias_edit)
        self.parallel_edit = QLineEdit("", self.advanced.content)
        self.parallel_edit.setPlaceholderText("默认 1")
        self.threads_http_edit = QLineEdit("", self.advanced.content)
        adv_form.add_pair("并发槽（-np）", self.parallel_edit, "HTTP 线程", self.threads_http_edit)
        self.api_key_edit = QLineEdit(DEFAULT_API_KEY, self.advanced.content)
        self.wait_edit = QLineEdit("180", self.advanced.content)
        adv_form.add_pair("API Key", self.api_key_edit, "等待就绪（秒）", self.wait_edit)
        self.advanced.body.addWidget(adv_form)
        form_layout.addWidget(self.advanced)

        form_layout.addWidget(QLabel("启动命令预览", form_root))
        self.command_preview = QPlainTextEdit(form_root)
        self.command_preview.setObjectName("Log")
        self.command_preview.setReadOnly(True)
        self.command_preview.setFixedHeight(64)
        form_layout.addWidget(self.command_preview)

        form_layout.addWidget(QLabel("自定义启动参数", form_root))
        self.extra_args_edit = QPlainTextEdit(form_root)
        self.extra_args_edit.setFixedHeight(56)
        self.extra_args_edit.setPlaceholderText("每行或空格分隔，例如：--no-mmap --flash-attn on")
        form_layout.addWidget(self.extra_args_edit)
        params_actions = QHBoxLayout()
        self.save_params_button = button("保存参数")
        self.save_params_button.clicked.connect(self._save_params)
        self.fill_params_button = button("填入当前参数")
        self.fill_params_button.clicked.connect(self._fill_params)
        params_actions.addWidget(self.save_params_button)
        params_actions.addWidget(self.fill_params_button)
        params_actions.addStretch(1)
        form_layout.addLayout(params_actions)
        return form_root

    def _install_param_form(self, form: QWidget) -> None:
        """替换「启动参数」卡的内容；旧表单移出但保留对象，便于切回时复用。"""
        while self.param_host.count():
            item = self.param_host.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        self.param_host.addWidget(form)

    def _param_form_for(self, usage: str) -> QWidget:  # noqa: ARG002 - 后端落地前各用途共用表单
        """按用途取参数表单。

        视频 / 音频目前没有本地推理后端（ADR 0027 / 0040），先复用 llama.cpp
        启动表单；后端落地时在这里换成各自的引擎表单即可，页面结构与按模型
        持久化的入口保持不变。
        """
        return self.launch_form

    def _mount_service_page(self, usage: str) -> None:
        """把启动参数页挂到当前用途的页签（对话 / 编程 / 视频 / 音频同一套表单）。"""
        self._usage = usage
        for target in self._service_layouts.values():
            target.removeWidget(self.service_page)
        self._service_layouts[usage].addWidget(self.service_page, 1)
        self._install_param_form(self._param_form_for(usage))

    def _on_tab_changed(self, index: int) -> None:
        """切换页签：对话 / 编程 / 视频 / 音频换模型候选并同步启动参数；画图只刷新状态。"""
        if index == _PAINT_TAB:
            # 进入画图页签就重扫一次扩散模型：下载 / 转换刚完成的模型不必等
            # 其它页面的信号（扩散权重不进 LLM 模型列表）。
            self.refresh_paint_assets()
            self.refresh_paint()
            self._report_paint_hint()
            return
        usage = _USAGE_BY_TAB.get(index)
        if usage is None or usage == self._usage:
            return
        self._persist_current_params()
        self._mount_service_page(usage)
        self.on_models_changed()
        candidates = [entry.path for entry in self._usage_models()]
        current = self.state.selected_model
        if current is not None and candidates and current not in candidates:
            self.state.select_model(candidates[0], update_name=True)
        self.status(f"已切到「{_USAGE_LABELS[usage]}」启动参数")

    def _usage_models(self) -> list[ModelEntry]:
        """当前用途的模型候选：按能力标签或分类关键字筛选；空时回退全部。"""
        if self._usage == "code":
            rows = [entry for entry in self.state.models if "代码" in llmmodel.detect_model_tags(entry.path.name)]
        elif self._usage in ("video", "audio"):
            rows = [entry for entry in self.state.models if _llmw_models.matches_category(entry.path.name, self._usage)]
        else:
            rows = [entry for entry in self.state.models if "代码" not in llmmodel.detect_model_tags(entry.path.name)]
        return rows or list(self.state.models)

    def _build_paint_tab(self, layout: QVBoxLayout) -> None:
        """画图页签：与对话 / 编程一致——顶部选模型 + 启停，下面只配启动参数。

        引擎安装 / 设为默认在设置页；这里决定「用哪个扩散模型、怎么加载、哪个端口」，
        下方启动参数卡与「对话 / 编程」同构：加载方式方块、运行选项胶囊、高级参数
        折叠区、只读命令预览与自定义启动参数（保存 / 填入）。启停按钮的文案与配色
        对齐 LLM 服务的模型卡片。
        """
        self.paint_model_card = Card("选择模型")
        model_row = QHBoxLayout()
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.setSpacing(8)
        paint_combo = _ModelCombo(self.paint_model_card)
        paint_combo.setObjectName("ModelCombo")
        paint_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        paint_combo.setMinimumContentsLength(8)
        self.paint_engine_section = PaintEngineSection(
            parse_paint_settings(self.state.settings.paint),
            self.paint_model_card,
            show_model=False,
            model_combo=paint_combo,
        )
        self.paint_engine_section.changed.connect(self._on_paint_engine_changed)
        self.paint_engine_section.statusRequested.connect(self.state.status)
        self.paint_engine_section.saveRequested.connect(self._save_paint_params)
        model_row.addWidget(self.paint_engine_section.model_combo, 1)
        self.paint_action_button = button("启动服务", kind="primary")
        self.paint_action_button.setObjectName("RunPrimary")
        self.paint_action_button.setToolTip("按本页启动参数启动 sd-server")
        self.paint_action_button.clicked.connect(self._on_paint_action)
        model_row.addWidget(self.paint_action_button)
        self.paint_restart_button = button("重启服务", kind="ghost", tooltip="按当前参数停止并重新启动画图服务")
        self.paint_restart_button.setObjectName("RunAction")
        self.paint_restart_button.setEnabled(False)
        self.paint_restart_button.clicked.connect(self._restart_paint_service)
        model_row.addWidget(self.paint_restart_button)
        self.paint_model_card.body.addLayout(model_row)
        # 运行状态只看启停按钮文案 + 模型下拉的「✓ 已启动」标记（与对话 / 编程一致），
        # 不再在模型行右侧放状态胶囊，也不在下方占一行说明；明细在概览页。
        self.paint_engine_section.assetsLoaded.connect(self._report_paint_hint)
        self.paint_engine_section.assetsLoaded.connect(self._refresh_paint_marker)
        layout.addWidget(self.paint_model_card)

        self.paint_config_card = Card("启动参数")
        self.paint_config_card.body.addWidget(self.paint_engine_section)
        layout.addWidget(self.paint_config_card)
        layout.addStretch(1)

    @staticmethod
    def _paint_default_hint() -> str:
        return (
            "画图在「对话」页切到「画图」模式后用提示词出图；采样 / 输出目录在对话页 ⚙ 里，"
            "引擎安装 / 设为默认在设置页。"
        )

    def _report_paint_hint(self) -> None:
        """把画图说明 / 缺模型提示写到底部状态栏（只在本页签可见时刷新）。"""
        if self.tabs.currentIndex() != _PAINT_TAB:
            return
        section = getattr(self, "paint_engine_section", None)
        hint = section.missing_model_hint() if section is not None else ""
        self.status(hint or self._paint_default_hint())

    # ------------------------------------------------------------------ #
    # 画图服务
    # ------------------------------------------------------------------ #
    def refresh_paint(self) -> None:
        """刷新画图服务状态：状态文件（同步）+ 端口探针（后台）→ 标签 / 按钮。"""
        state, alive = _llmpaint_server.status()
        self._apply_paint_status(_llmpaint_server.PaintProbe(state, alive, alive, alive))
        if self.tabs.currentIndex() == _PAINT_TAB:
            self._probe_paint()

    def _probe_paint(self) -> None:
        """后台探测端口 / 健康检查：状态文件丢失或外部启动的服务也能看到。"""
        settings = self.paint_settings()
        port = int(settings.get("port") or _llmpaint_server.DEFAULT_PORT)
        self._paint_probe_seq += 1
        seq = self._paint_probe_seq
        submit(
            _llmpaint_server.probe_paint,
            host=_llmpaint_server.DEFAULT_HOST,
            port=port,
            on_done=lambda probe: self._paint_probed(seq, probe),
            on_error=lambda _message: None,
        )

    def _paint_probed(self, seq: int, probe: _llmpaint_server.PaintProbe) -> None:
        """端口探针回到主线程：只采用最后一次探测结果，避免旧结果覆盖新状态。"""
        if seq != self._paint_probe_seq:
            return
        self._paint_probe = probe
        self._apply_paint_status(probe)

    def _apply_paint_status(self, probe: _llmpaint_server.PaintProbe) -> None:
        """记录探针结果，并同步模型下拉标记与启停按钮（运行状态只看这两处）。"""
        self._paint_probe = probe
        self._refresh_paint_marker()
        self._refresh_paint_actions(probe.alive, probe=probe)
        # 画图服务是独立后端：它的启停同样要反映到「画图」页签的数量徽标。
        self.sync_running_badges()

    def _refresh_paint_marker(self) -> None:
        """把「运行中的扩散模型」标记到画图下拉（与对话 / 编程的「✓ 已启动」一致）。"""
        section = getattr(self, "paint_engine_section", None)
        if section is None:
            return
        probe = self._paint_probe
        model = ""
        if probe is not None and probe.alive and probe.state is not None:
            model = probe.state.model
        section.set_running_model(model)

    def _refresh_paint_actions(
        self,
        alive: bool | None = None,
        *,
        probe: _llmpaint_server.PaintProbe | None = None,
    ) -> None:
        """按运行状态切换「启动 / 停止」与重启按钮：与 LLM 模型卡片同一口径。

        ``alive`` 只看本工具登记的进程；端口上探测到未登记的服务（``probe.reachable``）
        也算「在跑」——按钮才能用来结束显存占用。重启只对已登记服务开放。
        """
        if alive is None:
            _state, alive = _llmpaint_server.status()
        if probe is None:
            probe = self._paint_probe
        reachable = bool(probe.reachable) if probe is not None else False
        running = alive or reachable
        busy = self.state.busy or self._paint_busy
        set_button_text(
            self.paint_action_button,
            "停止服务" if running else "启动服务",
            tone="danger" if running else "on_accent",
        )
        self.paint_action_button.setObjectName("RunDanger" if running else "RunPrimary")
        self.paint_action_button.style().unpolish(self.paint_action_button)
        self.paint_action_button.style().polish(self.paint_action_button)
        self.paint_action_button.setEnabled(not busy)
        self.paint_restart_button.setEnabled(not busy and alive)
        section = getattr(self, "paint_engine_section", None)
        if section is not None:
            section.save_params_button.setEnabled(not busy)
            section.fill_params_button.setEnabled(not busy)

    def paint_settings(self) -> dict[str, Any]:
        """完整画图配置：本页引擎配置 + 已保存的采样 / 输出 + 启用标记。"""
        settings = parse_paint_settings(self.state.settings.paint)
        section = getattr(self, "paint_engine_section", None)
        if section is not None:
            settings.update(section.settings())
        return parse_paint_settings(settings)

    def refresh_paint_assets(self) -> None:
        """模型目录变化后重扫扩散模型（画图页签里的下拉）并刷新提示。"""
        self.paint_engine_section.refresh_assets()
        self._report_paint_hint()

    def _on_paint_engine_changed(self) -> None:
        """引擎配置改动：合并写回 paint 设置，延迟落盘，并刷新未选模型提示。"""
        self.state.settings.paint = self.paint_settings()
        self._paint_save_timer.start()
        self._report_paint_hint()

    def _persist_paint(self) -> None:
        self.state.settings.paint = self.paint_settings()
        self.state.persist()

    def _save_paint_params(self) -> None:
        """画图页签「保存参数」：把当前启动参数立即写进 llmw.json（全局只一份）。"""
        self.state.settings.paint = self.paint_settings()
        self.state.persist()
        self.status("画图启动参数已保存")
        self.state.log("已保存画图启动参数")

    def _on_paint_action(self) -> None:
        """同一个按钮：服务空闲时启动，已登记进程则停止，未登记服务则结束残留。"""
        _state, alive = _llmpaint_server.status()
        if alive:
            self._stop_paint_service()
            return
        probe = self._paint_probe
        if probe is not None and probe.reachable:
            self._stop_untracked_paint()
            return
        self._start_paint_service()

    def _stop_untracked_paint(self) -> None:
        """结束端口上未登记的画图服务（状态文件丢失 / CLI / 旧版本遗留）。"""
        port = int(self.paint_settings().get("port") or _llmpaint_server.DEFAULT_PORT)
        probe = self._paint_probe
        pids = [
            item.pid
            for item in (probe.occupants if probe is not None else ())
            if _llmpaint_server.is_paint_process_name(item.name)
        ]
        if not pids:
            self._error(
                "停止画图服务失败",
                f"端口 {port} 上的服务没有登记，也没找到可结束的 sd-server；用 fcmd portcheck c {port} 查看占用进程。",
            )
            return
        answer = QMessageBox.question(
            self,
            "停止画图服务",
            f"端口 {port} 上的画图服务未登记（pid {', '.join(map(str, pids))}），仍要结束这些 sd-server 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._set_paint_busy(True)
        self.status("正在停止未登记的画图服务…")
        submit_daemon(
            _llmpaint_server.stop_orphans,
            port,
            on_done=self._paint_orphans_stopped,
            on_error=self._paint_failed,
            on_finished=self._paint_finished,
        )

    def _paint_orphans_stopped(self, pids: tuple[int, ...]) -> None:
        if pids:
            self.status(f"已停止 {len(pids)} 个未登记的画图服务（pid {', '.join(map(str, pids))}）")
            self.state.log(f"已停止未登记的画图服务：{pids}")
        else:
            self.status("端口上没有可停止的 sd-server")
        self.refresh_paint()

    def _start_paint_service(self) -> None:
        """按当前画图配置启动 / 复用 sd-server。"""
        settings = self.paint_settings()
        try:
            _llmpaint.validate_components(components_from_settings(settings))
        except ServeError as exc:
            self._error("启动画图服务失败", str(exc))
            return
        self._set_paint_busy(True)
        self.status("正在启动画图服务…")
        submit_daemon(
            start_paint_service,
            settings,
            on_done=self._paint_started,
            on_error=self._paint_failed,
            on_finished=self._paint_finished,
        )

    def _paint_started(self, started: bool) -> None:
        self.status("画图服务已就绪" if started else "画图服务已在按当前配置运行")
        self.state.log("已启动画图服务" if started else "画图服务已在运行")
        self.refresh_paint()

    def _stop_paint_service(self) -> None:
        self._set_paint_busy(True)
        self.status("正在停止画图服务…")
        submit_daemon(
            _llmpaint_server.stop_paint,
            on_done=self._paint_stopped,
            on_error=self._paint_failed,
            on_finished=self._paint_finished,
        )

    def _paint_stopped(self, result: _llmpaint_server.PaintStopResult) -> None:
        self.status("画图服务已停止" if result.stopped else "画图服务未运行")
        if result.stopped:
            self.state.log("已停止画图服务")
        self.refresh_paint()

    def _restart_paint_service(self) -> None:
        """按当前参数停止并重新启动画图服务。"""
        settings = self.paint_settings()
        try:
            _llmpaint.validate_components(components_from_settings(settings))
        except ServeError as exc:
            self._error("重启画图服务失败", str(exc))
            return
        self._set_paint_busy(True)
        self.status("正在重启画图服务…")
        submit_daemon(
            self._restart_paint_work,
            settings,
            on_done=lambda _started: self._paint_started(True),
            on_error=self._paint_failed,
            on_finished=self._paint_finished,
        )

    @staticmethod
    def _restart_paint_work(settings: dict[str, Any]) -> bool:
        _llmpaint_server.stop_paint()
        return start_paint_service(settings)

    def _paint_failed(self, message: str) -> None:
        self.state.log(f"画图服务操作失败：{message}")
        self._error("画图服务", message)
        self.refresh_paint()

    def _paint_finished(self) -> None:
        self._set_paint_busy(False)

    def _set_paint_busy(self, busy: bool) -> None:
        self._paint_busy = busy
        self._refresh_paint_actions()

    def _wire(self) -> None:
        self.kv_choice.changed.connect(lambda _key: self._mark_manual("kv"))
        self.mode_choice.changed.connect(lambda _key: self._on_mode_changed())
        self.mode_choice.changed.connect(lambda _key: self._refresh_command_preview())
        self.kv_choice.changed.connect(lambda _key: self._refresh_command_preview())
        self.ubatch_edit.textChanged.connect(lambda _text: self._mark_manual("ubatch"))
        self.parallel_edit.textChanged.connect(lambda _text: self._mark_manual("parallel"))
        self.threads_http_edit.textChanged.connect(lambda _text: self._mark_manual("threads_http"))
        for widget in (
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
        profile = PROFILES.get(self._mode_profile(), PROFILES[DEFAULT_PROFILE])
        return pick_model(self.state.selected_model, self.state.models, profile.quant)

    def _custom_args(self) -> tuple[str, ...]:
        return sanitize_custom_args(parse_custom_args(self.extra_args_edit.toPlainText()))

    def _workflow(self, *, extra_args: str | None = None) -> dict[str, Any]:
        return {
            "name": self._service_name(),
            "profile": self._mode_profile(),
            "host": self.host_edit.text(),
            "port": self.port_edit.text(),
            "threads": self.threads_edit.text(),
            "tune": self._mode_tune(),
            "ctx": self.ctx_edit.text(),
            "kv": self.kv_choice.key(),
            "ubatch": self.ubatch_edit.text(),
            "alias": self.alias_edit.text(),
            "parallel": self.parallel_edit.text(),
            "threads_http": self.threads_http_edit.text(),
            "api_key": self.api_key_edit.text(),
            "extra_args": shlex.join(self._custom_args()) if extra_args is None else extra_args,
            "model": self._resolved_model(),
            "gpu_mib": self.state.gpu_mib,
        }

    def _param_values(self) -> dict[str, str]:
        """可持久化的表单值（不含模型路径与硬件快照）。"""
        return {
            "name": self._service_name(),
            "profile": self._mode_profile(),
            "host": self.host_edit.text(),
            "port": self.port_edit.text(),
            "threads": self.threads_edit.text(),
            "tune": self._mode_tune(),
            "ctx": self.ctx_edit.text(),
            "kv": self.kv_choice.key(),
            "ubatch": self.ubatch_edit.text(),
            "alias": self.alias_edit.text(),
            "parallel": self.parallel_edit.text(),
            "threads_http": self.threads_http_edit.text(),
            "api_key": self.api_key_edit.text(),
            "wait": self.wait_edit.text(),
            "extra_args": self.extra_args_edit.toPlainText(),
            "mode": self.mode_choice.key(),
        }

    def _param_key(self) -> str:
        path = self.state.selected_model or self._resolved_model()
        return llmenv.path_key(path) if path is not None else ""

    def _persist_current_params(self) -> None:
        key = self._active_param_key
        if not key:
            return
        saved = dict(self.state.settings.run_params or {})
        saved[key] = self._param_values()
        self.state.settings.run_params = saved

    def persist_params(self) -> None:
        """模型切换 / 关窗时把当前模型的启动参数落到配置。"""
        self._persist_current_params()
        self.state.persist()

    def _save_params(self) -> None:
        key = self._param_key()
        self._active_param_key = key
        self._persist_current_params()
        self.state.persist()
        self.status("启动参数已保存")
        self.state.log(f"已保存启动参数: {self.state.selected_model or '（未选择模型）'}")

    def _apply_saved_params(self, saved: dict[str, Any]) -> None:
        mode = str(saved.get("mode") or _mode_for_profile(str(saved.get("profile", "balanced"))))
        self.mode_choice.set_key(mode)
        self.host_edit.setText(str(saved.get("host", self.host_edit.text())))
        self.port_edit.setText(str(saved.get("port", self.port_edit.text())))
        self.threads_edit.setText(str(saved.get("threads", self.threads_edit.text())))
        self.ctx_edit.setText(str(saved.get("ctx", self.ctx_edit.text())))
        self.kv_choice.set_key(str(saved.get("kv", self.kv_choice.key())))
        self.ubatch_edit.setText(str(saved.get("ubatch", self.ubatch_edit.text())))
        self.alias_edit.setText(str(saved.get("alias", self.alias_edit.text())))
        self.parallel_edit.setText(str(saved.get("parallel", self.parallel_edit.text())))
        self.threads_http_edit.setText(str(saved.get("threads_http", self.threads_http_edit.text())))
        self.api_key_edit.setText(str(saved.get("api_key", self.api_key_edit.text())))
        self.wait_edit.setText(str(saved.get("wait", self.wait_edit.text())))
        self.extra_args_edit.setPlainText(str(saved.get("extra_args", "")))
        self._refresh_command_preview()

    def _fill_params(self) -> None:
        """把当前表单生成的启动参数填入自定义参数框，与命令预览保持一致。"""
        try:
            base = build_plan_from(self._workflow(extra_args=""))
        except ServeError as exc:
            self._error("参数错误", str(exc))
            return
        tail = startup_args_tail(base.argv())
        self.extra_args_edit.setPlainText(shlex.join(tail))
        self._refresh_command_preview()
        self.status("已填入当前启动参数")

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
        custom = self._custom_args()
        if custom and custom == startup_args_tail(base.argv()):
            return base
        return build_plan_from(self._workflow())

    def profile_context(self) -> tuple[str, int, str]:
        """当前表单下的档位名、上下文与 KV 类型（输入无效时回退档位默认值）。"""
        name = self._mode_profile() or DEFAULT_PROFILE
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
        tune = self._mode_tune()
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
        profile = PROFILES.get(self._mode_profile())
        kv_type = self.kv_choice.key() or tuning.kv_type or profile_kv
        ubatch = self.ubatch_edit.text().strip() or str(tuning.ubatch or (profile.ubatch if profile else ""))
        parallel = self.parallel_edit.text().strip() or str(tuning.parallel)
        threads_http = self.threads_http_edit.text().strip() or str(tuning.threads_http or "-")
        return (
            f"生效: b{tuning.batch} / ub {ubatch} / np {parallel} / th {threads_http} / "
            f"cr {tuning.cache_reuse} / kv {kv_type} / fa {tuning.flash_attn}"
        )

    def _mode_profile(self) -> str:
        return _MODE_PROFILE.get(self.mode_choice.key(), DEFAULT_PROFILE)

    def _mode_tune(self) -> str:
        return _MODE_TUNE.get(self.mode_choice.key(), "auto")

    def _on_mode_changed(self) -> None:
        """切换运行模式后同步高级参数。"""
        self._apply_tuning()

    # ------------------------------------------------------------------ #
    # 模型选择与提示
    # ------------------------------------------------------------------ #
    def _service_name(self) -> str:
        """自动服务名：优先运行中服务，否则由当前模型文件名推导。"""
        path = self.state.selected_model or self._resolved_model()
        if path is not None:
            current = self.state.state_for_model(path)
            return current.name if current is not None else service_name_from_model(path)
        return default_service_name()

    def service_name(self) -> str:
        """当前应使用的服务名（供概览采样等外部逻辑读取）。"""
        return self._service_name()

    def _on_model_action(self) -> None:
        """顶部按钮：当前模型运行中则停止，否则启动。"""
        selected = self._selected_service_state()
        if selected is not None and llmserve.process_alive(selected.pid, selected.create_time):
            self.stop_selected()
        else:
            self.start()

    def _selected_service_state(self) -> ServeState | None:
        path = self.state.selected_model or self._resolved_model()
        return self.state.state_for_model(path) if path is not None else None

    def _refresh_action_states(self) -> None:
        """按运行状态切换三个底部按钮的可用性。"""
        busy = self.state.busy
        selected = self._selected_service_state()
        running = selected is not None and llmserve.process_alive(selected.pid, selected.create_time)
        has_model = self.state.selected_model is not None or bool(self.state.models)
        set_button_text(
            self.model_action_button,
            "停止服务" if running else "启动服务",
            tone="danger" if running else "on_accent",
        )
        self.model_action_button.setObjectName("RunDanger" if running else "RunPrimary")
        self.model_action_button.style().unpolish(self.model_action_button)
        self.model_action_button.style().polish(self.model_action_button)
        self.model_action_button.setEnabled(not busy and has_model)
        # 重启只在服务已运行时可用；任务进行中禁用，避免停止 / 启动竞争。
        self.restart_button.setEnabled(not busy and running)

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
        self._refresh_combo_current_color()

    def _service_status_text(self, path: Path) -> str:
        """只有正在运行的服务才返回状态；未运行不占用下拉项文本。"""
        return "✓ 已启动" if self.state.is_running(path) else ""

    def _paint_combo_status(self, index: int, path: Path) -> None:
        self.model_combo.setItemData(index, bool(self._service_status_text(path)), _MODEL_RUNNING_ROLE)

    def _refresh_combo_current_color(self) -> None:
        path = self.state.selected_model
        running = path is not None and bool(self._service_status_text(path))
        self.model_combo.setProperty("running", "true" if running else "false")
        self.model_combo.style().unpolish(self.model_combo)
        self.model_combo.style().polish(self.model_combo)

    def _populate_model_combo(self) -> None:
        """把当前扫描到的 GGUF 与启动状态填入顶部下拉框，保留已选模型。"""
        current = self.state.selected_model
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for entry in self._usage_models():
            self.model_combo.addItem(
                model_combo_text(entry, status=self._service_status_text(entry.path)), str(entry.path)
            )
            index = self.model_combo.count() - 1
            self.model_combo.setItemData(index, str(entry.path), Qt.ToolTipRole)
            self._paint_combo_status(index, entry.path)
        self.model_combo.blockSignals(False)
        if current is not None:
            self._select_model_combo(current)
        self._refresh_combo_current_color()

    def _select_model_combo(self, path: Path) -> None:
        target = str(path)
        index = self.model_combo.findData(target)
        if index < 0:
            self.model_combo.blockSignals(True)
            label = model_combo_text(
                ModelEntry(root=path.parent, path=path, size=self._size_of(path)),
                status=self._service_status_text(path),
            )
            self.model_combo.addItem(label, target)
            index = self.model_combo.count() - 1
            self.model_combo.setItemData(index, target, Qt.ToolTipRole)
            self._paint_combo_status(index, path)
            self.model_combo.blockSignals(False)
        self.model_combo.blockSignals(True)
        self.model_combo.setCurrentIndex(index)
        self.model_combo.blockSignals(False)
        self._refresh_combo_current_color()

    def _on_selection_changed(self, path: object, update_name: bool) -> None:
        if path is None:
            self.model_combo.blockSignals(True)
            self.model_combo.setCurrentIndex(-1)
            self.model_combo.blockSignals(False)
            self._refresh_action_states()
            return
        selected = Path(path)  # type: ignore[arg-type]
        self._select_model_combo(selected)
        del update_name
        key = llmenv.path_key(selected)
        if self._active_param_key and self._active_param_key != key:
            self._persist_current_params()
        self._active_param_key = key
        saved = (self.state.settings.run_params or {}).get(key)
        if isinstance(saved, dict):
            self._apply_saved_params(saved)
        else:
            self._apply_model_recommendation(selected)
        self._refresh_action_states()

    def _apply_model_recommendation(self, path: Path) -> None:
        """选中模型后自动套用该模型在当前硬件上的推荐配置。"""
        limit = max(1, len(self.state.models))
        rows = _llmw_recommend.recommend(self.state.models, self.state.hardware, self.state.measured, limit=limit)
        rec = next((row for row in rows if row.model.path == path), None)
        if rec is None:
            return
        self.mode_choice.set_key(_mode_for_profile(rec.profile))
        self.ctx_edit.setText(str(rec.context))
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
        from fcmd.apps.llm._llmw_qt_state import gpu_usage

        try:
            note = memory_guard(plan, gpu_usage())
        except ServeError as exc:
            self.state.log("已取消启动：" + str(exc))
            self._error("资源不足", str(exc))
            return
        self.state.log(note)
        self._active_param_key = llmenv.path_key(plan.model)
        self._persist_current_params()
        self.state.settings.last_model = str(plan.model)
        self.state.remember_quant_choice(plan.model)
        self.state.persist()
        self.state.log(f"正在启动 {plan.name}，等待 /health（最多 {wait_s}s）…")
        self.status(f"正在启动 {plan.name} · {plan.profile.name} · ctx {plan.context}…")
        self.state.set_busy(True)
        submit(
            llmserve.start_server,
            plan,
            wait_s=float(wait_s),
            on_done=self._on_started,
            on_error=self._on_start_failed,
            on_finished=self._on_start_finished,
        )

    def start_paint_model(self, path: Path) -> None:
        """模型页扩散模型快捷启动：切到「画图」页签、选中主模型并启动 sd-server。"""
        self.tabs.setCurrentIndex(_PAINT_TAB)
        self.paint_engine_section.select_model(path)
        self._start_paint_service()

    def start_model(self, path: Path) -> None:
        """选中指定模型并立即启动（模型页快捷启动入口）。"""
        self.state.select_model(path, update_name=True)
        current = self.state.state_for_model(path)
        if current is not None and llmserve.process_alive(current.pid, current.create_time):
            self.status(f"{current.name} · 运行中 · {current.base_url}")
            self.state.log(f"{path.name} 已在运行，无需重复启动")
            return
        self.start()

    def _on_started(self, state: ServeState) -> None:
        self.state.log(f"服务已就绪: {state.base_url}（OpenAI: {state.base_url}/v1）")
        self.state.log(f"  模型: {state.model}")
        self.state.log(f"  档位: {state.profile} / ctx {state.context}")
        self.state.log(f"  日志: {state.log}")
        self.status(f"{state.name} · 运行中 · {state.base_url} · pid {state.pid}")
        self.state.refresh_services()

    def _on_start_failed(self, message: str) -> None:
        self.state.log(f"启动失败: {message}")
        self._error("启动失败", message)
        self.state.refresh_services()

    def _on_start_finished(self) -> None:
        """启动任务收尾：解除 busy 后再刷新模型列表（刷新会跳过 busy 状态）。"""
        self.state.set_busy(False)
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
            self.status("无已登记服务")
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
        current = self._selected_service_state()
        if current is None:
            self.status("当前模型没有已登记服务")
            self.state.log("当前模型没有已登记服务，无需停止")
            return
        name = current.name
        state = current
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

    def restart_selected(self) -> None:
        """停止当前服务后按当前参数重新启动（按钮仅在服务运行时可用）。"""
        if self.state.busy:
            self.state.log("已有任务正在进行，请等待完成")
            return
        current = self._selected_service_state()
        if current is None or not llmserve.process_alive(current.pid, current.create_time):
            self.status("当前模型没有运行中的服务")
            self.state.log("当前模型没有运行中的服务，无法重启")
            self._refresh_action_states()
            return
        self.state.log(f"正在重启服务 {current.name}…")
        self.status(f"正在重启 {current.name}…")
        self.state.set_busy(True)
        submit(
            self._stop_worker,
            (current.name,),
            on_done=self._on_restart_stopped,
            on_error=self._on_restart_failed,
            # busy 保持到 start() 接管，避免停止与启动之间按钮可点。
        )

    def _on_restart_stopped(self, payload: tuple[list[StopResult], list[str]]) -> None:
        """重启第一步结束：旧服务已停就按当前表单参数重新启动。"""
        results, failures = payload
        for result in results:
            if result.was_running:
                self.state.log(f"已停止服务 {result.state.name}")
        self.state.refresh_services()
        if failures:
            self.state.set_busy(False)
            self.status("重启失败")
            self._error("重启失败", "\n".join(failures))
            self._refresh_action_states()
            return
        self.state.log("旧服务已停止，正在按当前参数启动…")
        self.state.set_busy(False)  # start() 内部会再次置 busy
        self.start()

    def _on_restart_failed(self, message: str) -> None:
        """重启第一步失败（停止异常）：解除 busy 并报告原因。"""
        self.state.set_busy(False)
        self.status("重启失败")
        self._error("重启失败", message)
        self._refresh_action_states()

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
            self.status(f"停止完成 · {len(stopped)} 个已停止 · {len(failures)} 个失败")
            self._error("停止失败", "\n".join(failures))
        elif stopped:
            self.status(f"已停止 {len(stopped)} 个服务")
        else:
            self.status("无运行中的服务（已清理残留状态）")
        self.state.refresh_services()
        self._refresh_action_states()
        self.state.refresh_models(self._port_value())

    def refresh_status(self) -> None:
        """读取状态文件并刷新状态 / 日志（不改变正在进行的任务）。"""
        self.state.refresh_services()
        name = self._service_name()
        state = self._load_state_safe(name)
        if state is None:
            self.status(f"{name} · 未启动")
            self.state.log(f"服务 {name} 未启动（尚无状态文件，持续显示系统负载）")
            return
        alive = llmserve.process_alive(state.pid, state.create_time)
        self.status(f"{state.name} · {'运行中' if alive else '已退出'} · {state.base_url} · pid {state.pid}")
        self.state.log(f"服务 {state.name}: {'运行中' if alive else '已退出'}")
        self.state.log(f"  地址: {state.base_url}")
        self.state.log(f"  模型: {state.model}")
        self.state.log(f"  档位: {state.profile} / ctx {state.context}")
        self.state.log(f"  启动时间: {state.started_at}")
        self.state.log(f"  日志: {state.log}")

    def open_browser(self) -> None:
        """在默认浏览器中打开服务地址（优先运行中服务的实际地址）。"""
        import webbrowser

        name = self._service_name()
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

    def _on_models_changed_signal(self, _outcome: object) -> None:
        """``modelsChanged`` 槽：忽略扫描结果，直接刷新列表（结果已在共享状态里）。"""
        self.on_models_changed()

    def on_models_changed(self) -> None:
        """扫描完成后刷新模型下拉与服务名；未选择时自动带入一个可启动的模型。"""
        self._populate_model_combo()
        if self.state.selected_model is None and self.state.models:
            last = self.state.settings.last_model
            last_path = Path(last) if last else None
            chosen = last_path if last_path is not None and last_path.is_file() else self._resolved_model()
            if chosen is not None:
                self.state.log(f"未选择模型，自动使用: {chosen}")
                self.state.select_model(chosen, update_name=True)
                return
        if self.state.selected_model is not None:
            self._select_model_combo(self.state.selected_model)
        self._refresh_action_states()

    def _on_services_changed(self, _states: list[ServeState]) -> None:
        """运行中服务变化：刷新下拉框状态、底部按钮与页签数量徽标。"""
        self._populate_model_combo()
        self._refresh_action_states()
        self.sync_running_badges()

    # ------------------------------------------------------------------ #
    # 运行数量徽标
    # ------------------------------------------------------------------ #
    def paint_alive(self) -> bool:
        """画图（sd.cpp）服务是否在运行（状态文件或端口探针任一确认）。"""
        return bool(self._paint_probe is not None and self._paint_probe.alive)

    def running_counts(self) -> dict[str, int]:
        """按用途统计运行中的模型：对话 / 编程 / 画图 / 视频 / 音频。"""
        models = [state.model for state in self.state.alive_states]
        return _llmw_models.running_usage_counts(models, paint_alive=self.paint_alive())

    def sync_running_badges(self) -> None:
        """把运行数量写到页签徽标，并在数量变化时通知窗口刷新侧边栏徽标。"""
        counts = self.running_counts()
        bar = self.tabs.tabBar()
        for index, usage in _USAGE_BY_TAB.items():
            bar.set_badge(index, counts.get(usage, 0))
        bar.set_badge(_PAINT_TAB, counts.get("paint", 0))
        if counts != self._running_counts:
            self._running_counts = counts
            self.runningChanged.emit(dict(counts))

    def _on_busy_changed(self, busy: bool) -> None:
        self.save_params_button.setEnabled(not busy)
        self.fill_params_button.setEnabled(not busy)
        self._refresh_action_states()
        self._refresh_paint_actions()

    def apply_recommendation(self, recommendation: Any) -> None:
        """把推荐行的模型与启动配置带入运行页。"""
        self.state.select_model(recommendation.model.path, update_name=True)
        self.mode_choice.set_key(_mode_for_profile(recommendation.profile))
        self.ctx_edit.setText(str(recommendation.context))
        self._on_mode_changed()
        self.kv_choice.set_key(recommendation.kv_type)
        self.ubatch_edit.setText(str(recommendation.ubatch))
        self.parallel_edit.setText(str(recommendation.parallel))
        self.state.navigate("run")
        self.status(f"已带入推荐配置：{recommendation.model.path.name} · {recommendation.config_text}")

    # ------------------------------------------------------------------ #
    # 杂项
    # ------------------------------------------------------------------ #
    def _error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def update_service_and_fit(self) -> None:
        """外部（启动 / 停止 / 显存采样）变化后刷新按钮状态。"""
        self._refresh_action_states()
