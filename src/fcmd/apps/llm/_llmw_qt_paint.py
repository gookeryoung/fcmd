"""llmw 画图配置：sd.cpp 文生图的纯逻辑、服务调用与配置分区。

画图不再有独立页面：对话页勾选「画图」后用提示词出图；采样 / 输出嵌进对话页的
「模型与生成配置」窗口（:class:`PaintSampleSection`），引擎安装 / 默认引擎（:class:`PaintEngineCard`）放在设置页，
模型组件配置（:class:`PaintEngineSection`）放在运行页「画图」页签，与「对话 / 编程」
同构：加载方式方块选项、运行选项胶囊开关、高级参数折叠区、只读命令预览与自定义
启动参数（保存 / 填入）。资产扫描、组件配对、启动参数、请求载荷与出图保存都在
:mod:`fcmd.apps.llm._llmpaint` / ``_llmpaint_server`` / ``_llmpaint_engine``
（与 ``fcmd llmpaint`` 共用），这里只做设置解析、服务调用与 Qt 控件。

模块级函数都是纯逻辑（解析设置、构造组件 / 请求、命令预览），供测试直接断言；
:class:`PaintEngineSection` / :class:`PaintSampleSection` 与 :func:`generate_images` /
:func:`start_paint_service` 是 GUI / 服务胶水（``pragma: no cover``）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
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
    Card,
    ChoiceGroup,
    Collapsible,
    ElidedLabel,
    FormGrid,
    RowListWidget,
    SelectionRow,
    StatusTag,
    ToggleChip,
    button,
    hint_label,
    link_button,
)
from fcmd.gui.qt.workers import submit, submit_daemon

if TYPE_CHECKING:
    from collections.abc import Sequence


__all__ = [
    "MODES",
    "SAMPLERS",
    "SIZE_PRESETS",
    "PaintEngineCard",
    "PaintEngineSection",
    "PaintSampleSection",
    "command_preview",
    "components_from_settings",
    "default_settings",
    "format_extra_args",
    "format_progress",
    "generate_images",
    "model_roots",
    "output_dir",
    "parse_extra_args",
    "parse_paint_settings",
    "request_from_settings",
    "settings_summary",
    "size_preset",
    "start_paint_service",
    "startup_args_tail",
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

_MODE_KEYS = frozenset(key for key, _label in MODES)

MODEL_RUNNING_ROLE = int(Qt.ItemDataRole.UserRole) + 1
"""下拉项「运行中」标记：与运行页对话 / 编程的模型下拉共用同一角色。"""
MODEL_BASE_TEXT_ROLE = int(Qt.ItemDataRole.UserRole) + 2
"""下拉项基础文案：追回 / 去掉「✓ 已启动」后缀时以它为准。"""
_MODE_NOTES: dict[str, str] = {
    "auto": "按主模型家族自动判断 checkpoint / diffusion",
    "checkpoint": "单文件权重，用 -m 加载",
    "diffusion": "分离式权重，用 --diffusion-model 加载",
}
_MODE_TONES: dict[str, str] = {"auto": "accent", "checkpoint": "success", "diffusion": "warning"}


def _mode_options() -> list[tuple[str, str, str]]:
    """加载方式选项：(键, 标题, 说明)，供 ChoiceGroup 与回填校验共用。"""
    return [(key, label, _MODE_NOTES[key]) for key, label in MODES]


def _mode_key(value: str) -> str:
    """持久化值 → 选项键；未知值回落「自动」。"""
    return value if value in _MODE_KEYS else "auto"


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
_BOOL_KEYS = ("enabled", "auto_start", "offload_to_cpu", "vae_tiling", "diffusion_fa")
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
        "enabled": False,
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


def _base_components(
    settings: dict[str, Any],
    *,
    extra_args: Sequence[str] = (),
) -> _llmpaint.PaintComponents:
    """按页面设置构造组件集合；``extra_args`` 显式传入，便于构造不含额外参数的基线。"""
    return _llmpaint.PaintComponents(
        model=_path_value(settings.get("model")),
        vae=_path_value(settings.get("vae")),
        clip_l=_path_value(settings.get("clip_l")),
        clip_g=_path_value(settings.get("clip_g")),
        t5xxl=_path_value(settings.get("t5xxl")),
        llm=_path_value(settings.get("llm")),
        lora=_path_value(settings.get("lora")),
        mode=_mode_key(str(settings.get("mode") or "auto")),
        extra_args=tuple(extra_args),
        offload_to_cpu=bool(settings.get("offload_to_cpu")),
        vae_tiling=bool(settings.get("vae_tiling")),
        diffusion_fa=bool(settings.get("diffusion_fa")),
    )


def startup_args_tail(settings: dict[str, Any]) -> tuple[str, ...]:
    """去掉可执行文件与主模型参数（``-m`` / ``--diffusion-model``），保留其余启动参数。"""
    parsed = parse_paint_settings(settings)
    components = _base_components(parsed)
    argv = [
        "--listen-ip",
        _llmpaint_server.DEFAULT_HOST,
        "--listen-port",
        str(int(parsed.get("port") or _llmpaint_server.DEFAULT_PORT)),
        *_llmpaint.build_model_args(components),
    ]
    if components.model is not None:
        flag = "-m" if _llmpaint.effective_mode(components) == "checkpoint" else "--diffusion-model"
        if flag in argv:
            index = argv.index(flag)
            del argv[index : index + 2]
    return tuple(argv)


def components_from_settings(settings: dict[str, Any]) -> _llmpaint.PaintComponents:
    """按页面设置构造 sd-server 的模型组件集合；已填入的当前参数不二次追加。"""
    parsed = parse_paint_settings(settings)
    extra = tuple(parsed.get("extra_args") or ())
    if extra and extra == startup_args_tail(parsed):
        extra = ()
    return _base_components(parsed, extra_args=extra)


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
    parsed = parse_paint_settings(settings)
    port = int(parsed.get("port") or _llmpaint_server.DEFAULT_PORT)
    argv = [
        "sd-server",
        "--listen-ip",
        _llmpaint_server.DEFAULT_HOST,
        "--listen-port",
        str(port),
        *_llmpaint.build_model_args(components_from_settings(parsed)),
    ]
    return format_command(argv)


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


def settings_summary(settings: dict[str, Any]) -> str:
    """composer 里的配置摘要（模型名 · 尺寸 · 步数 · 采样器）。

    参数全部收进配置弹窗后，页面只靠这一行小字说明「下次生成用什么」；
    模型未选时直接提示去弹窗里选，不隐去当前尺寸 / 步数。
    """
    model = str(settings.get("model") or "")
    name = Path(model).name if model.strip() else "未选模型"
    parts = [name, f"{int(settings.get('width') or 512)}×{int(settings.get('height') or 512)}"]
    parts.append(f"{int(settings.get('steps') or 25)} 步")
    sampler = str(settings.get("sampler") or "").strip()
    if sampler:
        parts.append(sampler)
    batch = int(settings.get("batch") or 1)
    if batch > 1:
        parts.append(f"批量 {batch}")
    return " · ".join(parts)


def generate_images(settings: dict[str, Any]) -> tuple[list[Path], bool]:
    """确保 sd-server 按当前配置运行 → 出图 → 保存（阻塞，供线程池调用）。

    返回（图片路径, 是否本次新启动服务）。``auto_start`` 关闭且服务指纹不匹配时
    直接报错，把「先启动」的决定权留给用户，而不是偷偷重启服务。
    """
    components = components_from_settings(settings)
    request = request_from_settings(settings)
    plan = _llmpaint_server.make_paint_plan(
        components,
        port=int(settings.get("port") or _llmpaint_server.DEFAULT_PORT),
        engine_tag=_engine_tag(settings),
    )
    state, alive = _llmpaint_server.status()
    reused = alive and state is not None and state.fingerprint == _llmpaint_server.fingerprint(plan)
    started = False
    if not reused:
        if not bool(settings.get("auto_start")):
            raise ServeError("画图服务未按当前配置运行：在对话配置里勾选「生成前自动启动」后再生成")
        if state is not None and alive:
            _llmpaint_server.stop_paint()
        _llmpaint_server.start_paint(plan)
        started = True
    images = _llmpaint_server.txt2img(plan.host, plan.port, _llmpaint.build_payload(request))
    return _llmpaint.save_images(images, output_dir(settings), seed=request.seed), started


def start_paint_service(settings: dict[str, Any]) -> bool:
    """按当前配置启动 / 复用 sd-server；返回是否本次新启动（阻塞）。"""
    plan = _llmpaint_server.make_paint_plan(
        components_from_settings(settings),
        port=int(settings.get("port") or _llmpaint_server.DEFAULT_PORT),
        engine_tag=_engine_tag(settings),
    )
    state, alive = _llmpaint_server.status()
    if alive and state is not None and state.fingerprint == _llmpaint_server.fingerprint(plan):
        return False
    if state is not None and alive:
        _llmpaint_server.stop_paint()
    _llmpaint_server.start_paint(plan)
    return True


class PaintEngineSection(QWidget):  # pragma: no cover - Qt 控件
    """画图启动参数：加载方式方块、运行选项胶囊、模型组件、命令预览与自定义参数。

    布局与运行页「对话 / 编程」的启动参数卡同构：服务端口 → 加载方式（ChoiceGroup）
    → 运行选项（ToggleChip）→ 模型组件（Collapsible + 下拉）→ 只读命令预览
    （``QPlainTextEdit#Log``）→ 自定义启动参数 + 「保存参数 / 填入当前参数」。

    每次改动发 :attr:`changed`，由运行页延迟写盘到 ``ui/llmw.json`` 的 ``paint``
    字段；「保存参数」立即发 :attr:`saveRequested`，由运行页马上落盘。资产扫描在
    后台线程完成，扫描期间沿用已保存的路径，避免误清空模型选择。
    """

    changed = Signal()
    assetsLoaded = Signal()
    statusRequested = Signal(str)
    saveRequested = Signal()

    _KEYS = (
        "port",
        "auto_start",
        "mode",
        "model",
        "vae",
        "clip_l",
        "clip_g",
        "t5xxl",
        "llm",
        "lora",
        "offload_to_cpu",
        "vae_tiling",
        "diffusion_fa",
        "extra_args",
    )

    def __init__(
        self,
        settings: dict[str, Any],
        parent: QWidget | None = None,
        *,
        show_model: bool = True,
        model_combo: QComboBox | None = None,
    ) -> None:
        super().__init__(parent)
        self._show_model = show_model
        self._settings = parse_paint_settings(settings)
        self._assets = _llmpaint.PaintAssets()
        self._assets_loaded = False
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        service_form = FormGrid(self)
        self.port = QSpinBox(self)
        self.port.setRange(1, 65535)
        self.port.setToolTip("sd-server 监听端口（默认 1234，避开 llama-server 的 8080）")
        service_form.add_row("服务端口", self.port)
        layout.addWidget(service_form)

        layout.addWidget(QLabel("加载方式", self))
        self.mode_choice = ChoiceGroup(
            _mode_options(),
            current="auto",
            columns=3,
            tones=_MODE_TONES,
            parent=self,
        )
        layout.addWidget(self.mode_choice)

        layout.addWidget(QLabel("运行选项（可多选）", self))
        options_row = QHBoxLayout()
        options_row.setContentsMargins(0, 0, 0, 0)
        options_row.setSpacing(6)
        self.auto_start = ToggleChip(
            "自动启动",
            icon="run",
            tooltip="生成前自动启动 / 重启服务：模型或参数变化时按新指纹重启（FLUX 等大模型首次加载较慢）",
            parent=self,
        )
        self.offload_to_cpu = ToggleChip(
            "CPU 卸载",
            icon="models",
            tooltip="--offload-to-cpu：显存不足时把部分权重放到内存（更慢）",
            parent=self,
        )
        self.vae_tiling = ToggleChip(
            "VAE 分块",
            icon="images",
            tooltip="--vae-tiling：大图解码时降低显存峰值",
            parent=self,
        )
        self.diffusion_fa = ToggleChip(
            "Diffusion FA",
            icon="convert",
            tooltip="--diffusion-fa：启用 flash attention 加速",
            parent=self,
        )
        for chip in (self.auto_start, self.offload_to_cpu, self.vae_tiling, self.diffusion_fa):
            options_row.addWidget(chip)
        options_row.addStretch(1)
        layout.addLayout(options_row)

        # 模型名 / 大小 / 家族已在主模型下拉与命令预览里，不再单独占一行文字；
        # 这里只留「自动配对 / 下载缺失组件」两个动作。
        family_row = QHBoxLayout()
        family_row.setContentsMargins(0, 0, 0, 0)
        family_row.setSpacing(8)
        family_row.addStretch(1)
        self.suggest_button = button("自动配对", kind="ghost", tooltip="按主模型家族填满 VAE / 文本编码器槽位")
        family_row.addWidget(self.suggest_button)
        self.download_button = button("下载缺失组件…", kind="ghost")
        self.download_button.setToolTip("从 Comfy-Org / 官方仓库直接下载缺失的 VAE / 文本编码器")
        self.download_button.setVisible(False)
        self.download_button.clicked.connect(self._download_missing)
        family_row.addWidget(self.download_button)
        layout.addLayout(family_row)

        self.advanced = Collapsible("高级参数（模型组件，留空自动配对）", expanded=False, parent=self)
        components_form = FormGrid(self.advanced.content)
        self.model_combo = model_combo if model_combo is not None else QComboBox(self.advanced.content)
        if show_model:
            components_form.add_row("主模型", self.model_combo)
        self.role_combos: dict[str, QComboBox] = {}
        for role, label in ROLE_COMBOS:
            combo = QComboBox(self.advanced.content)
            self.role_combos[role] = combo
            components_form.add_row(label, combo)
        self.advanced.body.addWidget(components_form)
        layout.addWidget(self.advanced)

        layout.addWidget(QLabel("启动命令预览", self))
        self.command_preview = QPlainTextEdit(self)
        self.command_preview.setObjectName("Log")
        self.command_preview.setReadOnly(True)
        self.command_preview.setFixedHeight(64)
        layout.addWidget(self.command_preview)

        layout.addWidget(QLabel("自定义启动参数", self))
        self.extra_args_edit = QPlainTextEdit(self)
        self.extra_args_edit.setFixedHeight(56)
        self.extra_args_edit.setPlaceholderText("每行或空格分隔，例如：--clip-on-cpu --vae-tiling")
        self.extra_args_edit.setToolTip("原样追加到 sd-server；按空白分隔，含空格的值用双引号包裹")
        layout.addWidget(self.extra_args_edit)
        params_actions = QHBoxLayout()
        params_actions.setContentsMargins(0, 0, 0, 0)
        params_actions.setSpacing(6)
        self.save_params_button = button("保存参数")
        self.fill_params_button = button("填入当前参数", tooltip="把监听 / 组件 / 开关参数写进上面的自定义参数框")
        params_actions.addWidget(self.save_params_button)
        params_actions.addWidget(self.fill_params_button)
        params_actions.addStretch(1)
        layout.addLayout(params_actions)

        self._wire()
        self._apply()
        self.refresh_assets()

    # ------------------------------------------------------------------ #
    # 设置读写
    # ------------------------------------------------------------------ #
    def settings(self) -> dict[str, Any]:
        """只返回引擎相关字段：与对话页的采样设置合并后才是完整画图配置。"""
        self._settings.update(
            {
                "port": self.port.value(),
                "auto_start": self.auto_start.isChecked(),
                "mode": _mode_key(self.mode_choice.key()),
                "model": self._combo_value(self.model_combo, "model"),
                "offload_to_cpu": self.offload_to_cpu.isChecked(),
                "vae_tiling": self.vae_tiling.isChecked(),
                "diffusion_fa": self.diffusion_fa.isChecked(),
                "extra_args": parse_extra_args(self.extra_args_edit.toPlainText()),
            }
        )
        for role, _label in ROLE_COMBOS:
            self._settings[role] = self._combo_value(self.role_combos[role], role)
        return {key: self._settings[key] for key in self._KEYS}

    def set_settings(self, settings: dict[str, Any]) -> None:
        """外部改写设置后回填控件，不回发 ``changed``。"""
        self._settings.update(parse_paint_settings(settings))
        self._apply()

    def _apply(self) -> None:
        settings = self._settings
        self._loading = True
        try:
            self.port.setValue(int(settings["port"]))
            self.auto_start.setChecked(bool(settings["auto_start"]))
            self.offload_to_cpu.setChecked(bool(settings["offload_to_cpu"]))
            self.vae_tiling.setChecked(bool(settings["vae_tiling"]))
            self.diffusion_fa.setChecked(bool(settings["diffusion_fa"]))
            self.mode_choice.set_key(_mode_key(str(settings["mode"])))
            self.extra_args_edit.setPlainText(format_extra_args(tuple(settings["extra_args"])))
            self._apply_component_selection()
            self._update_preview()
            self._refresh_component_actions()
        finally:
            self._loading = False

    def _combo_value(self, combo: QComboBox, key: str) -> str:
        data = combo.currentData()
        if isinstance(data, str):
            return data
        return "" if self._assets_loaded else str(self._settings.get(key) or "")

    def _wire(self) -> None:
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self.suggest_button.clicked.connect(lambda: self._suggest(only_empty=False))
        self.save_params_button.clicked.connect(self._save_params)
        self.fill_params_button.clicked.connect(self._fill_params)
        self.mode_choice.changed.connect(lambda _key: self._on_changed())
        for signal in (
            self.port.valueChanged,
            self.auto_start.toggled,
            self.offload_to_cpu.toggled,
            self.vae_tiling.toggled,
            self.diffusion_fa.toggled,
            self.extra_args_edit.textChanged,
        ):
            signal.connect(self._on_changed)
        for combo in self.role_combos.values():
            combo.currentIndexChanged.connect(self._on_changed)

    def _on_changed(self, *_args: Any) -> None:
        if self._loading:
            return
        self._update_preview()
        self._refresh_component_actions()
        self.changed.emit()

    # ------------------------------------------------------------------ #
    # 资产
    # ------------------------------------------------------------------ #
    def refresh_assets(self) -> None:
        """后台重新扫描扩散模型与组件（模型目录变化时由窗口调用）。"""
        submit(
            lambda: _llmpaint.scan_assets(model_roots(self._settings)),
            on_done=self._apply_assets,
            on_error=self._assets_failed,
        )

    def _assets_failed(self, message: str) -> None:
        """扫描失败进底部状态栏（不再在参数卡里占一行文字）。"""
        self.statusRequested.emit(f"扫描模型失败：{message}")

    def _apply_assets(self, assets: _llmpaint.PaintAssets) -> None:
        self._assets = assets
        self._assets_loaded = True
        self._fill_combo(
            self.model_combo,
            [asset.path for asset in assets.main_models()],
            placeholder=None,
            show_role=False,
        )
        for role, combo in self.role_combos.items():
            self._fill_combo(combo, [asset.path for asset in assets.by_role(role)], placeholder="（不指定）")
        saved_model = str(self._settings.get("model") or "")
        if saved_model and self.model_combo.findData(saved_model) < 0:
            # 已保存的主模型不在本次扫描结果里（目录被移除 / 扫描根不含它）时补一条，
            # 避免任何一次信号触发都把 model 回落成空值导致无法启动服务。
            self._add_combo_item(self.model_combo, Path(saved_model))
        if not saved_model and self.model_combo.count() > 0:
            self.model_combo.setCurrentIndex(0)
        self._apply_component_selection()
        self._update_preview()
        self._refresh_component_actions()
        self.assetsLoaded.emit()

    def _fill_combo(
        self,
        combo: QComboBox,
        paths: Sequence[Path],
        *,
        placeholder: str | None,
        show_role: bool = True,
    ) -> None:
        """重建下拉：``placeholder`` 非空时首项为占位（主模型不再插「未找到…」占位），

        其余按「文件名 · 角色」列出；保留原选中项。
        """
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        if placeholder is not None:
            combo.addItem(placeholder, "")
        for path in paths:
            asset = self._assets.get(path)
            suffix = f" · {asset.role_label}" if asset is not None and show_role else ""
            label = f"{path.name}{suffix}"
            combo.addItem(label, str(path))
            combo.setItemData(combo.count() - 1, label, MODEL_BASE_TEXT_ROLE)
            detail = f"{path}\n{human_size(asset.size)}" if asset is not None else str(path)
            combo.setItemData(combo.count() - 1, detail, Qt.ItemDataRole.ToolTipRole)
        index = combo.findData(previous) if isinstance(previous, str) and previous else -1
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _add_combo_item(self, combo: QComboBox, path: Path, *, show_role: bool = True) -> None:
        """给下拉补一条自定义路径（保留已保存但不在扫描结果里的选择）。"""
        combo.blockSignals(True)
        asset = self._assets.get(path)
        suffix = f" · {asset.role_label}" if asset is not None and show_role else ""
        label = f"{path.name}{suffix}"
        combo.addItem(label, str(path))
        combo.setItemData(combo.count() - 1, label, MODEL_BASE_TEXT_ROLE)
        combo.setItemData(combo.count() - 1, str(path), Qt.ItemDataRole.ToolTipRole)
        combo.setCurrentIndex(combo.count() - 1)
        combo.blockSignals(False)

    def set_running_model(self, model: str) -> None:
        """标记正在运行的扩散模型：追加「✓ 已启动」并置绿（与对话 / 编程同款）。

        ``model`` 为空表示没有登记的画图服务；有服务时按路径键（Windows 大小写
        不敏感）匹配下拉项，运行项在弹层里绘制绿色文字与边框，当前选中项把
        下拉框本身置绿（``ModelCombo[running="true"]``）。
        """
        key = _llmconfig.path_key(Path(model)) if model.strip() else ""
        combo = self.model_combo
        for index in range(combo.count()):
            data = combo.itemData(index)
            base = combo.itemData(index, MODEL_BASE_TEXT_ROLE)
            running = bool(key) and isinstance(data, str) and bool(data) and _llmconfig.path_key(Path(data)) == key
            combo.setItemData(index, running, MODEL_RUNNING_ROLE)
            if isinstance(base, str) and base:
                combo.setItemText(index, f"{base} · ✓ 已启动" if running else base)
        selected = combo.currentData()
        running_selected = (
            bool(key) and isinstance(selected, str) and bool(selected) and (_llmconfig.path_key(Path(selected)) == key)
        )
        combo.setProperty("running", "true" if running_selected else "false")
        combo.style().unpolish(combo)
        combo.style().polish(combo)

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
        """主模型变化：给空槽位补默认组件（大小等信息留在下拉项的 tooltip 里）。"""
        self._suggest(only_empty=True)
        self._on_changed()

    def _suggest(self, *, only_empty: bool) -> None:
        """按家族槽位给出建议组件；``only_empty`` 时只补空槽位。

        主模型还没选时，「自动配对」先把资产里第一个扩散模型填上，再按家族补
        VAE / 文本编码器——否则配对点了没反应，启动服务仍报「请选择主模型」。
        """
        path = self._combo_path(self.model_combo)
        suggestion = _llmpaint.suggest_components(self._assets, path)
        if suggestion.model is not None and path is None:
            self._select_path(self.model_combo, str(suggestion.model))
            path = suggestion.model
        for role, combo in self.role_combos.items():
            value = getattr(suggestion, role, None)
            if value is None:
                continue
            if only_empty and self._combo_path(combo) is not None:
                continue
            self._select_path(combo, str(value))
        self._on_changed()

    def _missing_roles(self) -> tuple[str, ...]:
        """当前主模型家族还缺哪些必需组件（已选 / 已用额外参数补齐的不算）。"""
        settings = {**self._settings, **self.settings()}
        components = components_from_settings(parse_paint_settings(settings))
        return _llmpaint.missing_required_slots(components)

    def _refresh_component_actions(self) -> None:
        """缺组件且能自动下载时露出「下载缺失组件…」，否则收起。"""
        if self._loading:
            return
        components = components_from_settings(parse_paint_settings({**self._settings, **self.settings()}))
        missing = self._missing_roles()
        family = _llmpaint.detect_family(components.model) if components.model is not None else ""
        roles = tuple(role for role in missing if _llmpaint.companion_repos(family, role))
        if not roles:
            self.download_button.setVisible(False)
            return
        labels = {"vae": "VAE", "llm": "文本编码器"}
        names = "、".join(labels.get(role, role) for role in roles)
        self.download_button.setText(f"下载缺失的 {names}…")
        self.download_button.setToolTip(f"从 Comfy-Org / 官方仓库下载：{names}")
        self.download_button.setVisible(True)

    def _download_missing(self) -> None:
        """后台下载缺失组件到模型目录，完成后重扫资产并自动配对。"""
        components = components_from_settings(parse_paint_settings({**self._settings, **self.settings()}))
        missing = self._missing_roles()
        family = _llmpaint.detect_family(components.model) if components.model is not None else ""
        roles = tuple(role for role in missing if _llmpaint.companion_repos(family, role))
        if not roles:
            return
        labels = {"vae": "VAE", "llm": "文本编码器"}
        names = "、".join(labels.get(role, role) for role in roles)
        self.download_button.setEnabled(False)
        self.statusRequested.emit(f"正在下载 {names}…（Comfy-Org / 官方仓库）")
        dest = model_roots(self._settings)[0]
        submit_daemon(
            _llmpaint.download_companion_weights,
            family,
            roles,
            dest,
            on_done=self._download_done,
            on_error=self._download_failed,
            on_finished=self._download_finished,
        )

    def _download_done(self, paths: tuple[Path, ...]) -> None:
        names = "、".join(path.name for path in paths)
        self.statusRequested.emit(f"已下载组件：{names}（正在重扫模型）")
        self.refresh_assets()

    def _download_failed(self, message: str) -> None:
        self.statusRequested.emit(f"下载组件失败：{message.splitlines()[0]}")

    def _download_finished(self) -> None:
        self.download_button.setEnabled(True)
        self._refresh_component_actions()

    def missing_model_hint(self) -> str:
        """没有可用主模型时的提示（含未下载完成的文件）；有模型时返回空串。"""
        if self._assets.main_models():
            return ""
        pending = _llmpaint.pending_downloads(model_roots(self._settings))
        if pending:
            names = "、".join(path.name for path in pending[:3])
            return f"未找到可用的扩散主模型；发现未下载完成的文件：{names}。去「模型」页继续下载后再启动服务。"
        return "未找到扩散主模型：去「模型」页下载扩散模型，或在设置里登记模型目录后再启动服务。"

    def _update_preview(self) -> None:
        """刷新命令预览：非回填阶段合并实时控件值，避免「先刷预览、后同步设置」的滞后。"""
        settings = self._settings if self._loading else {**self._settings, **self.settings()}
        self.command_preview.setPlainText(command_preview(parse_paint_settings(settings)))

    def _save_params(self) -> None:
        """「保存参数」：把当前启动参数交给运行页立即落盘（画图参数全局只有一份）。"""
        self.saveRequested.emit()

    def _fill_params(self) -> None:
        """「填入当前参数」：写进自定义参数框；与预览相同的内容不会二次追加。"""
        settings = {**self._settings, **self.settings()}
        if not str(settings.get("model") or "").strip():
            self.statusRequested.emit("参数未就绪：请先选择主模型")
            return
        self.extra_args_edit.setPlainText(format_extra_args(startup_args_tail(settings)))
        self._update_preview()
        self.statusRequested.emit("已填入当前启动参数")


class PaintSampleSection(QWidget):  # pragma: no cover - Qt 控件
    """单次出图的采样参数与输出目录。

    嵌进对话页的「模型与生成配置」窗口：引擎 / 模型组件在运行页，这里只决定
    「这次怎么画、存到哪」。改动发 :attr:`changed`，由对话页延迟写盘。
    """

    changed = Signal()

    _KEYS = ("width", "height", "steps", "cfg_scale", "sampler", "scheduler", "seed", "batch", "output_dir")

    def __init__(self, settings: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = parse_paint_settings(settings)
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        params_form = FormGrid(self)
        self.preset_combo = QComboBox(params_form)
        self.preset_combo.addItem("自定义", None)
        for label, preset_width, preset_height in SIZE_PRESETS:
            self.preset_combo.addItem(label, (preset_width, preset_height))
        params_form.add_row("尺寸预设", self.preset_combo)

        size_row = QWidget(params_form)
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
        size_layout.addWidget(self.width, 1)
        size_layout.addWidget(hint_label("×", wrap=False))
        size_layout.addWidget(self.height, 1)
        params_form.add_row("宽 × 高", size_row)

        self.steps = QSpinBox(params_form)
        self.steps.setRange(1, 150)
        self.cfg = QDoubleSpinBox(params_form)
        self.cfg.setRange(0.0, 30.0)
        self.cfg.setSingleStep(0.5)
        self.cfg.setDecimals(1)
        params_form.add_pair("步数", self.steps, "CFG 引导", self.cfg)

        self.sampler = QComboBox(params_form)
        self.sampler.setEditable(True)
        self.sampler.addItems(list(SAMPLERS))
        self.scheduler = QLineEdit(params_form)
        self.scheduler.setPlaceholderText("留空用引擎默认")
        params_form.add_pair("采样器", self.sampler, "调度器", self.scheduler)

        seed_row = QWidget(params_form)
        seed_layout = QHBoxLayout(seed_row)
        seed_layout.setContentsMargins(0, 0, 0, 0)
        seed_layout.setSpacing(6)
        self.seed = QSpinBox(seed_row)
        self.seed.setRange(0, 2_147_483_647)
        self.random_seed = QCheckBox("随机", seed_row)
        self.random_seed.setToolTip("勾选时使用 -1（每次随机），取消后可固定种子复现")
        seed_layout.addWidget(self.seed)
        seed_layout.addWidget(self.random_seed)
        seed_layout.addStretch(1)
        self.batch = QSpinBox(params_form)
        self.batch.setRange(1, 8)
        params_form.add_pair("种子", seed_row, "批量", self.batch)
        layout.addWidget(params_form)

        output_row = QHBoxLayout()
        output_row.setContentsMargins(0, 0, 0, 0)
        output_row.setSpacing(8)
        self.output_label = ElidedLabel("", parent=self)
        self.output_label.setObjectName("Hint")
        output_row.addWidget(self.output_label, 1)
        self.save_to_button = button("更改…", kind="ghost")
        self.output_button = button("打开目录", kind="ghost")
        output_row.addWidget(self.save_to_button)
        output_row.addWidget(self.output_button)
        layout.addLayout(output_row)

        self._wire()
        self._apply()

    # ------------------------------------------------------------------ #
    # 设置读写
    # ------------------------------------------------------------------ #
    def settings(self) -> dict[str, Any]:
        """只返回采样 / 输出字段：与运行页的引擎设置合并后才是完整画图配置。"""
        self._loading = True
        try:
            self._settings.update(
                {
                    "width": self.width.value(),
                    "height": self.height.value(),
                    "steps": self.steps.value(),
                    "cfg_scale": self.cfg.value(),
                    "sampler": self.sampler.currentText().strip(),
                    "scheduler": self.scheduler.text().strip(),
                    "seed": -1 if self.random_seed.isChecked() else self.seed.value(),
                    "batch": self.batch.value(),
                }
            )
        finally:
            self._loading = False
        return {key: self._settings[key] for key in self._KEYS}

    def set_settings(self, settings: dict[str, Any]) -> None:
        """外部改写设置后回填控件，不回发 ``changed``。"""
        self._settings.update(parse_paint_settings(settings))
        self._apply()

    def _apply(self) -> None:
        settings = self._settings
        self._loading = True
        try:
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
            self._sync_preset()
            self._refresh_output_label()
        finally:
            self._loading = False

    def _wire(self) -> None:
        self.random_seed.toggled.connect(self.seed.setDisabled)
        self.width.valueChanged.connect(self._sync_preset)
        self.height.valueChanged.connect(self._sync_preset)
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        self.save_to_button.clicked.connect(self._pick_output_dir)
        self.output_button.clicked.connect(self._open_output_dir)
        for signal in (
            self.width.valueChanged,
            self.height.valueChanged,
            self.steps.valueChanged,
            self.cfg.valueChanged,
            self.seed.valueChanged,
            self.random_seed.toggled,
            self.batch.valueChanged,
            self.sampler.currentTextChanged,
            self.scheduler.textChanged,
        ):
            signal.connect(self._on_changed)

    def _on_changed(self, *_args: Any) -> None:
        if self._loading:
            return
        self.changed.emit()

    # ------------------------------------------------------------------ #
    # 联动与输出
    # ------------------------------------------------------------------ #
    def _sync_preset(self) -> None:
        """宽高变化时把预设下拉切到匹配项（没有匹配则「自定义」）。"""
        index = next(
            (
                row
                for row in range(self.preset_combo.count())
                if self.preset_combo.itemData(row) == (self.width.value(), self.height.value())
            ),
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

    def _refresh_output_label(self) -> None:
        path = output_dir(self._settings)
        self.output_label.setText(f"输出目录：{path}")
        self.output_button.setToolTip(f"打开输出目录：{path}")
        self.save_to_button.setToolTip(f"更改输出目录（当前：{path}）")

    def _pick_output_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择出图目录", str(output_dir(self._settings)))
        if not chosen:
            return
        self._settings["output_dir"] = chosen
        self._refresh_output_label()
        self.changed.emit()

    def _open_output_dir(self) -> None:
        target = output_dir(self._settings)
        try:
            target.mkdir(parents=True, exist_ok=True)
            open_path(target)
        except OSError as exc:
            QMessageBox.critical(self, "打开失败", f"无法打开目录：{target}\n{exc}")


class _PaintEngineRow(SelectionRow, QFrame):  # pragma: no cover - Qt 控件
    """已安装 sd.cpp 引擎行：单行紧凑布局（标签 + 可执行文件 + 默认 / 后端）。

    画图 / 视频 / 音频共用同一个引擎目录与同一个默认 tag，行内只有「设为默认」。
    """

    useRequested = Signal(str)

    def __init__(
        self,
        engine: _llmpaint_engine.SdEngine,
        *,
        current: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.setObjectName("EngineRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("selected", "false")
        self.setToolTip(f"{engine.tag}\n{engine.exe}\n后端：{engine.backend or '-'}")

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 10, 4)
        row.setSpacing(8)

        name = QLabel(_llmpaint_engine.engine_version(engine.tag), self)
        name.setObjectName("EngineItemName")
        name.setMaximumWidth(220)
        name.setToolTip(f"引擎 tag：{engine.tag}")
        row.addWidget(name, 0)

        exe = ElidedLabel(str(engine.exe), mode=Qt.TextElideMode.ElideMiddle, parent=self)
        exe.setObjectName("Hint")
        row.addWidget(exe, 1)

        self.use_button = None
        if current:
            mark = StatusTag("默认", tone="success", parent=self)
            mark.setToolTip("画图 / 视频 / 音频共用该引擎")
            row.addWidget(mark, 0)
        else:
            self.use_button = button("设为默认")
            self.use_button.setToolTip("把该引擎设为默认 sd.cpp 引擎（画图 / 视频 / 音频共用）")
            self.use_button.clicked.connect(lambda: self.useRequested.emit(self.engine.tag))
            row.addWidget(self.use_button, 0)

        backend_tag = _llmpaint_engine.engine_backend(engine)
        label = _llmpaint_engine.variant_label(backend_tag) if backend_tag else "sd.cpp"
        tone = "muted" if backend_tag in {"", "cpu"} else "accent"
        backend = StatusTag(label, tone=tone, parent=self)
        backend.setToolTip(f"构建后端：{backend_tag or '未标注'}")
        row.addWidget(backend, 0)
        self.setMinimumHeight(self.fontMetrics().height() + 14)


class PaintEngineCard(Card):  # pragma: no cover - Qt 控件
    """sd.cpp 引擎卡片：画图 / 视频 / 音频共用一张卡、一个引擎目录、一个默认 tag。

    标题行右侧「刷新」、目录行右侧动作按钮、已安装引擎一律用紧凑单行
    （标签 + 可执行文件 + 「默认 / 设为默认」+ 后端胶囊），列表高度随内容伸缩、
    超过 6 行才滚动，空态由提示行承接。安装走 ``PaintEngineDialog``（版本 / 变体
    下载、本地 zip / 目录导入）。

    「设为默认」写 ``current.json``，三种用途都按它启动，不再有按用途的覆盖 key。
    """

    changed = Signal()
    """默认引擎或已安装列表变化，设置页据此同步刷新其它同目录卡片。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "画图/视频/音频引擎(sd.cpp)",
    ) -> None:
        super().__init__(title, parent)
        self.refresh_button = link_button("刷新", tooltip="重新读取已安装 sd.cpp 引擎与默认引擎")
        self.add_title_action(self.refresh_button)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(hint_label("引擎目录", wrap=False))
        self.root_label = ElidedLabel(str(_llmconfig.paint_engines_root()), parent=self)
        self.root_label.setObjectName("Hint")
        row.addWidget(self.root_label, 1)
        self.open_button = button("打开", kind="ghost")
        self.open_button.setToolTip("在文件管理器中打开 sd.cpp 引擎目录")
        row.addWidget(self.open_button)
        self.install_button = button("安装引擎…", kind="ghost")
        self.install_button.setToolTip("下载 stable-diffusion.cpp 预编译包，或导入本地 zip / 目录")
        row.addWidget(self.install_button)
        self.body.addLayout(row)

        self.engines_list = RowListWidget(self)
        self.engines_list.setObjectName("PaintEngineList")
        self.engines_list.setSelectionMode(RowListWidget.SelectionMode.SingleSelection)
        self.engines_list.setSpacing(4)
        self.engines_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.engines_list.setToolTip("双击一行可直接设为默认 sd.cpp 引擎")
        self.body.addWidget(self.engines_list)
        self.engines_empty = hint_label("还没有已安装 sd.cpp 引擎；点「安装引擎…」下载或导入。")
        self.engines_empty.setVisible(False)
        self.body.addWidget(self.engines_empty)

        self._engines: list[_llmpaint_engine.SdEngine] = []
        self.open_button.clicked.connect(self._open_root)
        self.install_button.clicked.connect(self.install)
        self.refresh_button.clicked.connect(self.refresh)
        self.engines_list.currentRowChanged.connect(self._on_row_selected)
        self.engines_list.itemDoubleClicked.connect(self._on_double_clicked)
        self.refresh()

    def _selected_tag(self) -> str | None:
        """当前生效的 sd.cpp 引擎 tag（画图 / 视频 / 音频共用 ``current.json``）。"""
        return _llmpaint_engine.current_sd_tag()

    def refresh(self) -> None:
        """刷新已安装引擎列表；空态 / 错误态由提示行承接，列表随之收起。"""
        try:
            self._engines = _llmpaint_engine.list_sd_engines()
        except OSError as exc:
            self._engines = []
            self.engines_list.clear()
            self.engines_list.setVisible(False)
            self.engines_empty.setText(f"sd.cpp 引擎信息不可用：{exc}")
            self.engines_empty.setVisible(True)
            return

        selected = self._selected_tag()
        self.engines_list.clear()
        for engine in self._engines:
            list_item = QListWidgetItem(self.engines_list)
            list_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            row = _PaintEngineRow(engine, current=engine.tag == selected, parent=self.engines_list)
            row.useRequested.connect(self._use)
            list_item.setSizeHint(QSize(0, row.sizeHint().height()))
            self.engines_list.setItemWidget(list_item, row)
        self.engines_list.sync_row_widths()
        self.engines_list.setVisible(bool(self._engines))
        self._fit_list()
        self.engines_empty.setText("还没有已安装 sd.cpp 引擎；点「安装引擎…」下载或导入。")
        self.engines_empty.setVisible(not self._engines)

    def _fit_list(self) -> None:
        """把列表高度收到内容高度，超过 6 行后再出现滚动条（与推理引擎一致）。"""
        count = self.engines_list.count()
        if count == 0:
            self.engines_list.setFixedHeight(0)
            return
        heights = [self.engines_list.sizeHintForRow(index) for index in range(count)]
        spacing = self.engines_list.spacing()
        content = sum(heights) + spacing * (count + 2) + 2
        cap = max(heights) * 6 + spacing * 8 + 2
        self.engines_list.setFixedHeight(min(content, cap))

    def _on_row_selected(self, current: int) -> None:
        """同步行选中态（只加粗边框，不影响内容）。"""
        for index in range(self.engines_list.count()):
            row = self.engines_list.itemWidget(self.engines_list.item(index))
            if isinstance(row, _PaintEngineRow):
                row.set_selected(index == current)

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        """双击引擎行直接设为默认。"""
        index = self.engines_list.row(item)
        if 0 <= index < len(self._engines):
            self._use(self._engines[index].tag)

    def install(self) -> None:
        """打开 sd.cpp 引擎安装对话框；安装完成后刷新列表。"""
        from fcmd.apps.llm._llmw_qt_paint_engine import PaintEngineDialog

        dialog = PaintEngineDialog(self)
        dialog.installed.connect(self._on_installed)
        dialog.exec()

    def _on_installed(self, _tag: str) -> None:
        """安装成功后刷新列表；默认 tag 已由安装流程写入 ``current.json``。"""
        self.refresh()
        self.changed.emit()

    def _use(self, tag: str) -> None:
        """把指定 tag 设为默认 sd.cpp 引擎（画图 / 视频 / 音频共用）。"""
        try:
            _llmpaint_engine.use_sd_engine(tag)
        except (OSError, ServeError) as exc:
            QMessageBox.critical(self, "切换 sd.cpp 引擎失败", str(exc))
            return
        self.refresh()
        self.changed.emit()

    def _open_root(self) -> None:
        root = _llmconfig.paint_engines_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
            open_path(root)
        except OSError as exc:
            QMessageBox.critical(self, "打开失败", f"无法打开目录：{root}\n{exc}")
