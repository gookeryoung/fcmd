"""llmw（Qt）运行页：文本 / 图像两个能力域页签的启动参数与服务启停。

文本页（对话 / 编程 / 推理合并）使用 llama.cpp 启动参数表单，图像页签配置
sd.cpp 的扩散模型与启动参数；引擎安装 / 设为默认统一在设置页。
参数校验、调优策略联动、显存余量检查与启动计划文本都放在模块级纯函数里
（``parse_int`` / ``memory_guard`` / ``plan_lines``），GUI 类只负责取值与展示，
便于单测。

后台动作（启动 / 停止）通过 :func:`fcmd.gui.workers.submit` 执行，
按钮状态由共享状态的 ``busyChanged`` 统一驱动，避免任务期间重复点击。
"""

from __future__ import annotations

import json
import shlex
import time
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
    _llmconfig,
    _llmpaint,
    _llmpaint_server,
    _llmtune,
    _llmvllm,
    _llmw_compat,
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
    hint_label,
    set_button_text,
    tab_widget,
)
from fcmd.gui.workers import submit, submit_daemon

from .qt.run_actions import RunActionsMixin
from .qt.run_models import RunModelsMixin
from .qt.run_paint import RunPaintMixin
from .qt.run_params import RunParamsMixin

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fcmd.apps.llm._llmw_qt_state import LlmState
    from fcmd.gui.theme import Theme

__all__ = [
    "RunPage",
    "build_plan_from",
    "default_service_name",
    "memory_guard",
    "model_combo_text",
    "model_item_text_color",
    "parse_custom_args",
    "parse_int",
    "parse_optional_int",
    "plan_lines",
    "sanitize_custom_args",
    "startup_args_tail",
]

_PAINT_TAB = 1
"""运行页页签顺序：文本 / 图像。"""
_USAGE_BY_TAB: dict[int, str] = {0: "text"}
_USAGE_LABELS = {"text": "文本"}


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


def model_item_text_color(theme: Theme, *, selected: bool, running: bool) -> str:
    """模型下拉项文字色（弹层项与当前项共用一套语义色）。

    选中项必须用主题的 ``select_fg``：弹层里的选中底色是 ``select_bg``，在深色 /
    浅色主题下都不等于 ``accent``，写死 ``on_accent`` 会得到「深色字压中蓝底」的低
    对比（切到已启动 / 未启动模型时选中项颜色看起来都不对）。运行中的项由绿色描边
    与「✓ 已启动」文案继续表达，不会因为被选中而丢失标记。
    """
    palette = theme.palette
    if selected:
        return palette.select_fg
    return palette.success if running else palette.text


class _ModelItemDelegate(QStyledItemDelegate):  # pragma: no cover - Qt 控件
    """模型下拉项：运行中的项绘制绿色文字与描边，选中项跟随主题选中色。"""

    def paint(self, painter: Any, option: QStyleOptionViewItem, index: Any) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        # 运行时主题可能尚未落盘，用 active_theme 而不是 load_theme，避免沿用旧调色板。
        theme = _qt_theme.active_theme()
        running = bool(index.data(_MODEL_RUNNING_ROLE))
        color = QColor(model_item_text_color(theme, selected=selected, running=running))
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
        # 当前项颜色按「运行中」角色直接取语义色，不再只依赖 QSS 写进调色板的 color：
        # 切换已启动 / 未启动模型后（属性重新 polish）个别平台样式不会刷新调色板，
        # 会出现该绿不绿 / 该恢复正文色仍发绿的残留。
        running = bool(self.itemData(self.currentIndex(), _MODEL_RUNNING_ROLE))
        if running:
            painter.setPen(QColor(_qt_theme.active_theme().palette.success))
        else:
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


def _infer_engine_options(platform: _llmvllm.VllmPlatform) -> list[tuple[str, str, str]]:
    """文本页的引擎族选项：llama.cpp（GGUF）与 vLLM（HF safetensors）。"""
    if platform.supported:
        note = "HF safetensors · WSL2 / Linux"
    else:
        note = "当前系统不可用"
    return [
        ("llama.cpp", "llama.cpp", "GGUF · 现有引擎，通用"),
        ("vllm", "vLLM", note),
    ]


