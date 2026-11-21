"""llmw 引擎下载对话框：可访问的版本列表 + 变体选择 + 下载进度。

版本列表来自 llama.cpp 的 GitHub releases（``llmengine releases`` 同一份数据）；
选中版本后按 release 里实际存在的资产名下载（不同 release 的 CUDA 版本 / 变体
可能不同），安装与「首个引擎设为默认」复用 :mod:`fcmd.apps.llm._llmw_settings`
的 ``install_online``，与 CLI 保持同一套逻辑与错误语义。
"""

from __future__ import annotations

import contextlib

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QVBoxLayout, QWidget

from fcmd.apps.llm import _llmw_settings as settings_logic
from fcmd.apps.llm import llmengine
from fcmd.apps.llm.llmbase import EngineInfo, human_size
from fcmd.gui.qt.widgets import Card, HintLabel, ProgressRow, button, fit_dialog_to_content, hint_label
from fcmd.gui.qt.workers import ProgressBridge, submit_daemon

__all__ = ["EngineDownloadDialog"]

_RELEASE_LIMIT = 15
"""版本列表拉取条数：新版在前，15 个足够覆盖常用引擎 tag。"""


class EngineDownloadDialog(QDialog):  # pragma: no cover - Qt 控件
    """选择 llama.cpp 版本 / 变体并下载安装到引擎目录。"""

    installed = Signal(str)
    """安装成功信号：携带引擎 tag，设置页据此刷新列表。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("下载引擎")
        self.setModal(True)
        self._releases: tuple[llmengine.EngineRelease, ...] = ()
        self._fetching = False
        self._downloading = False
        self._closed = False
        self._bridge: ProgressBridge | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        card = Card("llama.cpp 引擎", self)
        layout.addWidget(card)

        picker = QHBoxLayout()
        picker.setContentsMargins(0, 0, 0, 0)
        picker.setSpacing(8)
        picker.addWidget(hint_label("版本", wrap=False))
        self.version_combo = QComboBox(card)
        self.version_combo.setMinimumWidth(220)
        self.version_combo.currentIndexChanged.connect(lambda _index: self._sync_variants())
        picker.addWidget(self.version_combo, 1)
        picker.addWidget(hint_label("变体", wrap=False))
        self.variant_combo = QComboBox(card)
        self.variant_combo.setMinimumWidth(120)
        self.variant_combo.setToolTip("CUDA 适合 NVIDIA 显卡；Vulkan 兼容性最广；CPU 兼容兜底")
        self.variant_combo.currentIndexChanged.connect(lambda _index: self._sync_info())
        picker.addWidget(self.variant_combo, 0)
        self.refresh_button = button("刷新", kind="ghost")
        self.refresh_button.setToolTip("重新拉取 GitHub 上的可下载版本列表")
        self.refresh_button.clicked.connect(self.refresh)
        picker.addWidget(self.refresh_button)
        card.body.addLayout(picker)

        self.variant_hint = hint_label("", wrap=True)
        card.body.addWidget(self.variant_hint)
        self.info = HintLabel("")
        card.body.addWidget(self.info)

        self.progress_row = ProgressRow(parent=card)
        self.progress = self.progress_row.bar
        self.download_button = button("下载并安装", kind="primary")
        self.download_button.setToolTip("下载选中版本到引擎目录；尚无默认引擎时自动设为默认")
        self.download_button.clicked.connect(self._download)
        self.progress_row.add_action(self.download_button)
        self.close_button = button("关闭", kind="ghost")
        self.close_button.clicked.connect(self.reject)
        self.progress_row.add_action(self.close_button)
        card.body.addWidget(self.progress_row)

        self.status = hint_label("正在获取版本列表…", wrap=False)
        card.body.addWidget(self.status)
        mirror_hint = hint_label(
            "版本来自 GitHub releases（默认国内镜像，失败回退 GitHub）；"
            "FCMD_GITHUB_MIRROR 可覆盖，已下载的 zip 会复用。"
        )
        mirror_hint.setToolTip(
            "镜像顺序 gh-proxy.com / gh-proxy.net，失败自动回退 GitHub；"
            "设 FCMD_GITHUB_MIRROR=direct 只用 GitHub；安装到设置页的引擎目录。"
        )
        card.body.addWidget(mirror_hint)

        self._releases_ready(False)
        self.refresh()
        self._fit()

    def _fit(self) -> None:
        """内容变化后重新贴合高度（版本 / 变体信息会换行，高度不定）。"""
        fit_dialog_to_content(self, min_width=640)

    # ------------------------------------------------------------------ #
    # 版本列表
    # ------------------------------------------------------------------ #
    def closeEvent(self, event: QCloseEvent) -> None:
        """关窗不等待后台任务：断开进度信号，网络请求按自身超时收尾。"""
        self._closed = True
        if self._bridge is not None:
            with contextlib.suppress(RuntimeError, TypeError):
                self._bridge.progress.disconnect(self._on_progress)
            self._bridge = None
        super().closeEvent(event)

    def refresh(self) -> None:
        """后台拉取版本列表；重复点击在请求完成前忽略。"""
        if self._fetching or self._downloading:
            return
        self._fetching = True
        self.refresh_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.status.setText("正在获取版本列表…")
        submit_daemon(
            settings_logic.engine_releases,
            _RELEASE_LIMIT,
            on_done=self._render_releases,
            on_error=self._fetch_failed,
        )

    def _fetch_failed(self, message: str) -> None:
        self._fetching = False
        if self._closed:
            return
        self.refresh_button.setEnabled(True)
        self._releases = ()
        self.version_combo.clear()
        self.variant_combo.clear()
        self.variant_hint.setText("")
        self.info.setText("")
        self._releases_ready(False)
        self.status.setText(f"获取版本列表失败：{message}")
        self._fit()

    def _render_releases(self, releases: tuple[llmengine.EngineRelease, ...]) -> None:
        self._fetching = False
        if self._closed:
            return
        self.refresh_button.setEnabled(True)
        self._releases = tuple(releases)
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        for release in self._releases:
            label = f"{release.tag} · {release.published[:10] or '未知日期'}"
            self.version_combo.addItem(label, release.tag)
        self.version_combo.blockSignals(False)
        if not self._releases:
            self.variant_combo.clear()
            self.variant_hint.setText("")
            self.info.setText("")
            self._releases_ready(False)
            self.status.setText("没有可下载的引擎版本（检查网络或稍后重试）")
            self._fit()
            return
        self._sync_variants()
        self.status.setText("选择版本与变体后下载")
        self._fit()

    def _releases_ready(self, ready: bool) -> None:
        self.version_combo.setEnabled(ready)
        self.variant_combo.setEnabled(ready)
        self.download_button.setEnabled(ready)

    def _current_release(self) -> llmengine.EngineRelease | None:
        tag = self.version_combo.currentData()
        return next((release for release in self._releases if release.tag == tag), None)

    def _sync_variants(self) -> None:
        """切换版本时重建变体下拉（只保留当前机器可用架构）。"""
        release = self._current_release()
        self.variant_combo.blockSignals(True)
        self.variant_combo.clear()
        if release is not None:
            for variant in release.variants():
                self.variant_combo.addItem(settings_logic.variant_label(variant), variant)
                self.variant_combo.setItemData(
                    self.variant_combo.count() - 1,
                    settings_logic.variant_hint(variant),
                    Qt.ItemDataRole.ToolTipRole,
                )
        self.variant_combo.blockSignals(False)
        has_variant = self.variant_combo.count() > 0
        self._releases_ready(release is not None and has_variant and not self._downloading)
        self._sync_info()

    def _sync_info(self) -> None:
        release = self._current_release()
        variant = self.variant_combo.currentData()
        if release is None or not isinstance(variant, str):
            self.variant_hint.setText("")
            self.info.setText("")
            return
        self.variant_hint.setText(f"{settings_logic.variant_label(variant)}：{settings_logic.variant_hint(variant)}")
        assets = release.install_assets(variant)
        names = "、".join(asset.name for asset in assets) or "-"
        size = human_size(sum(asset.size for asset in assets)) if assets else "-"
        self.info.setText(f"发布 {release.published[:10] or '-'} · {size}\n{names}")
        self.download_button.setEnabled(bool(assets) and not self._downloading)

    # ------------------------------------------------------------------ #
    # 下载安装
    # ------------------------------------------------------------------ #
    def _download(self) -> None:
        release = self._current_release()
        variant = self.variant_combo.currentData()
        if release is None or not isinstance(variant, str) or self._downloading:
            return
        assets = release.install_assets(variant)
        if not assets:
            self.status.setText("该版本没有可用的安装资产")
            return
        self._downloading = True
        self._releases_ready(False)
        self.refresh_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.status.setText(f"正在下载 {release.tag}（{settings_logic.variant_label(variant)}）…")
        bridge = ProgressBridge(self)
        bridge.progress.connect(self._on_progress)
        self._bridge = bridge

        def _report(name: str, received: int, total: int | None) -> None:
            """把下载回调的三元组打包成一个对象，再经信号投递到主线程。"""
            bridge.progress.emit((name, received, total))

        submit_daemon(
            settings_logic.install_online,
            release.tag,
            variant,
            asset_names=tuple(asset.name for asset in assets),
            on_progress=_report,
            on_done=self._installed,
            on_error=self._failed,
            on_finished=self._finished,
        )

    def _on_progress(self, event: object) -> None:
        if self._closed:
            return
        if not (isinstance(event, tuple) and len(event) == 3):
            return
        name, received, total = event
        if not isinstance(name, str) or not isinstance(received, int):
            return
        if isinstance(total, int) and total > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(received * 100 // total, 100))
            self.status.setText(f"正在下载 {name}：{human_size(received)} / {human_size(total)}")
        else:
            self.progress.setRange(0, 0)
            self.status.setText(f"正在下载 {name}：{human_size(received)}")

    def _installed(self, info: EngineInfo) -> None:
        if self._closed:
            return
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.status.setText(f"已安装 {info.tag}：{info.exe}")
        self.installed.emit(info.tag)

    def _failed(self, message: str) -> None:
        if self._closed:
            return
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status.setText(f"下载失败：{message}")

    def _finished(self) -> None:
        self._downloading = False
        if self._closed:
            return
        self._bridge = None
        self.refresh_button.setEnabled(True)
        self._releases_ready(bool(self._releases))
        self._sync_info()
