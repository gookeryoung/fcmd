"""locllmw 概览页：硬件检测、模型盘、智能推荐与已加载模型。

硬件快照首次检测后写入 ``locllmw.json``，之后读缓存展示并支持重新检测；
推荐行由 :mod:`fcmd.apps.locllm._locllmw_recommend` 纯逻辑计算，性能默认标注
「预测」，同一模型跑过性能测试后改用实测值。检测与刷新不依赖真实 Tk 控件，
Tk 部分沿用 mixin 方式与其它页一致。
"""

from __future__ import annotations

import queue
import threading
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fcmd.apps.locllm import llmenv
from fcmd.apps.locllm._locllmw_hardware import (
    HardwareSnapshot,
    detect_hardware,
    disk_for_path,
    format_disk,
    snapshot_to_dict,
)
from fcmd.apps.locllm._locllmw_recommend import recommend
from fcmd.apps.locllm.llmbase import human_size, now_iso
from fcmd.gui import window as _guiwindow

if TYPE_CHECKING:
    from collections.abc import Callable

    from fcmd.apps.locllm.llmbase import ModelEntry

__all__: list[str] = []


class _LocllmWindowOverview:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """LocllmWindow 的「概览」页：硬件信息、模型盘与智能推荐。"""

    if TYPE_CHECKING:
        # 宿主 LocllmWindow / _LocllmWindowUI 提供的成员。
        root: Any
        _tk: Any
        _ttk: Any
        _theme: Any
        _messagebox: Any
        _filedialog: Any
        _settings: Any
        _settings_path: Path | None
        _hardware: HardwareSnapshot | None
        _measured: dict[str, dict[str, Any]]
        _hardware_thread: threading.Thread | None
        _hardware_queue: queue.Queue[tuple[str, Any]]
        _hardware_detecting: bool
        _closing: bool
        _models: list[ModelEntry]
        _selected_model: Path | None
        _build_loaded: Callable[[Any], None]
        _refresh_models: Callable[[], None]
        _persist_settings: Callable[[], None]

    # ------------------------------------------------------------------ #
    # 界面
    # ------------------------------------------------------------------ #
    def _build_overview(self, parent: Any) -> None:
        """概览页：硬件与模型盘 → 智能推荐 → 已加载模型（最下方）。"""
        self._build_hardware(parent)
        self._build_recommendations(parent)
        self._build_loaded(parent)

    def _build_hardware(self, parent: Any) -> None:
        ttk = self._ttk
        space = self._theme.spacing
        box = ttk.LabelFrame(parent, text="硬件信息", padding=space.sm)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)

        self._hardware_vars = {
            key: self._tk.StringVar(value="检测中…") for key in ("gpu", "cpu", "memory", "model_disk", "detected")
        }
        rows = (
            ("显卡", "gpu"),
            ("处理器", "cpu"),
            ("内存", "memory"),
            ("模型盘", "model_disk"),
        )
        for row, (label, key) in enumerate(rows):
            ttk.Label(box, text=label, style="Muted.TLabel").grid(
                row=row, column=0, sticky="nw", padx=(0, space.sm), pady=space.xxs
            )
            value = ttk.Label(box, textvariable=self._hardware_vars[key], justify="left")
            value.grid(row=row, column=1, sticky="we", pady=space.xxs)
            value.bind(
                "<Configure>",
                lambda event: event.widget.configure(wraplength=_guiwindow.fit_wraplength(event.width)),
            )
        ttk.Button(box, text="修改模型盘…", command=self._choose_model_root).grid(
            row=3, column=2, sticky="e", padx=(space.sm, 0)
        )

        self._usage_vars = {
            "gpu": self._tk.StringVar(value="等待采样…"),
            "memory": self._tk.StringVar(value="等待采样…"),
        }
        self._usage_bars: dict[str, Any] = {}
        for row, (label, key) in enumerate((("显存占用", "gpu"), ("内存占用", "memory")), start=4):
            ttk.Label(box, text=label, style="Muted.TLabel").grid(
                row=row, column=0, sticky="nw", padx=(0, space.sm), pady=space.xxs
            )
            usage = ttk.Frame(box)
            usage.grid(row=row, column=1, columnspan=2, sticky="we", pady=space.xxs)
            usage.columnconfigure(0, weight=1)
            bar = ttk.Progressbar(usage, mode="determinate", maximum=100, length=220)
            bar.grid(row=0, column=0, sticky="we", padx=(0, space.sm))
            ttk.Label(usage, textvariable=self._usage_vars[key], style="Hint.TLabel").grid(row=0, column=1, sticky="e")
            self._usage_bars[key] = bar

        ttk.Label(box, textvariable=self._hardware_vars["detected"], style="Hint.TLabel").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(space.xs, 0)
        )
        ttk.Button(box, text="重新检测", command=self._start_hardware_detection).grid(
            row=6, column=2, sticky="e", padx=(space.sm, 0)
        )

    def _refresh_overview_usage(self, sample: Any) -> None:
        """用系统采样刷新显存 / 内存占用条与百分比文案。"""
        gpu = sample.gpu
        if gpu is not None and gpu.memory_used is not None and gpu.memory_total:
            percent = min(max(gpu.memory_used / gpu.memory_total * 100, 0.0), 100.0)
            self._usage_bars["gpu"].configure(value=percent)
            self._usage_vars["gpu"].set(
                f"{gpu.memory_used / 1024:.1f} / {gpu.memory_total / 1024:.1f} GB（{percent:.1f} %）"
            )
        else:
            self._usage_bars["gpu"].configure(value=0)
            self._usage_vars["gpu"].set("未检测到 NVIDIA GPU" if gpu is None else "暂不可用")
        if sample.memory_total:
            percent = min(max(sample.memory_percent, 0.0), 100.0)
            self._usage_bars["memory"].configure(value=percent)
            self._usage_vars["memory"].set(
                f"{human_size(sample.memory_used)} / {human_size(sample.memory_total)}（{percent:.1f} %）"
            )
        else:  # pragma: no cover - psutil 总能返回内存总量
            self._usage_bars["memory"].configure(value=0)
            self._usage_vars["memory"].set("暂不可用")

    def _reset_overview_usage(self) -> None:
        """停止采样时把占用条归零，避免停在过期数值。"""
        for key, bar in self._usage_bars.items():
            bar.configure(value=0)
            self._usage_vars[key].set("未监控")

    def _build_recommendations(self, parent: Any) -> None:
        ttk = self._ttk
        space = self._theme.spacing
        box = ttk.LabelFrame(parent, text="智能推荐（按显存筛选；性能测试后优先用实测值）", padding=space.sm)
        box.pack(fill="x", pady=(space.sm, 0))
        self._recommend_tree = ttk.Treeview(
            box,
            columns=("model", "config", "performance", "basis"),
            show="headings",
            height=3,
            selectmode="browse",
        )
        for column, label, width, stretch in (
            ("model", "推荐模型", 260, True),
            ("config", "启动配置", 320, True),
            ("performance", "性能", 300, True),
            ("basis", "依据", 160, False),
        ):
            self._recommend_tree.heading(column, text=label)
            self._recommend_tree.column(column, width=width, anchor="w", stretch=stretch)
        self._recommend_tree.pack(fill="x")
        self._recommend_status = self._tk.StringVar(value="等待硬件检测与模型扫描…")
        ttk.Label(box, textvariable=self._recommend_status, style="Hint.TLabel", justify="left").pack(
            anchor="w", fill="x", pady=(space.xs, 0)
        )

    # ------------------------------------------------------------------ #
    # 初始化 / 硬件检测
    # ------------------------------------------------------------------ #
    def _init_overview(self) -> None:
        """填充缓存快照；没有缓存时首次检测并写入配置。"""
        if self._hardware is None:
            self._hardware_vars["detected"].set("尚未检测硬件，正在首次检测…")
            self._start_hardware_detection()
        else:
            self._refresh_hardware_view()
        self._refresh_recommendations()

    def _start_hardware_detection(self) -> None:
        if self._hardware_detecting:
            return
        self._hardware_detecting = True
        self._hardware_vars["detected"].set("正在检测硬件…")
        self._hardware_queue = queue.Queue()
        self._hardware_thread = threading.Thread(target=self._hardware_work, daemon=True)
        self._hardware_thread.start()
        self.root.after(120, self._poll_hardware)

    def _hardware_work(self) -> None:
        """后台检测；异常也回传，避免界面一直停在「检测中」。"""
        try:
            snapshot = detect_hardware(models_root=self._primary_model_root())
        except Exception as exc:  # 边界兜底：驱动 / psutil 异常不能拖垮窗口
            self._hardware_queue.put(("error", str(exc)))
            return
        self._hardware_queue.put(("ok", snapshot))

    def _poll_hardware(self) -> None:
        if self._closing:
            return
        try:
            kind, payload = self._hardware_queue.get_nowait()
        except queue.Empty:
            self.root.after(120, self._poll_hardware)
            return
        self._hardware_detecting = False
        if kind == "error":
            self._hardware_vars["detected"].set(f"检测失败：{payload}")
            return
        self._hardware = payload
        self._refresh_hardware_view()
        self._refresh_recommendations()
        self._persist_settings()

    def _refresh_hardware_view(self) -> None:
        snapshot = self._hardware
        if snapshot is None:
            return
        gpu = (
            f"{snapshot.gpu} · {snapshot.gpu_mib / 1024:.0f} GB 显存"
            if snapshot.gpu_mib
            else "未检测到 NVIDIA GPU（CPU 推理）"
        )
        self._hardware_vars["gpu"].set(gpu)
        self._hardware_vars["cpu"].set(f"{snapshot.cpu}（{snapshot.cpu_cores} 核 / {snapshot.cpu_threads} 线程）")
        self._hardware_vars["memory"].set(human_size(snapshot.memory_total) if snapshot.memory_total else "-")
        model_path = self._selected_model or Path(snapshot.model_root or ".")
        disk = disk_for_path(model_path, snapshot.disks)
        root_text = str(model_path if self._selected_model else (snapshot.model_root or "未配置"))
        self._hardware_vars["model_disk"].set(f"{root_text}\n{format_disk(disk)}")
        self._hardware_vars["detected"].set(
            f"检测时间 {snapshot.detected_at or '未知'}；模型盘随选中模型或主模型目录变化"
        )

    # ------------------------------------------------------------------ #
    # 模型盘
    # ------------------------------------------------------------------ #
    @staticmethod
    def _primary_model_root() -> Path:
        return llmenv.models_dir()

    def _choose_model_root(self) -> None:
        """选择新的模型根目录并写入 ``llmenv`` 配置，随后重扫模型。"""
        current = self._primary_model_root()
        chosen = self._filedialog.askdirectory(
            parent=self.root,
            title="选择模型盘 / 模型根目录",
            initialdir=str(current),
        )
        if not chosen:
            return
        path = Path(chosen).expanduser()
        if not path.is_dir():
            self._messagebox.showerror("修改模型盘", f"目录不存在：{path}")
            return
        if not self._messagebox.askyesno(
            "修改模型盘",
            f"把模型根目录改为：\n{path}\n\n现有模型目录配置会被替换，随后重新扫描模型。",
        ):
            return
        llmenv.set_models_dir(path)
        if self._hardware is not None:
            self._hardware = replace(self._hardware, model_root=str(path))
        self._refresh_hardware_view()
        self._persist_settings()
        self._refresh_models()

    # ------------------------------------------------------------------ #
    # 智能推荐 / 实测
    # ------------------------------------------------------------------ #
    def _refresh_recommendations(self) -> None:
        tree = getattr(self, "_recommend_tree", None)
        status = getattr(self, "_recommend_status", None)
        if tree is None or status is None:
            return
        tree.delete(*tree.get_children())
        rows = recommend(self._models, self._hardware, self._measured)
        if not rows:
            status.set(
                "未检测到可用模型：先在「模型」页刷新，或用「修改模型盘…」登记模型目录。"
                if not self._models
                else "当前模型都放不进整卡显存：建议换更小量化，或释放显存后重新检测。"
            )
            return
        for row in rows:
            tree.insert(
                "",
                "end",
                values=(
                    row.model.path.name,
                    row.config_text,
                    row.performance_text,
                    row.basis,
                ),
            )
        status.set(
            "推荐按模型大小与显存适配排序；解码速度为「预测」时来自参考机器外推，"
            "跑过性能测试后同一模型自动切换为「实测」。"
        )

    def _record_benchmark(self, model_path: str, result: Any) -> None:
        """记录性能测试结果；后续推荐优先使用实测解码速度。"""
        if not model_path or result.error is not None or result.cancelled:
            return
        key = llmenv.path_key(Path(model_path))
        record = dict(self._measured.get(key, {}))
        if result.scenario.key in ("short", "generate"):
            record["decode_tps"] = float(result.mean_tps)
        if result.scenario.key == "concurrent4":
            record["aggregate_tps"] = float(result.aggregate_tps)
        if not record:
            return
        record["scenario"] = result.scenario.key
        record["at"] = now_iso()
        self._measured[key] = record
        self._persist_settings()
        self._refresh_recommendations()

    def _hardware_dict(self) -> dict[str, Any] | None:
        """返回可写入配置的硬件快照。"""
        return snapshot_to_dict(self._hardware) if self._hardware is not None else None