def _hf_context(path: Path | None, fallback: int = 32768) -> int:
    """从 HF ``config.json`` 读取最大位置数作为 vLLM 默认上下文。"""
    if path is None:
        return fallback
    try:
        data = json.loads((path / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback
    if not isinstance(data, dict):
        return fallback
    for key in ("max_position_embeddings", "n_positions", "seq_length", "model_max_length"):
        value = data.get(key)
        if isinstance(value, int) and 0 < value <= 1_048_576:
            return value
    return fallback


def _parse_ratio(text: str) -> float | None:
    """解析显存占比；留空返回 ``None``（由 vLLM 用默认值）。"""
    raw = text.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ServeError(f"显存占比不是数字: {raw}") from exc


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


class RunPage(
    RunPaintMixin,
    RunParamsMixin,
    RunModelsMixin,
    RunActionsMixin,
    AppPage,
):  # pragma: no cover - Qt 控件
    """运行页：参数表单 + 模型列表 + 计划预览 + 启停动作。"""

    PAGE_KEY = "run"

    runningChanged = Signal(object)
    """运行中模型的数量变化：``{"text": 1, "paint": 1, ...}``（侧边栏徽标用）。"""

    def __init__(self, state: LlmState, parent: QWidget | None = None) -> None:
        super().__init__(state, parent, spacing=6)
        self._syncing_tune = False
        self._active_param_key: str | None = None
        self._tune_auto = {"kv": False, "ubatch": False, "parallel": False, "threads_http": False}
        self._usage = "text"
        self._vllm_platform = _llmvllm.detect_platform()
        self._hf_models: list[_llmvllm.HfModel] = []
        self._running_counts: dict[str, int] = dict.fromkeys(_llmw_models.USAGE_KEYS, 0)
        self._paint_busy = False
        self._paint_probe: _llmpaint_server.PaintProbe | None = None
        self._paint_probe_seq = 0
        self._paint_expected = False
        self._paint_watch_restarting = False
        self._paint_watch_failures = 0
        self._paint_watch_retry_at = 0.0
        self._paint_save_timer = QTimer(self)
        self._paint_save_timer.setSingleShot(True)
        self._paint_save_timer.setInterval(400)
        self._paint_save_timer.timeout.connect(self._persist_paint)
        self._build()
        self._wire()
        self._apply_engine_mode()
        self.on_models_changed()
        self._refresh_command_preview()
        self._refresh_action_states()
        self.refresh_paint()
        # 初始已在运行（含上次 llmw 退出后留下的服务）时也纳入看护：意外退出后自动重启。
        self._paint_expected = bool(self._paint_probe is not None and self._paint_probe.alive)
        self._paint_watch_timer = QTimer(self)
        self._paint_watch_timer.setInterval(5000)
        self._paint_watch_timer.timeout.connect(self._watch_paint)
        self._paint_watch_timer.start()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        outer = self.body

        # 运行页按能力域分页：文本（对话 / 编程 / 推理）用 llama.cpp 启动参数，
        # 图像页签配 sd.cpp；引擎安装 / 默认引擎统一归设置页。
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

        self.text_tab = QWidget(self.tabs)
        self.text_tab_layout = QVBoxLayout(self.text_tab)
        self.text_tab_layout.setContentsMargins(0, 0, 0, 0)
        self.text_tab_layout.setSpacing(6)
        self.tabs.addTab(self.text_tab, "文本")
        self._service_layouts = {"text": self.text_tab_layout}

        self.model_card = Card("选择模型")
        engine_box = QVBoxLayout()
        engine_box.setContentsMargins(0, 0, 0, 0)
        engine_box.setSpacing(4)
        engine_box.addWidget(QLabel("推理引擎", self.model_card))
        self.engine_choice = ChoiceGroup(
            _infer_engine_options(self._vllm_platform),
            current=self._engine_kind(),
            columns=2,
            tones={"vllm": "accent"},
            parent=self.model_card,
        )
        if not self._vllm_platform.supported:
            self.engine_choice.set_option_enabled("vllm", False, f"vLLM 不可用：{self._vllm_platform.reason}")
        self.engine_choice.changed.connect(self._on_engine_changed)
        engine_box.addWidget(self.engine_choice)
        self.model_card.body.addLayout(engine_box)
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
        self.model_compat_hint = hint_label("", wrap=True)
        self.model_card.body.addWidget(self.model_compat_hint)
        layout.addWidget(self.model_card)

        self.params_card = Card("启动参数")
        # 参数卡内容做成可替换的插槽：文本 / 视频 / 音频装同一份 llama.cpp 启动
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
        self.tabs.addTab(paint_page, "图像")
        self.paint_scroll = PageScroll(paint_page)
        paint_layout.addWidget(self.paint_scroll, 1)
        self._build_paint_tab(self.paint_scroll.body)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._mount_service_page("text")

    # ------------------------------------------------------------------ #
    # 推理引擎族（llama.cpp / vLLM）
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # 画图服务
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # 画图服务看护
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # 参数与调优
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # 模型选择与提示
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # 运行数量徽标
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # 杂项
    # ------------------------------------------------------------------ #


_REEXPORTS = (
    Card,
    ChoiceGroup,
    Collapsible,
    DEFAULT_API_KEY,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_PROFILE,
    DEFAULT_THREADS,
    FormGrid,
    KV_SCALE,
    ModelEntry,
    PROFILES,
    PaintEngineSection,
    Path,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
    Qt,
    ServeError,
    _MODEL_RUNNING_ROLE,
    _MODE_PROFILE,
    _MODE_TONE,
    _MODE_TUNE,
    _ModelCombo,
    _PAINT_TAB,
    _USAGE_BY_TAB,
    _USAGE_LABELS,
    _hf_context,
    _kv_options,
    _llmconfig,
    _llmpaint,
    _llmpaint_server,
    _llmtune,
    _llmvllm,
    _llmw_compat,
    _llmw_models,
    _llmw_recommend,
    _mode_for_profile,
    _mode_options,
    _parse_ratio,
    _probe_web_ui,
    build_plan_from,
    button,
    components_from_settings,
    default_service_name,
    format_command,
    hint_label,
    http_base_url,
    llmbase,
    llmenv,
    llmmodel,
    llmserve,
    memory_guard,
    model_combo_text,
    parse_custom_args,
    parse_int,
    parse_optional_int,
    parse_paint_settings,
    pick_model,
    sanitize_custom_args,
    service_name_from_model,
    set_button_text,
    shlex,
    start_paint_service,
    startup_args_tail,
    submit,
    submit_daemon,
    time,
)
