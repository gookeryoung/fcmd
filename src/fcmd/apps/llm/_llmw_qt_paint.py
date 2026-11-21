"""llmw 画图配置：sd.cpp 文生图的纯逻辑、服务调用与配置分区。

画图不再有独立页面：对话页勾选「画图」后用提示词出图，画图配置嵌进对话页的
「模型与生成配置」窗口（:class:`PaintConfigSection`），引擎安装 / 状态放在运行页。
资产扫描、组件配对、启动参数、请求载荷与出图保存都在
:mod:`fcmd.apps.llm._llmpaint` / ``_llmpaint_server`` / ``_llmpaint_engine``
（与 ``fcmd llmpaint`` 共用），这里只做设置解析、服务调用与 Qt 控件。

模块级函数都是纯逻辑（解析设置、构造组件 / 请求、命令预览），供测试直接断言；
:class:`PaintConfigSection` 与 :func:`generate_images` / :func:`start_paint_service`
是 GUI / 服务胶水（``pragma: no cover``）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.llm import _llmconfig, _llmpaint, _llmpaint_server
from fcmd.apps.llm._llmw_models import open_path
from fcmd.apps.llm.llmbase import ServeError, format_command, human_size
from fcmd.gui.qt.widgets import (
    Collapsible,
    ElidedLabel,
    FormGrid,
    button,
    hint_label,
    mono_label,
)
from fcmd.gui.qt.workers import submit

if TYPE_CHECKING:
    from collections.abc import Sequence


__all__ = [
    "MODES",
    "SAMPLERS",
    "SIZE_PRESETS",
    "PaintConfigSection",
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
    "service_label",
    "settings_summary",
    "size_preset",
    "start_paint_service",
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


def _section_label(text: str, parent: QWidget) -> QLabel:
    """分区里的小节标题（与配置窗口的分节标题同一套字号 / 颜色）。"""
    label = QLabel(text, parent)
    label.setObjectName("SectionTitle")
    return label


class PaintConfigSection(QWidget):  # pragma: no cover - Qt 控件
    """画图配置分区：服务、模型与组件、采样参数、输出目录。

    嵌进对话页的「模型与生成配置」窗口；引擎（sd.cpp 版本 / 安装）在运行页。
    每次改动发 :attr:`changed`，由对话页负责刷新摘要与延迟写盘；组合框的资产
    扫描在后台线程完成，扫描期间沿用已保存的路径，避免误清空模型选择。
    """

    changed = Signal()

    def __init__(self, settings: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = parse_paint_settings(settings)
        self._assets = _llmpaint.PaintAssets()
        self._assets_loaded = False
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(_section_label("画图服务", self))
        service_form = FormGrid(self)
        self.port = QSpinBox(self)
        self.port.setRange(1, 65535)
        self.port.setToolTip("sd-server 监听端口（默认 1234，避开 llama-server 的 8080）")
        service_form.add_row("服务端口", self.port)
        self.auto_start = QCheckBox("生成前自动启动 / 重启服务", self)
        self.auto_start.setToolTip("模型或参数变化时自动按新指纹重启（FLUX 等大模型首次加载较慢）")
        service_form.add_row("自动启动", self.auto_start)
        layout.addWidget(service_form)

        layout.addWidget(_section_label("模型与组件", self))
        family_row = QHBoxLayout()
        family_row.setContentsMargins(0, 0, 0, 0)
        family_row.setSpacing(8)
        self.family_label = mono_label("未选择主模型")
        family_row.addWidget(self.family_label, 1)
        self.suggest_button = button("自动配对", kind="ghost", tooltip="按主模型家族填满 VAE / 文本编码器槽位")
        family_row.addWidget(self.suggest_button)
        layout.addLayout(family_row)

        models_form = FormGrid(self)
        self.mode_combo = QComboBox(models_form)
        for key, label in MODES:
            self.mode_combo.addItem(label, key)
        models_form.add_row("加载方式", self.mode_combo)
        self.model_combo = QComboBox(models_form)
        models_form.add_row("主模型", self.model_combo)
        self.role_combos: dict[str, QComboBox] = {}
        for role, label in ROLE_COMBOS:
            combo = QComboBox(models_form)
            self.role_combos[role] = combo
            models_form.add_row(label, combo)
        layout.addWidget(models_form)

        options_row = QHBoxLayout()
        options_row.setContentsMargins(0, 0, 0, 0)
        options_row.setSpacing(10)
        self.offload_to_cpu = QCheckBox("CPU 卸载", self)
        self.offload_to_cpu.setToolTip("--offload-to-cpu：显存不足时把部分权重放到内存（更慢）")
        self.vae_tiling = QCheckBox("VAE 分块", self)
        self.vae_tiling.setToolTip("--vae-tiling：大图解码时降低显存峰值")
        self.diffusion_fa = QCheckBox("Diffusion FA", self)
        self.diffusion_fa.setToolTip("--diffusion-fa：启用 flash attention 加速")
        for widget in (self.offload_to_cpu, self.vae_tiling, self.diffusion_fa):
            options_row.addWidget(widget)
        options_row.addStretch(1)
        layout.addLayout(options_row)

        self.extra_edit = QLineEdit(self)
        self.extra_edit.setPlaceholderText("额外参数（可选，例如 --clip-on-cpu）")
        self.extra_edit.setToolTip("原样追加到 sd-server；按空白分隔，含空格的值用双引号包裹")
        layout.addWidget(self.extra_edit)

        self.preview_collapse = Collapsible("命令预览", parent=self)
        self.preview_label = mono_label("")
        self.preview_label.setWordWrap(True)
        self.preview_label.setObjectName("Hint")
        self.preview_collapse.body.addWidget(self.preview_label)
        layout.addWidget(self.preview_collapse)

        layout.addWidget(_section_label("采样参数", self))
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

        layout.addWidget(_section_label("输出目录", self))
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
        self.refresh_assets()

    # ------------------------------------------------------------------ #
    # 设置读写
    # ------------------------------------------------------------------ #
    def settings(self) -> dict[str, Any]:
        """收集当前控件值（未扫描资产前沿用已保存路径，避免误清空）。"""
        settings = dict(self._settings)
        settings.update(
            {
                "port": self.port.value(),
                "auto_start": self.auto_start.isChecked(),
                "mode": str(self.mode_combo.currentData() or "auto"),
                "model": self._combo_value(self.model_combo, "model"),
                "width": self.width.value(),
                "height": self.height.value(),
                "steps": self.steps.value(),
                "cfg_scale": self.cfg.value(),
                "sampler": self.sampler.currentText().strip(),
                "scheduler": self.scheduler.text().strip(),
                "seed": -1 if self.random_seed.isChecked() else self.seed.value(),
                "batch": self.batch.value(),
                "offload_to_cpu": self.offload_to_cpu.isChecked(),
                "vae_tiling": self.vae_tiling.isChecked(),
                "diffusion_fa": self.diffusion_fa.isChecked(),
                "extra_args": parse_extra_args(self.extra_edit.text()),
            }
        )
        for role, _label in ROLE_COMBOS:
            settings[role] = self._combo_value(self.role_combos[role], role)
        return parse_paint_settings(settings)

    def set_settings(self, settings: dict[str, Any]) -> None:
        """外部改写设置（如切换对话恢复）后回填控件，不回发 ``changed``。"""
        self._settings = parse_paint_settings(settings)
        self._apply()

    def _apply(self) -> None:
        """把 ``self._settings`` 回填到控件（组合框选中项等资产扫描完再补）。"""
        settings = self._settings
        self._loading = True
        try:
            self.port.setValue(int(settings["port"]))
            self.auto_start.setChecked(bool(settings["auto_start"]))
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
            # 输出目录可能来自外部改写，刷新命令预览后再回填一次状态。
            self._settings = self.settings()
        finally:
            self._loading = False

    def _combo_value(self, combo: QComboBox, key: str) -> str:
        data = combo.currentData()
        if isinstance(data, str):
            return data
        return "" if self._assets_loaded else str(self._settings.get(key) or "")

    def _wire(self) -> None:
        self.random_seed.toggled.connect(self.seed.setDisabled)
        self.width.valueChanged.connect(self._sync_preset)
        self.height.valueChanged.connect(self._sync_preset)
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self.suggest_button.clicked.connect(lambda: self._suggest(only_empty=False))
        self.save_to_button.clicked.connect(self._pick_output_dir)
        self.output_button.clicked.connect(self._open_output_dir)
        for signal in (
            self.port.valueChanged,
            self.auto_start.toggled,
            self.width.valueChanged,
            self.height.valueChanged,
            self.steps.valueChanged,
            self.cfg.valueChanged,
            self.seed.valueChanged,
            self.random_seed.toggled,
            self.batch.valueChanged,
            self.sampler.currentTextChanged,
            self.scheduler.textChanged,
            self.mode_combo.currentIndexChanged,
            self.offload_to_cpu.toggled,
            self.vae_tiling.toggled,
            self.diffusion_fa.toggled,
            self.extra_edit.textChanged,
        ):
            signal.connect(self._on_changed)
        for combo in self.role_combos.values():
            combo.currentIndexChanged.connect(self._on_changed)

    def _on_changed(self, *_args: Any) -> None:
        if self._loading:
            return
        self._update_preview()
        self.changed.emit()

    # ------------------------------------------------------------------ #
    # 资产
    # ------------------------------------------------------------------ #
    def refresh_assets(self) -> None:
        """后台重新扫描扩散模型与组件（模型目录变化时由窗口调用）。"""
        settings = self.settings()
        self._settings = settings
        submit(
            lambda: _llmpaint.scan_assets(model_roots(settings)),
            on_done=self._apply_assets,
            on_error=self._assets_failed,
        )

    def _assets_failed(self, message: str) -> None:
        self.family_label.setText(f"扫描模型失败：{message}")

    def _apply_assets(self, assets: _llmpaint.PaintAssets) -> None:
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
            self.model_combo.setCurrentIndex(1)
        self._apply_component_selection()
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
        self._on_changed()

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
        self._on_changed()

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

    def _update_preview(self) -> None:
        self.preview_label.setText(command_preview(self.settings()))

    def _refresh_output_label(self) -> None:
        path = output_dir(self._settings)
        self.output_label.setText(f"输出目录：{path}")
        self.output_button.setToolTip(f"打开输出目录：{path}")
        self.save_to_button.setToolTip(f"更改输出目录（当前：{path}）")

    def _pick_output_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择出图目录", str(output_dir(self._settings)))
        if not chosen:
            return
        self._settings = parse_paint_settings({**self._settings, "output_dir": chosen})
        self._refresh_output_label()
        self.changed.emit()

    def _open_output_dir(self) -> None:
        target = output_dir(self.settings())
        try:
            target.mkdir(parents=True, exist_ok=True)
            open_path(target)
        except OSError as exc:
            QMessageBox.critical(self, "打开失败", f"无法打开目录：{target}\n{exc}")
