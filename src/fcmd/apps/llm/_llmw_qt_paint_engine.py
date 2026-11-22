"""llmw 画图引擎对话框：stable-diffusion.cpp 版本 / 变体下载与本地 zip 导入。

版本列表来自 ``leejet/stable-diffusion.cpp`` 的 GitHub releases（与
``fcmd llmpaint engine-install`` 同一份解析逻辑）；默认变体按本机 GPU 选择
（N 卡 → CUDA 并附带 cudart 运行库，否则 Vulkan / CPU）。安装完成后把引擎设为
默认并刷新运行页的「画图引擎」卡片。
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QComboBox, QDialog, QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from fcmd.apps.llm import _llmpaint_engine, llmengine
from fcmd.apps.llm.llmbase import human_size
from fcmd.gui.qt.widgets import Card, HintLabel, LoadingBar, ProgressRow, button, fit_dialog_to_content, hint_label
from fcmd.gui.qt.workers import ProgressBridge, submit_daemon
from fcmd.infras.download import SpeedMeter, format_eta, format_speed

__all__ = ["PaintEngineDialog", "variant_note"]

_RELEASE_LIMIT = 20
"""版本列表拉取条数：新版在前，20 个足够覆盖常用 sd.cpp tag。"""

_VARIANT_NOTES: dict[str, str] = {
    "cuda": "NVIDIA 显卡优先；会自动附带 cudart 运行库",
    "vulkan": "兼容性最广，AMD / Intel 显卡可用",
    "rocm": "AMD ROCm；需要自备驱动",
    "cpu": "无 GPU 时的兜底（很慢）",
}


def variant_note(variant: str) -> str:
    """变体的一句话说明（``cuda12`` / ``rocm-10.1`` 按前缀归并）。"""
    prefix = variant.split("-", 1)[0].lower().rstrip("0123456789")
    return _VARIANT_NOTES.get(prefix, "自定义构建")


class PaintEngineDialog(QDialog):  # pragma: no cover - Qt 控件
    """选择 sd.cpp 版本 / 变体下载，或从本地 zip 导入引擎。"""

    installed = Signal(str)
    """安装成功信号：携带引擎 tag，画图页据此刷新引擎状态。"""

    def __init__(self, parent: QWidget | None = None, *, online_only: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("安装画图引擎")
        self.setModal(True)
        self._releases: tuple[_llmpaint_engine.SdRelease, ...] = ()
        self._gpu: Any = None
        self._fetching = False
        self._installing = False
        self._closed = False
        self._bridge: ProgressBridge | None = None
        self._download_speed = SpeedMeter()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        card = Card("stable-diffusion.cpp 引擎", self)
        layout.addWidget(card)

        picker = QHBoxLayout()
        picker.setContentsMargins(0, 0, 0, 0)
        picker.setSpacing(8)
        picker.addWidget(hint_label("版本", wrap=False))
        self.version_combo = QComboBox(card)
        self.version_combo.setMinimumWidth(240)
        self.version_combo.setPlaceholderText("正在获取版本列表…")
        self.version_combo.currentIndexChanged.connect(lambda _index: self._sync_variants())
        picker.addWidget(self.version_combo, 1)
        picker.addWidget(hint_label("变体", wrap=False))
        self.variant_combo = QComboBox(card)
        self.variant_combo.setMinimumWidth(120)
        self.variant_combo.setPlaceholderText("选择变体")
        self.variant_combo.setToolTip("CUDA 适合 NVIDIA 显卡；Vulkan 兼容性最广；CPU 兼容兜底")
        self.variant_combo.currentIndexChanged.connect(lambda _index: self._sync_info())
        picker.addWidget(self.variant_combo, 0)
        self.refresh_button = button("刷新", kind="ghost")
        self.refresh_button.setToolTip("重新拉取 GitHub 上的可下载版本列表")
        self.refresh_button.clicked.connect(self.refresh)
        picker.addWidget(self.refresh_button)
        card.body.addLayout(picker)

        # 版本列表还没回来时不要显示「空白下拉 + 空行」：加载条填住信息区。
        self.loading = LoadingBar("正在获取版本列表…", card)
        card.body.addWidget(self.loading)
        self.variant_hint = hint_label("", wrap=True)
        self.variant_hint.setVisible(False)
        card.body.addWidget(self.variant_hint)
        self.info = HintLabel("")
        self.info.setVisible(False)
        card.body.addWidget(self.info)

        self.progress_row = ProgressRow(parent=card)
        self.progress = self.progress_row.bar
        self.install_button = button("下载并安装", kind="primary")
        self.install_button.setToolTip("下载选中版本到画图引擎目录并设为默认")
        self.install_button.clicked.connect(self._download)
        self.local_button = button("从本地 zip 安装…", kind="ghost")
        self.local_button.setToolTip("已有 sd.cpp 预编译包时直接导入（Windows / Linux / macOS 均可）")
        self.local_button.clicked.connect(self._install_local)
        self.local_button.setVisible(not online_only)
        self.close_button = button("关闭", kind="ghost")
        self.close_button.clicked.connect(self.reject)
        for widget in (self.install_button, self.local_button, self.close_button):
            self.progress_row.add_action(widget)
        card.body.addWidget(self.progress_row)

        self.status = hint_label("", wrap=False)
        self.status.setVisible(False)
        card.body.addWidget(self.status)
        mirror_hint = hint_label(
            "版本来自 GitHub releases（默认国内镜像，失败回退 GitHub）；"
            "FCMD_GITHUB_MIRROR 可覆盖；本地包在设置页用「导入压缩包」。"
        )
        mirror_hint.setToolTip(
            "镜像失败自动回退 GitHub；设 FCMD_GITHUB_MIRROR=direct 只用 GitHub；"
            "安装目录由 FCMD_SD_ENGINES / 配置项 sd_engines_root 决定。"
        )
        card.body.addWidget(mirror_hint)

        self._releases_ready(False)
        self.refresh()
        self._fit()

    def _fit(self) -> None:
        """内容变化后重新贴合高度（版本 / 变体信息会换行，高度不定）。"""
        fit_dialog_to_content(self, min_width=660)

    def _set_status(self, text: str) -> None:
        """状态行：空文本时收起，并重新贴合高度。"""
        self.status.setText(text)
        self.status.setVisible(bool(text))
        self._fit()

    # ------------------------------------------------------------------ #
    # 版本列表
    # ------------------------------------------------------------------ #
    def closeEvent(self, event: QCloseEvent) -> None:
        """关窗不等待后台任务：断开进度信号，下载按自身超时收尾。"""
        self._closed = True
        if self._bridge is not None:
            with contextlib.suppress(RuntimeError, TypeError):
                self._bridge.progress.disconnect(self._on_progress)
            self._bridge = None
        super().closeEvent(event)

    def refresh(self) -> None:
        """后台拉取版本列表（同时探测 GPU 以选默认变体）。"""
        if self._fetching or self._installing:
            return
        self._fetching = True
        self.refresh_button.setEnabled(False)
        self._releases_ready(False)
        self.loading.start("正在获取版本列表…")
        self.variant_hint.setVisible(False)
        self.info.setVisible(False)
        # 版本没回来之前不显示进度条 / 下载安装 / 关闭按钮那一行，避免误以为可下载。
        self.progress_row.setVisible(False)
        self._set_status("")
        submit_daemon(
            self._load,
            on_done=self._render_releases,
            on_error=self._fetch_failed,
        )

    @staticmethod
    def _load() -> tuple[tuple[_llmpaint_engine.SdRelease, ...], Any]:
        """后台任务：拉 release 列表并探测 GPU（失败时 GPU 为 ``None``）。"""
        releases = _llmpaint_engine.fetch_sd_releases(_RELEASE_LIMIT)
        try:
            gpu = llmengine.detect_gpu()
        except (OSError, RuntimeError):
            gpu = None
        return releases, gpu

    def _fetch_failed(self, message: str) -> None:
        self._fetching = False
        if self._closed:
            return
        self.refresh_button.setEnabled(True)
        self.loading.stop()
        self.progress_row.setVisible(True)
        self._releases = ()
        self.version_combo.clear()
        self.variant_combo.clear()
        self.variant_hint.setText("")
        self.variant_hint.setVisible(False)
        self.info.setText("")
        self.info.setVisible(False)
        self._releases_ready(False)
        self._set_status(f"获取版本列表失败：{message}")

    def _render_releases(self, payload: tuple[tuple[_llmpaint_engine.SdRelease, ...], Any]) -> None:
        self._fetching = False
        if self._closed:
            return
        self.refresh_button.setEnabled(True)
        self.loading.stop()
        self.progress_row.setVisible(True)
        releases, gpu = payload
        self._releases = tuple(releases)
        self._gpu = gpu
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        for release in self._releases:
            label = f"{release.tag} · {release.published[:10] or '未知日期'}"
            self.version_combo.addItem(label, release.tag)
        if self._releases:
            self.version_combo.setCurrentIndex(0)
        self.version_combo.blockSignals(False)
        if not self._releases:
            self.variant_combo.clear()
            self.variant_hint.setText("")
            self.variant_hint.setVisible(False)
            self.info.setText("")
            self.info.setVisible(False)
            self._releases_ready(False)
            self._set_status("没有可下载的引擎版本（检查网络或稍后重试）")
            return
        self._sync_variants()
        self._set_status("选择版本与变体后下载")

    def _releases_ready(self, ready: bool) -> None:
        self.version_combo.setEnabled(ready)
        self.variant_combo.setEnabled(ready)
        self.install_button.setEnabled(ready)

    def _current_release(self) -> _llmpaint_engine.SdRelease | None:
        tag = self.version_combo.currentData()
        return next((release for release in self._releases if release.tag == tag), None)

    def _sync_variants(self) -> None:
        """切换版本时重建变体下拉，并按 GPU 预选默认变体。"""
        release = self._current_release()
        self.variant_combo.blockSignals(True)
        self.variant_combo.clear()
        variants = release.variants() if release is not None else ()
        for variant in variants:
            self.variant_combo.addItem(_llmpaint_engine.variant_label(variant), variant)
            self.variant_combo.setItemData(
                self.variant_combo.count() - 1,
                variant_note(variant),
                Qt.ItemDataRole.ToolTipRole,
            )
        default = _llmpaint_engine.pick_default_variant(variants, self._gpu)
        if default is not None:
            index = self.variant_combo.findData(default)
            if index >= 0:
                self.variant_combo.setCurrentIndex(index)
        self.variant_combo.blockSignals(False)
        has_variant = self.variant_combo.count() > 0
        self._releases_ready(release is not None and has_variant and not self._installing)
        self._sync_info()

    def _sync_info(self) -> None:
        release = self._current_release()
        variant = self.variant_combo.currentData()
        if release is None or not isinstance(variant, str):
            self.variant_hint.setText("")
            self.variant_hint.setVisible(False)
            self.info.setText("")
            self.info.setVisible(False)
            return
        self.variant_hint.setText(f"{_llmpaint_engine.variant_label(variant)}：{variant_note(variant)}")
        self.variant_hint.setVisible(True)
        assets = release.install_assets(variant)
        names = "、".join(asset.name for asset in assets) or "-"
        size = human_size(sum(asset.size for asset in assets)) if assets else "-"
        self.info.setText(f"发布 {release.published[:10] or '-'} · {size}\n{names}")
        self.info.setVisible(True)
        self.install_button.setEnabled(bool(assets) and not self._installing)

    # ------------------------------------------------------------------ #
    # 安装
    # ------------------------------------------------------------------ #
    def _download(self) -> None:
        release = self._current_release()
        variant = self.variant_combo.currentData()
        if release is None or not isinstance(variant, str) or self._installing:
            return
        if not release.install_assets(variant):
            self._set_status("该版本没有可用的安装资产")
            return
        self._set_status(f"正在下载 {release.tag}（{_llmpaint_engine.variant_label(variant)}）…")
        self._start_install(release.tag, variant=variant)

    def _install_local(self) -> None:
        if self._installing:
            return
        chosen, _filter = QFileDialog.getOpenFileNames(
            self,
            "选择 sd.cpp 引擎包（可同时选引擎 + cudart）",
            "",
            "Zip 压缩包 (*.zip)",
        )
        if not chosen:
            return
        paths = tuple(Path(item) for item in chosen)
        label = paths[0].name if len(paths) == 1 else f"{paths[0].name} 等 {len(paths)} 个包"
        self._set_status(f"正在导入 {label}…")
        self._start_install(None, archives=paths)

    def _install_directory(self) -> None:
        if self._installing:
            return
        chosen = QFileDialog.getExistingDirectory(self, "选择 sd.cpp 引擎目录")
        if not chosen:
            return
        self._set_status(f"正在注册引擎目录 {Path(chosen).name}…")
        self._start_install(None, path=Path(chosen))

    def _start_install(self, tag: str | None, **kwargs: Any) -> None:
        """后台安装：``tag`` 为空时按本地文件名推断；成功即设为默认引擎。"""
        if self._installing:
            return
        self._installing = True
        self._releases_ready(False)
        self.refresh_button.setEnabled(False)
        self.local_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self._download_speed.reset()
        bridge = ProgressBridge(self)
        bridge.progress.connect(self._on_progress)
        self._bridge = bridge

        def _report(name: str, received: int, total: int | None) -> None:
            """把下载回调的三元组打包成一个对象，再经信号投递到主线程。"""
            bridge.progress.emit((name, received, total))

        submit_daemon(
            _llmpaint_engine.install_sd_engine,
            tag,
            use=True,
            on_progress=_report,
            on_done=self._installed,
            on_error=self._failed,
            on_finished=self._finished,
            **kwargs,
        )

    def _on_progress(self, event: object) -> None:
        if self._closed:
            return
        if not (isinstance(event, tuple) and len(event) == 3):
            return
        name, received, total = event
        if not isinstance(name, str) or not isinstance(received, int):
            return
        speed = self._download_speed.update(received)
        if isinstance(total, int) and total > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(received * 100 // total, 100))
            eta = self._download_speed.eta(total, done=received)
            eta_text = f" · 剩余 {format_eta(eta)}" if eta is not None else ""
            self.status.setText(
                f"正在下载 {name}：{human_size(received)} / {human_size(total)} · {format_speed(speed)}{eta_text}"
            )
        else:
            self.progress.setRange(0, 0)
            eta_text = ""
            self.status.setText(f"正在下载 {name}：{human_size(received)} · {format_speed(speed)}{eta_text}")

    def _installed(self, engine: _llmpaint_engine.SdEngine) -> None:
        if self._closed:
            return
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self._set_status(f"已安装 {engine.tag}：{engine.exe}")
        self.installed.emit(engine.tag)

    def _failed(self, message: str) -> None:
        if self._closed:
            return
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self._set_status(f"安装失败：{message}")

    def _finished(self) -> None:
        self._installing = False
        if self._closed:
            return
        self._bridge = None
        self.refresh_button.setEnabled(True)
        self.local_button.setEnabled(True)
        self._releases_ready(bool(self._releases))
        self._sync_info()
