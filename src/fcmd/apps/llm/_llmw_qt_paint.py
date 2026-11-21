"""llmw（Qt）画图页：托管 stable-diffusion.cpp 的本地文生图。

页面只做编排：模型扫描、组件配对、启动参数、请求载荷与出图保存都在
:mod:`fcmd.apps.llm._llmpaint` / ``_llmpaint_server`` / ``_llmpaint_engine``
（与 ``fcmd llmpaint`` 共用），这里负责控件、线程投递与设置持久化。
页面采用与对话页一致的扁平布局：左侧提示词 composer 与参数 / 模型表单，
右侧大图预览与最近图库；状态提示写进窗口底部状态栏。

模块级函数都是纯逻辑（解析设置、构造组件 / 请求、命令预览），供测试直接断言；
:class:`PaintPage` 是需要 Qt 事件循环的 GUI 胶水（``pragma: no cover``）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.llm import _llmconfig, _llmpaint, _llmpaint_engine, _llmpaint_server
from fcmd.apps.llm._llmw_models import open_path
from fcmd.apps.llm.llmbase import ServeError, format_command, human_size
from fcmd.gui.qt.widgets import (
    Collapsible,
    FormGrid,
    PageScroll,
    ProgressRow,
    StatusTag,
    button,
    hint_label,
    mono_label,
)
from fcmd.gui.qt.workers import submit, submit_daemon

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fcmd.apps.llm._llmw_qt_state import LlmState

__all__ = [
    "MODES",
    "SAMPLERS",
    "SIZE_PRESETS",
    "PaintPage",
    "command_preview",
    "components_from_settings",
    "default_settings",
    "format_extra_args",
    "format_progress",
    "model_roots",
    "output_dir",
    "parse_extra_args",
    "parse_paint_settings",
    "request_from_settings",
    "service_label",
    "size_preset",
]

SIZE_PRESETS: tuple[tuple[str, int, int], ...] = (
    ("512 × 512", 512, 512),
    ("768 × 768", 768, 768),
    ("1024 × 1024", 1024, 1024),
    ("832 × 1216（竖）", 832, 1216),
    ("1216 × 832（横）", 1216, 832),
)
"""常用尺寸预设（name, width, height）；与 sd.cpp 的 8 倍数要求兼容。"""

SAMPLERS: tuple[str, ...] = (
    "euler_a",
    "euler",
    "heun",
    "dpm2",
    "dpm++2m",
    "dpm++2s_a",
    "dpm++2mv2",
    "ipndm",
    "ipndm_v",
    "lcm",
    "ddim_trailing",
    "tcd",
)
"""下拉里的采样器候选；服务不认时接口层会自动去掉该字段重试。"""

MODES: tuple[tuple[str, str], ...] = (
    ("auto", "自动"),
    ("checkpoint", "Checkpoint"),
    ("diffusion", "Diffusion"),
)
"""加载方式：(数据值, 显示名)。"""

ROLE_COMBOS: tuple[tuple[str, str], ...] = (
    ("vae", "VAE"),
    ("clip_l", "CLIP-L"),
    ("clip_g", "CLIP-G"),
    ("t5xxl", "T5-XXL"),
    ("llm", "文本编码器"),
)
"""组件下拉：(设置键, 显示名)。"""

_TEXT_KEYS = (
    "prompt",
    "negative",
    "engine",
    "model",
    "vae",
    "clip_l",
    "clip_g",
    "t5xxl",
    "llm",
    "lora",
    "model_dir",
    "output_dir",
    "sampler",
    "scheduler",
    "mode",
)
_BOOL_KEYS = ("auto_start", "offload_to_cpu", "vae_tiling", "diffusion_fa")
_INT_RANGES: dict[str, tuple[int, int]] = {
    "port": (1, 65535),
    "width": (64, 4096),
    "height": (64, 4096),
    "steps": (1, 150),
    "seed": (-1, 2_147_483_647),
    "batch": (1, 8),
}


def default_settings() -> dict[str, Any]:
    """画图页的默认设置（与 ``fcmd llmpaint gen`` 的默认值一致）。"""
    return {
        "prompt": "",
        "negative": "",
        "engine": "",
        "model": "",
        "vae": "",
        "clip_l": "",
        "clip_g": "",
        "t5xxl": "",
        "llm": "",
        "lora": "",
        "model_dir": "",
        "output_dir": "",
        "mode": "auto",
        "width": 512,
        "height": 512,
        "steps": 25,
        "cfg_scale": 7.0,
        "sampler": "euler_a",
        "scheduler": "",
        "seed": -1,
        "batch": 1,
        "port": _llmpaint_server.DEFAULT_PORT,
        "auto_start": True,
        "offload_to_cpu": False,
        "vae_tiling": False,
        "diffusion_fa": False,
        "extra_args": (),
    }


def parse_paint_settings(raw: Any) -> dict[str, Any]:
    """把持久化 / 表单数据规整成页面设置：非法值回退默认，数值夹到合法区间。"""
    settings = default_settings()
    if not isinstance(raw, dict):
        return settings
    for key in _TEXT_KEYS:
        value = raw.get(key)
        if isinstance(value, str):
            settings[key] = value.strip()
    for key in _BOOL_KEYS:
        value = raw.get(key)
        if isinstance(value, bool):
            settings[key] = value
    for key, (low, high) in _INT_RANGES.items():
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            settings[key] = min(max(value, low), high)
    cfg_scale = raw.get("cfg_scale")
    if isinstance(cfg_scale, (int, float)) and not isinstance(cfg_scale, bool):
        settings["cfg_scale"] = min(max(float(cfg_scale), 0.0), 30.0)
    extra = raw.get("extra_args")
    if isinstance(extra, str):
        settings["extra_args"] = parse_extra_args(extra)
    elif isinstance(extra, (list, tuple)):
        settings["extra_args"] = tuple(str(item) for item in extra if isinstance(item, str) and item.strip())
    return settings


def parse_extra_args(text: str) -> tuple[str, ...]:
    """把「额外参数」输入框拆成 argv：按空白分隔，双引号内的空格保留。"""
    tokens: list[str] = []
    current: list[str] = []
    quoted = False
    for char in text:
        if char == '"':
            quoted = not quoted
            continue
        if char.isspace() and not quoted:
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def format_extra_args(args: Sequence[str]) -> str:
    """把 argv 写回输入框：含空白的值用双引号包裹。"""
    return " ".join(f'"{item}"' if " " in item else item for item in args)


def _path_value(value: Any) -> Path | None:
    if isinstance(value, str) and value.strip():
        return Path(value).expanduser()
    return None


def _engine_tag(settings: dict[str, Any]) -> str | None:
    value = settings.get("engine")
    return value if isinstance(value, str) and value.strip() else None


def components_from_settings(settings: dict[str, Any]) -> _llmpaint.PaintComponents:
    """按页面设置构造 sd-server 的模型组件集合。"""
    return _llmpaint.PaintComponents(
        model=_path_value(settings.get("model")),
        vae=_path_value(settings.get("vae")),
        clip_l=_path_value(settings.get("clip_l")),
        clip_g=_path_value(settings.get("clip_g")),
        t5xxl=_path_value(settings.get("t5xxl")),
        llm=_path_value(settings.get("llm")),
        lora=_path_value(settings.get("lora")),
        mode=str(settings.get("mode") or "auto"),
        extra_args=tuple(settings.get("extra_args") or ()),
        offload_to_cpu=bool(settings.get("offload_to_cpu")),
        vae_tiling=bool(settings.get("vae_tiling")),
        diffusion_fa=bool(settings.get("diffusion_fa")),
    )


def request_from_settings(settings: dict[str, Any]) -> _llmpaint.PaintRequest:
    """按页面设置构造文生图请求（数值已由 :func:`parse_paint_settings` 收敛）。"""
    return _llmpaint.PaintRequest(
        prompt=str(settings.get("prompt") or ""),
        negative=str(settings.get("negative") or ""),
        width=int(settings.get("width") or 512),
        height=int(settings.get("height") or 512),
        steps=int(settings.get("steps") or 25),
        cfg_scale=float(settings.get("cfg_scale") or 7.0),
        seed=int(settings.get("seed", -1)),
        sampler=str(settings.get("sampler") or ""),
        scheduler=str(settings.get("scheduler") or ""),
        batch=int(settings.get("batch") or 1),
    )


def model_roots(settings: dict[str, Any]) -> list[Path]:
    """模型扫描根：页面覆盖目录优先，否则模型目录 + ``converted`` 产物目录。"""
    override = settings.get("model_dir")
    if isinstance(override, str) and override.strip():
        return [Path(override).expanduser()]
    return list(dict.fromkeys((_llmconfig.models_dir(), _llmconfig.products_dir())))


def output_dir(settings: dict[str, Any]) -> Path:
    """出图目录：页面覆盖优先，否则 ``${FCMD_HOME}/paint/out``。"""
    override = settings.get("output_dir")
    if isinstance(override, str) and override.strip():
        return Path(override).expanduser()
    return _llmpaint_server.default_out_dir()


def command_preview(settings: dict[str, Any]) -> str:
    """sd-server 启动命令预览（组件参数在启动时加载）。"""
    port = int(settings.get("port") or _llmpaint_server.DEFAULT_PORT)
    argv = [
        "sd-server",
        "--listen-ip",
        _llmpaint_server.DEFAULT_HOST,
        "--listen-port",
        str(port),
        *_llmpaint.build_model_args(components_from_settings(settings)),
    ]
    return format_command(argv)


def service_label(state: _llmpaint_server.PaintState | None, alive: bool) -> tuple[str, str]:
    """服务状态标签：(文案, 语义色)。"""
    if state is None:
        return "服务未启动", "muted"
    if alive:
        return f"运行中 · {state.engine_tag or 'sd.cpp'}", "success"
    return "已退出（残留状态）", "warning"


def format_progress(payload: Any) -> str:
    """进度响应 → 百分比文本；服务不支持进度时返回空串。"""
    value = _llmpaint_server.progress_value(payload)
    return "" if value is None else f"{value * 100:.0f}%"


def size_preset(width: int, height: int) -> str:
    """返回匹配的预设名称；没有匹配时返回 ``自定义``。"""
    for label, preset_width, preset_height in SIZE_PRESETS:
        if (preset_width, preset_height) == (width, height):
            return label
    return "自定义"


class PaintPage(QWidget):  # pragma: no cover - Qt 控件
    """画图页：提示词 / 采样参数 + 模型组件 + 结果预览与最近图库。"""

    def __init__(self, state: LlmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._settings = parse_paint_settings(getattr(state.settings, "paint", None))
        self._assets = _llmpaint.PaintAssets()
        self._assets_loaded = False
        self._engines: list[_llmpaint_engine.SdEngine] = []
        self._running = False
        self._polling = False
        self._closed = False
        self._auto_started = False
        self._start_flag: dict[str, bool] = {"value": False}
        self._progress_ok = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = PageScroll(self)
        outer.addWidget(scroll)
        layout = scroll.body
        layout.addWidget(self._build_toolbar())

        columns = QGridLayout()
        columns.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(10)
        left.addWidget(self._build_prompt_card())
        left.addWidget(self._build_params_card())
        left.addWidget(self._build_models_card())
        left.addStretch(1)
        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(self._build_result_card(), 1)
        columns.addLayout(left, 0, 0)
        columns.addLayout(right, 0, 1)
        columns.setColumnStretch(0, 3)
        columns.setColumnStretch(1, 4)
        layout.addLayout(columns)
        layout.addStretch(1)

        self._poll = QTimer(self)
        self._poll.setInterval(1500)
        self._poll.timeout.connect(self._refresh_progress)

        self._apply_settings()
        self._set_status("就绪")
        self._refresh_gallery()
        self.refresh_engine()
        self.refresh_status()
        self.refresh_assets()

    # ------------------------------------------------------------------ #
    # 布局
    # ------------------------------------------------------------------ #
    def _build_toolbar(self) -> QWidget:
        """顶栏：引擎 / 服务状态、端口与安装、启停（不套卡片，与对话页工具行一致）。"""
        bar = QWidget(self)
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.engine_tag = StatusTag("引擎检查中…", parent=bar)
        self.service_tag = StatusTag("服务未启动", parent=bar)
        row.addWidget(self.engine_tag)
        row.addWidget(self.service_tag)
        row.addStretch(1)
        self.port = QSpinBox(bar)
        self.port.setRange(1, 65535)
        self.port.setPrefix("端口 ")
        self.port.setMaximumWidth(120)
        self.port.setToolTip("sd-server 监听端口（默认 1234，避开 llama-server 的 8080）")
        self.port.valueChanged.connect(lambda _value: self._update_preview())
        row.addWidget(self.port)
        self.install_button = button("安装引擎…", kind="ghost")
        self.install_button.setToolTip("下载 stable-diffusion.cpp 预编译包，或导入本地 zip / 目录")
        self.install_button.clicked.connect(self._install_engine)
        self.start_button = button("启动服务", kind="ghost")
        self.start_button.setToolTip("按当前模型与参数启动 sd-server")
        self.start_button.clicked.connect(self._start_service)
        self.stop_button = button("停止服务", kind="ghost")
        self.stop_button.clicked.connect(self._stop_service)
        for widget in (self.install_button, self.start_button, self.stop_button):
            row.addWidget(widget)
        return bar

    def _build_prompt_card(self) -> QWidget:
        """提示词输入区：复用对话页的圆角 composer（无边框输入 + 底部工具行）。"""
        composer = QFrame(self)
        composer.setObjectName("ChatComposer")
        composer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        body = QVBoxLayout(composer)
        body.setContentsMargins(12, 10, 12, 10)
        body.setSpacing(4)
        self.prompt = QPlainTextEdit(composer)
        self.prompt.setObjectName("ChatInput")
        self.prompt.setPlaceholderText("描述想画的内容，例如：a cinematic photo of a red fox in snow, 85mm lens")
        self.prompt.setMinimumHeight(92)
        self.prompt.setMaximumHeight(160)
        body.addWidget(self.prompt)
        self.negative = QPlainTextEdit(composer)
        self.negative.setObjectName("ChatInput")
        self.negative.setPlaceholderText("负面提示词（可留空），例如：blurry, low quality, watermark")
        self.negative.setMaximumHeight(56)
        body.addWidget(self.negative)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.auto_start = QCheckBox("自动启动", composer)
        self.auto_start.setToolTip("生成前自动启动服务；模型 / 参数变化时自动重启（FLUX 等大模型首次加载较慢）")
        tools.addWidget(self.auto_start)
        self.progress_row = ProgressRow(parent=composer)
        self.progress = self.progress_row.bar
        self.generate_button = button("生成", kind="primary")
        self.generate_button.clicked.connect(self.generate)
        self.interrupt_button = button("中断", kind="ghost")
        self.interrupt_button.setEnabled(False)
        self.interrupt_button.setToolTip("请求服务停止当前任务（服务不支持时静默降级）")
        self.interrupt_button.clicked.connect(self._interrupt)
        self.progress_row.add_action(self.generate_button)
        self.progress_row.add_action(self.interrupt_button)
        tools.addWidget(self.progress_row, 1)
        body.addLayout(tools)
        return composer

    def _build_params_card(self) -> QWidget:
        """采样参数：直接铺在页面底色上，表单标签已足够说明，不再套卡片标题。"""
        form = FormGrid(self)
        self.preset_combo = QComboBox(form)
        self.preset_combo.addItem("自定义", None)
        for label, width, height in SIZE_PRESETS:
            self.preset_combo.addItem(label, (width, height))
        self.preset_combo.currentIndexChanged.connect(lambda _index: self._apply_preset())
        form.add_row("尺寸预设", self.preset_combo)

        size_row = QWidget(form)
        size_layout = QHBoxLayout(size_row)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.setSpacing(6)
        self.width = QSpinBox(size_row)
        self.width.setRange(64, 4096)
        self.width.setSingleStep(8)
        self.width.setToolTip("宽度：64–4096，8 的倍数")
        self.height = QSpinBox(size_row)
        self.height.setRange(64, 4096)
        self.height.setSingleStep(8)
        self.height.setToolTip("高度：64–4096，8 的倍数")
        self.width.valueChanged.connect(lambda _value: self._sync_preset())
        self.height.valueChanged.connect(lambda _value: self._sync_preset())
        size_layout.addWidget(self.width, 1)
        size_layout.addWidget(hint_label("×", wrap=False))
        size_layout.addWidget(self.height, 1)
        form.add_row("宽 × 高", size_row)

        self.steps = QSpinBox(form)
        self.steps.setRange(1, 150)
        self.cfg = QDoubleSpinBox(form)
        self.cfg.setRange(0.0, 30.0)
        self.cfg.setSingleStep(0.5)
        self.cfg.setDecimals(1)
        form.add_pair("步数", self.steps, "CFG 引导", self.cfg)

        self.sampler = QComboBox(form)
        self.sampler.setEditable(True)
        self.sampler.addItems(list(SAMPLERS))
        self.scheduler = QLineEdit(form)
        self.scheduler.setPlaceholderText("留空用引擎默认")
        form.add_pair("采样器", self.sampler, "调度器", self.scheduler)

        seed_row = QWidget(form)
        seed_layout = QHBoxLayout(seed_row)
        seed_layout.setContentsMargins(0, 0, 0, 0)
        seed_layout.setSpacing(6)
        self.seed = QSpinBox(seed_row)
        self.seed.setRange(0, 2_147_483_647)
        self.random_seed = QCheckBox("随机", seed_row)
        self.random_seed.setToolTip("勾选时使用 -1（每次随机），取消后可固定种子复现")
        self.random_seed.toggled.connect(self.seed.setDisabled)
        seed_layout.addWidget(self.seed)
        seed_layout.addWidget(self.random_seed)
        seed_layout.addStretch(1)
        self.batch = QSpinBox(form)
        self.batch.setRange(1, 8)
        form.add_pair("种子", seed_row, "批量", self.batch)
        return form

    def _build_models_card(self) -> QWidget:
        """模型与组件：族信息 + 下拉 + 选项，平铺在页面底色上。"""
        holder = QWidget(self)
        body = QVBoxLayout(holder)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)
        self.family_label = mono_label("未选择主模型")
        body.addWidget(self.family_label)
        form = FormGrid(holder)
        self.mode_combo = QComboBox(form)
        for key, label in MODES:
            self.mode_combo.addItem(label, key)
        form.add_row("加载方式", self.mode_combo)
        self.model_combo = QComboBox(form)
        self.model_combo.setMinimumWidth(240)
        self.model_combo.currentIndexChanged.connect(lambda _index: self._on_model_changed())
        form.add_row("主模型", self.model_combo)
        self.role_combos: dict[str, QComboBox] = {}
        for role, label in ROLE_COMBOS:
            combo = QComboBox(form)
            combo.currentIndexChanged.connect(lambda _index: self._update_preview())
            self.role_combos[role] = combo
            form.add_row(label, combo)
        body.addWidget(form)

        self.offload_to_cpu = QCheckBox("CPU 卸载", holder)
        self.offload_to_cpu.setToolTip("--offload-to-cpu：显存不足时把部分权重放到内存（更慢）")
        self.vae_tiling = QCheckBox("VAE 分块", holder)
        self.vae_tiling.setToolTip("--vae-tiling：大图解码时降低显存峰值")
        self.diffusion_fa = QCheckBox("Diffusion FA", holder)
        self.diffusion_fa.setToolTip("--diffusion-fa：启用 flash attention 加速")
        for widget in (self.offload_to_cpu, self.vae_tiling, self.diffusion_fa):
            widget.toggled.connect(lambda _checked: self._update_preview())
        body.addLayout(self._row(self.offload_to_cpu, self.vae_tiling, self.diffusion_fa))

        self.extra_edit = QLineEdit(holder)
        self.extra_edit.setPlaceholderText("额外参数（可选，例如 --clip-on-cpu）")
        self.extra_edit.setToolTip("原样追加到 sd-server；按空白分隔，含空格的值用双引号包裹")
        self.extra_edit.textChanged.connect(lambda _text: self._update_preview())
        body.addWidget(self.extra_edit)

        self.preview_collapse = Collapsible("命令预览", parent=holder)
        self.preview_label = mono_label("")
        self.preview_label.setWordWrap(True)
        self.preview_label.setObjectName("Hint")
        self.preview_collapse.body.addWidget(self.preview_label)
        body.addWidget(self.preview_collapse)
        return holder

    def _build_result_card(self) -> QWidget:
        """出图结果：工具行 + 大图预览 + 最近图库，不再另设标题与输出路径行。"""
        holder = QWidget(self)
        body = QVBoxLayout(holder)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        header.addStretch(1)
        self.output_button = button("打开输出目录", kind="ghost")
        self.output_button.clicked.connect(self._open_output_dir)
        self.suggest_button = button("自动配对", kind="ghost")
        self.suggest_button.setToolTip("按主模型家族填满 VAE / 文本编码器槽位")
        self.suggest_button.clicked.connect(lambda: self._suggest(only_empty=False))
        self.save_to_button = button("保存到…", kind="ghost")
        self.save_to_button.clicked.connect(self._pick_output_dir)
        header.addWidget(self.suggest_button)
        header.addWidget(self.save_to_button)
        header.addWidget(self.output_button)
        body.addLayout(header)

        self.preview = QLabel("尚未生成", holder)
        self.preview.setObjectName("PaintPreview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(360)
        body.addWidget(self.preview, 1)
        self.gallery_title = hint_label("最近出图")
        self.gallery_title.setVisible(False)
        body.addWidget(self.gallery_title)
        self.gallery_grid = QGridLayout()
        self.gallery_grid.setSpacing(6)
        body.addLayout(self.gallery_grid)
        return holder

    @staticmethod
    def _row(*widgets: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        for widget in widgets:
            row.addWidget(widget)
        row.addStretch(1)
        return row

    # ------------------------------------------------------------------ #
    # 设置读写
    # ------------------------------------------------------------------ #
    def _apply_settings(self) -> None:
        """把设置回填到控件（组合框的选中项等资产扫描完成后再补）。"""
        settings = self._settings
        self.prompt.setPlainText(str(settings["prompt"]))
        self.negative.setPlainText(str(settings["negative"]))
        self.port.setValue(int(settings["port"]))
        self.width.setValue(int(settings["width"]))
        self.height.setValue(int(settings["height"]))
        self.steps.setValue(int(settings["steps"]))
        self.cfg.setValue(float(settings["cfg_scale"]))
        self.sampler.setCurrentText(str(settings["sampler"]))
        self.scheduler.setText(str(settings["scheduler"]))
        seed = int(settings["seed"])
        self.random_seed.setChecked(seed < 0)
        self.seed.setValue(0 if seed < 0 else seed)
        self.batch.setValue(int(settings["batch"]))
        self.auto_start.setChecked(bool(settings["auto_start"]))
        self.offload_to_cpu.setChecked(bool(settings["offload_to_cpu"]))
        self.vae_tiling.setChecked(bool(settings["vae_tiling"]))
        self.diffusion_fa.setChecked(bool(settings["diffusion_fa"]))
        self.extra_edit.setText(format_extra_args(tuple(settings["extra_args"])))
        index = self.mode_combo.findData(str(settings["mode"]))
        self.mode_combo.setCurrentIndex(max(index, 0))
        self._apply_component_selection()
        self._sync_preset()
        self._refresh_output_label()
        self._update_preview()

    def _collect_settings(self) -> dict[str, Any]:
        """把控件内容收集成设置（已扫描过资产时以组合框为准）。"""
        settings = dict(self._settings)
        settings.update(
            {
                "prompt": self.prompt.toPlainText().strip(),
                "negative": self.negative.toPlainText().strip(),
                "port": self.port.value(),
                "width": self.width.value(),
                "height": self.height.value(),
                "steps": self.steps.value(),
                "cfg_scale": self.cfg.value(),
                "sampler": self.sampler.currentText().strip(),
                "scheduler": self.scheduler.text().strip(),
                "seed": -1 if self.random_seed.isChecked() else self.seed.value(),
                "batch": self.batch.value(),
                "auto_start": self.auto_start.isChecked(),
                "offload_to_cpu": self.offload_to_cpu.isChecked(),
                "vae_tiling": self.vae_tiling.isChecked(),
                "diffusion_fa": self.diffusion_fa.isChecked(),
                "extra_args": parse_extra_args(self.extra_edit.text()),
                "mode": str(self.mode_combo.currentData() or "auto"),
                "model": self._component_value(self.model_combo, "model"),
            }
        )
        for role, _label in ROLE_COMBOS:
            settings[role] = self._component_value(self.role_combos[role], role)
        return parse_paint_settings(settings)

    def _component_value(self, combo: QComboBox, key: str) -> str:
        """组合框当前值；资产还没扫描完时沿用已保存的路径（避免误清空）。"""
        data = combo.currentData()
        if isinstance(data, str):
            return data
        return "" if self._assets_loaded else str(self._settings.get(key) or "")

    def _persist(self) -> None:
        self.state.settings.paint = dict(self._settings)
        self.state.persist()

    # ------------------------------------------------------------------ #
    # 资产与引擎
    # ------------------------------------------------------------------ #
    def refresh_assets(self) -> None:
        """后台重新扫描扩散模型与组件（模型目录变化时由窗口调用）。"""
        if self._assets_loaded:
            self._settings = self._collect_settings()
        settings = dict(self._settings)
        submit(
            lambda: _llmpaint.scan_assets(model_roots(settings)),
            on_done=self._apply_assets,
            on_error=self._assets_failed,
        )

    def _assets_failed(self, message: str) -> None:
        if self._closed:
            return
        self.state.log(f"扫描画图模型失败：{message}")
        self._set_status(f"扫描模型失败：{message}")

    def _apply_assets(self, assets: _llmpaint.PaintAssets) -> None:
        if self._closed:
            return
        self._assets = assets
        self._assets_loaded = True
        self._fill_combo(
            self.model_combo,
            [asset.path for asset in assets.main_models()],
            placeholder="（未找到扩散模型）",
            show_role=False,
        )
        for role, combo in self.role_combos.items():
            self._fill_combo(combo, [asset.path for asset in assets.by_role(role)], placeholder="（不指定）")
        if not str(self._settings.get("model") or "") and self.model_combo.count() > 1:
            # 没有历史选择时默认选中第一个主模型（与 CLI 的唯一候选自动选用对齐）。
            self.model_combo.setCurrentIndex(1)
        self._apply_component_selection()
        if not assets.assets:
            roots = "、".join(str(root) for root in assets.roots)
            self._set_status(f"未找到扩散模型（扫描目录：{roots}）" if roots else "未找到扩散模型")
        self._update_preview()

    def _fill_combo(
        self,
        combo: QComboBox,
        paths: Sequence[Path],
        *,
        placeholder: str,
        show_role: bool = True,
    ) -> None:
        """重建下拉：首项为占位，其余按「文件名 · 角色」列出；保留原选中项。"""
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(placeholder, "")
        for path in paths:
            asset = self._assets.get(path)
            suffix = f" · {asset.role_label}" if asset is not None and show_role else ""
            combo.addItem(f"{path.name}{suffix}", str(path))
            detail = f"{path}\n{human_size(asset.size)}" if asset is not None else str(path)
            combo.setItemData(combo.count() - 1, detail, Qt.ItemDataRole.ToolTipRole)
        index = combo.findData(previous) if isinstance(previous, str) and previous else -1
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _apply_component_selection(self) -> None:
        """按设置恢复主模型与各组件选中项。"""
        self._select_path(self.model_combo, str(self._settings.get("model") or ""))
        for role, combo in self.role_combos.items():
            self._select_path(combo, str(self._settings.get(role) or ""))
        self._on_model_changed()

    @staticmethod
    def _select_path(combo: QComboBox, value: str) -> None:
        if not value:
            return
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _combo_path(combo: QComboBox) -> Path | None:
        data = combo.currentData()
        return Path(data).expanduser() if isinstance(data, str) and data else None

    def _on_model_changed(self) -> None:
        """主模型变化：刷新家族提示，并给空槽位补默认组件。"""
        path = self._combo_path(self.model_combo)
        asset = self._assets.get(path) if path is not None else None
        if asset is None:
            self.family_label.setText("未选择主模型")
        else:
            self.family_label.setText(f"{asset.family_label} · {human_size(asset.size)}")
        self._suggest(only_empty=True)
        self._update_preview()

    def _suggest(self, *, only_empty: bool) -> None:
        """按家族槽位给出建议组件；``only_empty`` 时只补空槽位。"""
        path = self._combo_path(self.model_combo)
        if path is None:
            return
        suggestion = _llmpaint.suggest_components(self._assets, path)
        for role, combo in self.role_combos.items():
            value = getattr(suggestion, role, None)
            if value is None:
                continue
            if only_empty and self._combo_path(combo) is not None:
                continue
            self._select_path(combo, str(value))
        self._update_preview()

    def refresh_engine(self) -> None:
        """刷新引擎状态标签（只读本地目录，不联网）。"""
        try:
            engines = _llmpaint_engine.list_sd_engines()
            current = _llmpaint_engine.current_sd_tag()
        except OSError:
            engines, current = [], None
        self._engines = list(engines)
        if not engines:
            self.engine_tag.set_status("引擎未安装", "warning")
            return
        tag = current if current and any(engine.tag == current for engine in engines) else engines[0].tag
        self.engine_tag.set_status(f"引擎 {tag}", "accent")

    def _set_status(self, text: str) -> None:
        """状态统一写到窗口底部状态栏（归属画图页，切走再切回仍可见）。"""
        self.state.page_status("paint", text)

    def refresh_status(self) -> None:
        """刷新服务状态标签。"""
        state, alive = _llmpaint_server.status()
        self.service_tag.set_status(*service_label(state, alive))

    # ------------------------------------------------------------------ #
    # 参数联动
    # ------------------------------------------------------------------ #
    def _sync_preset(self) -> None:
        """宽高变化时把预设下拉切到匹配项（没有匹配则「自定义」）。"""
        width, height = self.width.value(), self.height.value()
        index = next(
            (row for row in range(self.preset_combo.count()) if self.preset_combo.itemData(row) == (width, height)),
            0,
        )
        if index == self.preset_combo.currentIndex():
            return
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(index)
        self.preset_combo.blockSignals(False)

    def _apply_preset(self) -> None:
        data = self.preset_combo.currentData()
        if not isinstance(data, tuple):
            return
        width, height = data
        self.width.setValue(int(width))
        self.height.setValue(int(height))

    def _update_preview(self) -> None:
        if not hasattr(self, "preview_label"):
            return
        self.preview_label.setText(command_preview(self._collect_settings()))

    def _refresh_output_label(self) -> None:
        """输出目录不再占一行；写入按钮 tooltip，保留可发现性。"""
        path = output_dir(self._settings)
        self.output_button.setToolTip(f"打开输出目录：{path}")
        self.save_to_button.setToolTip(f"保存到…（当前：{path}）")

    # ------------------------------------------------------------------ #
    # 出图
    # ------------------------------------------------------------------ #
    def generate(self) -> None:
        """校验设置并提交出图任务（必要时先启动 / 重启服务）。"""
        if self._running:
            return
        settings = self._collect_settings()
        try:
            _llmpaint.validate_components(components_from_settings(settings))
            _llmpaint.validate_request(request_from_settings(settings))
        except ServeError as exc:
            self._fail(str(exc))
            return
        self._settings = settings
        self._persist()
        self._running = True
        self._progress_ok = False
        self._start_flag = {"value": False}
        self._set_running(True)
        self.progress.setRange(0, 0)
        self._set_status("正在准备模型…")
        self._poll.start()
        request = request_from_settings(settings)
        self.state.log(
            f"画图请求：{request.width}×{request.height} · {request.steps} 步 · {request.sampler or '默认采样器'}"
        )
        submit_daemon(
            self._generate_work,
            settings,
            self._start_flag,
            on_done=self._generated,
            on_error=self._fail,
            on_finished=self._generation_finished,
        )

    @staticmethod
    def _generate_work(settings: dict[str, Any], start_flag: dict[str, bool]) -> tuple[list[Path], bool]:
        """后台任务：确保服务按当前指纹运行 → 调接口 → 保存图片。"""
        components = components_from_settings(settings)
        request = request_from_settings(settings)
        plan = _llmpaint_server.make_paint_plan(
            components,
            port=int(settings["port"]),
            engine_tag=_engine_tag(settings),
        )
        state, alive = _llmpaint_server.status()
        reused = alive and state is not None and state.fingerprint == _llmpaint_server.fingerprint(plan)
        started = False
        if not reused:
            if not bool(settings.get("auto_start")):
                raise ServeError("画图服务未按当前模型/参数运行：先点「启动」，或勾选「生成前自动启动」")
            if state is not None and alive:
                _llmpaint_server.stop_paint()
            _llmpaint_server.start_paint(plan)
            started = True
            start_flag["value"] = True
        images = _llmpaint_server.txt2img(plan.host, plan.port, _llmpaint.build_payload(request))
        return _llmpaint.save_images(images, output_dir(settings), seed=request.seed), started

    def _generated(self, result: tuple[list[Path], bool]) -> None:
        if self._closed:
            return
        paths, started = result
        self._auto_started = self._auto_started or started or bool(self._start_flag.get("value"))
        self._progress_ok = True
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self._set_status(f"已生成 {len(paths)} 张 · {paths[0].parent}")
        self._show_preview(paths[0])
        self._refresh_gallery()
        self.refresh_status()
        self.state.log("画图完成：" + "、".join(str(path) for path in paths))

    def _generation_finished(self) -> None:
        self._running = False
        self._poll.stop()
        if self._closed:
            return
        self._set_running(False)
        if not self._progress_ok:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)

    def _show_preview(self, path: Path) -> None:
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.preview.setText(f"已保存：{path}")
            return
        # 按预览区实际尺寸缩放（至少 320px），窗口变大时图片跟着变大。
        limit = max(320, min(self.preview.width() - 24, self.preview.height() - 24, 720))
        self.preview.setPixmap(
            pixmap.scaled(
                limit,
                limit,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _fail(self, message: str) -> None:
        self.state.log(f"画图失败：{message}")
        if self._closed:
            return
        self._set_status("失败：" + message.splitlines()[0])
        QMessageBox.critical(self, "画图失败", message)

    def _set_running(self, running: bool) -> None:
        self.generate_button.setEnabled(not running)
        self.interrupt_button.setEnabled(running)
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(not running)

    # ------------------------------------------------------------------ #
    # 进度 / 中断
    # ------------------------------------------------------------------ #
    def _refresh_progress(self) -> None:
        if not self._running or self._polling or self._closed:
            return
        self._polling = True
        submit_daemon(
            _llmpaint_server.fetch_progress,
            _llmpaint_server.DEFAULT_HOST,
            self.port.value(),
            on_done=self._progress_ready,
            on_error=lambda _message: None,
            on_finished=self._poll_finished,
        )

    def _progress_ready(self, payload: Any) -> None:
        if self._closed:
            return
        value = _llmpaint_server.progress_value(payload)
        text = format_progress(payload)
        if value is None or not text:
            return
        self.progress.setRange(0, 100)
        self.progress.setValue(int(value * 100))
        self._set_status(f"生成中 … {text}")

    def _poll_finished(self) -> None:
        self._polling = False

    def _interrupt(self) -> None:
        if not self._running:
            return
        self._set_status("正在请求中断…")
        submit_daemon(
            _llmpaint_server.interrupt,
            _llmpaint_server.DEFAULT_HOST,
            self.port.value(),
            on_done=self._interrupted,
            on_error=self._interrupt_failed,
        )

    def _interrupted(self, ok: bool) -> None:
        if self._closed:
            return
        self._set_status("已请求中断" if ok else "服务不支持中断；请等待当前任务完成")

    def _interrupt_failed(self, message: str) -> None:
        if self._closed:
            return
        self._set_status(f"中断失败：{message}")

    # ------------------------------------------------------------------ #
    # 服务启停
    # ------------------------------------------------------------------ #
    def _start_service(self) -> None:
        settings = self._collect_settings()
        try:
            _llmpaint.validate_components(components_from_settings(settings))
        except ServeError as exc:
            self._fail(str(exc))
            return
        self._settings = settings
        self._persist()
        self._start_flag = {"value": False}
        self.start_button.setEnabled(False)
        self._set_status("正在启动服务…")
        submit_daemon(
            self._start_work,
            settings,
            self._start_flag,
            on_done=self._started_service,
            on_error=self._fail,
            on_finished=self._start_finished,
        )

    @staticmethod
    def _start_work(settings: dict[str, Any], start_flag: dict[str, bool]) -> bool:
        components = components_from_settings(settings)
        plan = _llmpaint_server.make_paint_plan(
            components,
            port=int(settings["port"]),
            engine_tag=_engine_tag(settings),
        )
        state, alive = _llmpaint_server.status()
        if alive and state is not None and state.fingerprint == _llmpaint_server.fingerprint(plan):
            return False
        if state is not None and alive:
            _llmpaint_server.stop_paint()
        _llmpaint_server.start_paint(plan)
        start_flag["value"] = True
        return True

    def _started_service(self, started: bool) -> None:
        if self._closed:
            return
        self._auto_started = self._auto_started or started
        self._set_status("服务已就绪" if started else "服务已在按当前参数运行")
        if started:
            self.state.log("已启动画图服务")
        self.refresh_status()

    def _start_finished(self) -> None:
        if self._closed:
            return
        self.start_button.setEnabled(True)

    def _stop_service(self) -> None:
        self.stop_button.setEnabled(False)
        self._set_status("正在停止服务…")
        submit_daemon(
            _llmpaint_server.stop_paint,
            on_done=self._stopped_service,
            on_error=self._fail,
            on_finished=self._stop_finished,
        )

    def _stopped_service(self, result: _llmpaint_server.PaintStopResult) -> None:
        self._auto_started = False
        if self._closed:
            return
        if result.stopped:
            self._set_status("服务已停止")
            self.state.log(f"已停止画图服务（pid={result.state.pid if result.state else '-'}）")
        else:
            self._set_status("服务未运行")
        self.refresh_status()

    def _stop_finished(self) -> None:
        if self._closed:
            return
        self.stop_button.setEnabled(True)

    # ------------------------------------------------------------------ #
    # 输出目录 / 图库 / 引擎安装
    # ------------------------------------------------------------------ #
    def _pick_output_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择出图目录", str(output_dir(self._settings)))
        if not chosen:
            return
        self._settings = parse_paint_settings({**self._settings, "output_dir": chosen})
        self._refresh_output_label()
        self._persist()
        self._refresh_gallery()
        self._set_status(f"输出目录已切换到 {chosen}")

    def _open_output_dir(self) -> None:
        target = output_dir(self._collect_settings())
        try:
            target.mkdir(parents=True, exist_ok=True)
            open_path(target)
        except OSError as exc:
            QMessageBox.critical(self, "打开失败", f"无法打开目录：{target}\n{exc}")

    def _refresh_gallery(self) -> None:
        while self.gallery_grid.count():
            item = self.gallery_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        paths = _llmpaint.gallery(output_dir(self._settings), limit=6)
        self.gallery_title.setVisible(bool(paths))
        for index, path in enumerate(paths):
            label = QLabel(self)
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                label.setPixmap(
                    pixmap.scaled(
                        96,
                        96,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            label.setToolTip(str(path))
            self.gallery_grid.addWidget(label, index // 3, index % 3)

    def _install_engine(self) -> None:
        from fcmd.apps.llm._llmw_qt_paint_engine import PaintEngineDialog

        dialog = PaintEngineDialog(self)
        dialog.installed.connect(self._engine_installed)
        dialog.exec()

    def _engine_installed(self, tag: str) -> None:
        if self._closed:
            return
        self.refresh_engine()
        self._set_status(f"画图引擎已就绪：{tag}")

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """关窗：停轮询、保存设置；本窗口自动拉起的服务异步停止，避免残留显存占用。"""
        if self._closed:
            return
        self._closed = True
        self._poll.stop()
        self._settings = self._collect_settings()
        self._persist()
        owns_service = self._auto_started or bool(self._start_flag.get("value"))
        self._auto_started = False
        if owns_service:
            submit_daemon(_llmpaint_server.stop_paint)
