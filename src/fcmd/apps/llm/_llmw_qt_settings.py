"""llmw（Qt）设置页：存储、引擎（llama.cpp / sd.cpp）、下载参数与模型转换。

目录口径与 CLI 完全一致：模型目录列表由 :mod:`fcmd.apps.llm._llmconfig` 提供，
存储卡片以 Steam 式磁盘卡并排展示；引擎目录独立登记；改动即时写配置并通知其它页刷新。
引擎安装 / 切换仍复用 :mod:`fcmd.apps.llm.llmengine` 的既有实现（在线下载入口是
:class:`~fcmd.apps.llm._llmw_qt_engine_install.EngineDownloadDialog`，版本列表与
``fcmd llmengine releases`` 同源），页面只做展示与「设为默认」这类轻动作。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QWidget,
)

from fcmd.apps.llm import (
    _llmconfig,
    _llmw_compat,
    _llmw_convert,
    _llmw_models,
    _llmw_storage,
    llmconvert,
    llmengine,
    llmenv,
)
from fcmd.apps.llm import _llmw_settings as settings_logic
from fcmd.apps.llm._llmw_models import open_path
from fcmd.apps.llm._llmw_qt_engine_card import EngineCard, EngineCardConfig, EngineDirAction, EngineOption
from fcmd.apps.llm.llmbase import EngineInfo, ServeError
from fcmd.gui.page import AppPage
from fcmd.gui.widgets import (
    Card,
    DiskLocation,
    PageScroll,
    PathRow,
    StatusTag,
    StorageCard,
    button,
    confirm,
    hint_label,
    link_button,
)

if TYPE_CHECKING:
    from fcmd.apps.llm._llmw_qt_state import LlmState

__all__ = ["SettingsPage", "engine_rows_values"]


def engine_rows_values(engines: list[EngineInfo]) -> list[tuple[str, str, str]]:
    """已安装引擎的 ``(标签, 来源, llama-server)`` 摘要（供测试与调试）。"""
    return [(info.tag, info.source or "-", str(info.exe)) for info in engines]


class SettingsPage(AppPage):  # pragma: no cover - Qt 控件
    """设置页：存储、推理引擎（llama.cpp）、sd.cpp 引擎（画图 / 视频 / 音频共用）、下载参数与模型转换。"""

    PAGE_KEY = "settings"

    def __init__(self, state: LlmState, parent: QWidget | None = None) -> None:
        super().__init__(state, parent, spacing=0)
        self._engines: list[EngineInfo] = []
        self._engine_manager_dialog: QWidget | None = None
        outer = self.body
        scroll = PageScroll(self)
        layout = scroll.body
        outer.addWidget(scroll)
        layout.addWidget(self._build_storage())
        layout.addWidget(self._build_engines())
        layout.addWidget(self._build_paint_engine())
        layout.addWidget(self._build_download())
        layout.addWidget(self._build_convert())
        layout.addStretch(1)
        self.paint_engine_card.changed.connect(self.refresh_engine_cards)
        self.state.hardwareChanged.connect(lambda *_args: self.refresh_engine_cards())
        self.refresh()

    # ------------------------------------------------------------------ #
    # 存储
    # ------------------------------------------------------------------ #
    def _build_storage(self) -> Card:
        """存储卡片：公共 :class:`StorageCard`，多模型目录增删 / 设为主目录。"""
        locations = _llmw_storage.locations()
        card = StorageCard(
            [location.path for location in locations],
            title="存储",
            hint="模型目录（首个为下载与转换产物落点，可添加多个）",
            add_text="添加模型目录",
            primary_label="下载目标",
            dialog_title="添加模型目录",
            remove_title="移除模型目录",
            min_count=0,
            locations=lambda roots: [_disk_location(location) for location in _llmw_storage.locations_for(roots)],
            open_path=open_path,
            parent=self,
        )
        card.changed.connect(self._on_storage_changed)
        card.message.connect(self._on_storage_message)
        self.storage_card = card
        # 兼容既有测试 / 页内访问路径。
        self.storage_grid = card.grid
        self.migration_note = card.note_label
        self._refresh_migration_note()
        return card

    def _on_storage_message(self, message: str) -> None:
        self.status(message)
        self.state.log(message)

    def _on_storage_changed(self, roots: object) -> None:
        """公共存储卡片变更：落盘模型目录列表并触发模型重扫。"""
        paths = [Path(str(item)) for item in roots] if isinstance(roots, list) else []
        try:
            _llmconfig.set_models_dirs(paths)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            self.storage_card.set_roots([location.path for location in _llmw_storage.locations()])
            return
        # 移除最后一个目录会回落默认目录：把卡片同步到当前生效列表，
        # 否则界面停留在空网格，看不到默认目录已重新生效。
        self.storage_card.set_roots([location.path for location in _llmw_storage.locations()])
        self._refresh_migration_note()
        self.state.refresh_models()

    def _refresh_migration_note(self) -> None:
        note = settings_logic.migration_note()
        self.storage_card.set_note(note)

    def _build_paint_engine(self) -> Card:
        """sd.cpp 引擎卡片：画图 / 视频 / 音频共用一张（同目录、同安装、同一个默认 tag）。

        与推理引擎（llama.cpp）卡片同构；安装 / 设为默认都在设置页完成。
        """
        from fcmd.apps.llm._llmw_qt_paint import build_paint_engine_card

        self.paint_engine_card = build_paint_engine_card(
            self,
            gpu_provider=lambda: _llmw_compat.gpu_status(self.state.hardware, self.state.gpu_mib),
        )
        return self.paint_engine_card

    # ------------------------------------------------------------------ #
    # 下载
    # ------------------------------------------------------------------ #
    def _build_download(self) -> Card:
        """下载参数：源、并发、校验与包含 / 排除掩码；目标仍是模型目录。"""
        card = Card("下载")
        settings = _llmw_models.settings_from_dict(self.state.settings.download)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.addWidget(QLabel("下载源"), 0, 0, Qt.AlignmentFlag.AlignRight)
        self.download_source = QComboBox(card)
        self.download_source.addItems(list(_llmw_models._SOURCE_LABELS.values()))
        self.download_source.setCurrentText(_llmw_models._SOURCE_LABELS.get(settings.source, "ModelScope"))
        grid.addWidget(self.download_source, 0, 1)
        grid.addWidget(QLabel("并发"), 0, 2, Qt.AlignmentFlag.AlignRight)
        self.download_workers = QSpinBox(card)
        self.download_workers.setRange(1, _llmw_models._MAX_WORKERS)
        self.download_workers.setValue(settings.workers)
        grid.addWidget(self.download_workers, 0, 3)
        self.download_verify = QCheckBox("校验 SHA256", card)
        self.download_verify.setChecked(settings.verify)
        grid.addWidget(self.download_verify, 0, 4)
        grid.addWidget(QLabel("同时下载", card), 0, 5, Qt.AlignmentFlag.AlignRight)
        self.download_parallel = QSpinBox(card)
        self.download_parallel.setRange(1, _llmw_models._MAX_PARALLEL)
        self.download_parallel.setValue(settings.parallel)
        self.download_parallel.setToolTip("同时下载的模型数量；超出上限会排队，连接数按并发分摊")
        grid.addWidget(self.download_parallel, 0, 6)
        grid.addWidget(QLabel("包含掩码"), 1, 0, Qt.AlignmentFlag.AlignRight)
        self.download_include = QLineEdit(settings.include, card)
        self.download_include.setPlaceholderText("如 *.gguf，留空为全部")
        grid.addWidget(self.download_include, 1, 1, 1, 3)
        grid.addWidget(QLabel("排除掩码"), 2, 0, Qt.AlignmentFlag.AlignRight)
        self.download_exclude = QLineEdit(settings.exclude, card)
        self.download_exclude.setPlaceholderText("如 *.bin，留空不排除")
        grid.addWidget(self.download_exclude, 2, 1, 1, 3)
        card.body.addLayout(grid)
        self.download_source.currentIndexChanged.connect(lambda _index: self._save_download())
        self.download_workers.valueChanged.connect(lambda _value: self._save_download())
        self.download_verify.toggled.connect(lambda _checked: self._save_download())
        self.download_parallel.valueChanged.connect(lambda _value: self._save_download())
        self.download_include.editingFinished.connect(self._save_download)
        self.download_exclude.editingFinished.connect(self._save_download)
        return card

    def _save_download(self) -> None:
        settings = _llmw_models.DownloadGuiSettings(
            source=_llmw_models._SOURCE_KEYS.get(self.download_source.currentText(), "auto"),
            workers=min(max(self.download_workers.value(), 1), _llmw_models._MAX_WORKERS),
            parallel=min(max(self.download_parallel.value(), 1), _llmw_models._MAX_PARALLEL),
            verify=self.download_verify.isChecked(),
            include=self.download_include.text(),
            exclude=self.download_exclude.text(),
        )
        self.state.settings.download = _llmw_models.settings_to_dict(settings)
        self.state.persist()
        self.state.log("已自动保存下载设置")
        self.status("已自动保存下载设置")

    # ------------------------------------------------------------------ #
    # 转换
    # ------------------------------------------------------------------ #
    def _build_convert(self) -> Card:
        """HF safetensors → GGUF 的持久化参数；转换入口在模型列表行内。"""
        card = Card("模型转换")
        settings = _llmw_convert.convert_settings_from_dict(self.state.settings.convert)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.addWidget(QLabel("转换精度"), 0, 0, Qt.AlignmentFlag.AlignRight)
        self.convert_outtype = QComboBox(card)
        self.convert_outtype.addItems(list(llmconvert.CONVERT_OUTTYPES))
        self.convert_outtype.setCurrentText(settings.outtype)
        grid.addWidget(self.convert_outtype, 0, 1)
        grid.addWidget(QLabel("引擎 tag"), 0, 2, Qt.AlignmentFlag.AlignRight)
        self.convert_tag = QComboBox(card)
        self.convert_tag.setEditable(True)
        self.convert_tag.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        line_edit = self.convert_tag.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText("留空用默认引擎")
        self._refresh_convert_engine_options(settings.tag)
        grid.addWidget(self.convert_tag, 0, 3)
        grid.addWidget(QLabel("解释器"), 1, 0, Qt.AlignmentFlag.AlignRight)
        self.convert_python = QLineEdit(settings.python, card)
        self.convert_python.setPlaceholderText("留空用本应用解释器（缺 torch / safetensors / numpy 时回退其它 Python）")
        grid.addWidget(self.convert_python, 1, 1, 1, 3)
        grid.addWidget(QLabel("转换脚本"), 2, 0, Qt.AlignmentFlag.AlignRight)
        self.convert_script = QLineEdit(settings.script, card)
        self.convert_script.setPlaceholderText(_llmw_convert._SCRIPT_PLACEHOLDER)
        grid.addWidget(self.convert_script, 2, 1, 1, 3)
        pick_script = button("浏览…")
        pick_script.clicked.connect(self._pick_convert_script)
        # 按钮列不参与拉伸，避免被网格拉成整列宽。
        grid.addWidget(pick_script, 2, 4)
        self.convert_overwrite = QCheckBox("覆盖已存在的输出文件", card)
        self.convert_overwrite.setChecked(settings.overwrite)
        grid.addWidget(self.convert_overwrite, 3, 0, 1, 2)
        card.body.addLayout(grid)
        arch_row = QHBoxLayout()
        arch_row.setContentsMargins(0, 0, 0, 0)
        arch_row.setSpacing(8)
        self.convert_arch_button = link_button(
            "查看支持的架构…", tooltip="读取当前转换脚本注册表；不在列表内的架构无法转换"
        )
        self.convert_arch_button.clicked.connect(self._show_supported_architectures)
        arch_row.addWidget(self.convert_arch_button)
        arch_row.addWidget(
            hint_label(
                "llama.cpp 只支持脚本注册表内的架构；自定义架构请指定支持它的脚本 / 源码包，或直接下载官方 GGUF。",
                wrap=True,
            ),
            1,
        )
        card.body.addLayout(arch_row)
        self.convert_outtype.currentIndexChanged.connect(lambda _index: self._save_convert())
        self.convert_tag.currentIndexChanged.connect(lambda _index: self._save_convert())
        if line_edit is not None:
            line_edit.editingFinished.connect(self._save_convert)
        self.convert_python.editingFinished.connect(self._save_convert)
        self.convert_script.editingFinished.connect(self._save_convert)
        self.convert_overwrite.toggled.connect(lambda _checked: self._save_convert())
        return card

    def _convert_tag_text(self) -> str:
        """当前转换引擎 tag（可编辑下拉框文本；空串表示默认引擎）。"""
        return self.convert_tag.currentText().strip()

    def _refresh_convert_engine_options(self, selected: str = "") -> None:
        """把已安装引擎 tag 填进可编辑下拉框，同时保留自定义文本。"""
        if not hasattr(self, "convert_tag"):
            return
        current = selected.strip() or self.convert_tag.currentText().strip()
        self.convert_tag.blockSignals(True)
        self.convert_tag.clear()
        tags = [info.tag for info in settings_logic.installed_engines()]
        self.convert_tag.addItems(tags)
        self.convert_tag.setCurrentText(current)
        self.convert_tag.blockSignals(False)

    def _show_supported_architectures(self) -> None:
        """列出当前转换脚本 / 源码能识别的架构，解释「为什么不支持」。"""
        script_text = self.convert_script.text().strip()
        script = Path(script_text).expanduser() if script_text else None
        archs = llmconvert.supported_architectures(script, tag=self._convert_tag_text() or None)
        if not archs:
            QMessageBox.information(
                self,
                "暂无法读取架构列表",
                "尚未获取到 llama.cpp 源码，或当前转换脚本是未解压的 zip。\n",
                "先执行一次转换让源码就绪，或在「转换脚本」中选择源码目录 / convert_hf_to_gguf.py。",
            )
            return
        QMessageBox.information(
            self,
            "转换脚本支持的架构",
            f"当前脚本共注册 {len(archs)} 个架构：\n\n" + "、".join(archs),
        )

    def _pick_convert_script(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "选择转换脚本或 llama.cpp 源码包",
            "",
            "转换脚本 / 源码包 (*.py *.zip);;所有文件 (*.*)",
        )
        if chosen:
            self.convert_script.setText(chosen)
            self._save_convert()

    def _save_convert(self) -> None:
        settings = _llmw_convert.ConvertSettings(
            outtype=self.convert_outtype.currentText().strip() or llmconvert.CONVERT_OUTTYPES[0],
            tag=self._convert_tag_text(),
            python=self.convert_python.text().strip(),
            script=self.convert_script.text().strip(),
            overwrite=self.convert_overwrite.isChecked(),
        )
        self.state.settings.convert = _llmw_convert.convert_settings_to_dict(settings)
        self.state.persist()
        self._refresh_engine_source()
        self.state.log("已自动保存模型转换设置")
        self.status("已自动保存模型转换设置")

    def _refresh_engine_source(self) -> None:
        """刷新推理引擎卡片上的 llama.cpp 源码状态（显式脚本 / 当前转换引擎 tag）。"""
        script_text = self.convert_script.text().strip()
        if script_text:
            script = Path(script_text).expanduser()
            if script.is_file():
                title = "已指定源码包" if script.suffix.lower() == ".zip" else "已指定转换脚本"
                self.engine_source_tag.set_status(title, "success")
                self.engine_source_tag.setToolTip(f"模型转换使用：{script}")
                return
            self.engine_source_tag.set_status("指定文件不存在", "warning")
            self.engine_source_tag.setToolTip(script_text)
            return
        tag = self._convert_tag_text() or None
        try:
            status = _llmw_convert.convert_source_status(tag)
        except OSError as exc:  # pragma: no cover - 读盘失败时只提示，不阻断设置页
            self.engine_source_tag.set_status("读取失败", "warning")
            self.engine_source_tag.setToolTip(str(exc))
            return
        self.engine_source_tag.set_status(status.title, status.tone)
        self.engine_source_tag.setToolTip(f"引擎 {status.tag} / {status.hint} / {status.path}")

    # ------------------------------------------------------------------ #
    # 引擎
    # ------------------------------------------------------------------ #
    def _build_engines(self) -> Card:
        """推理引擎（llama.cpp）卡片：与 sd.cpp 共用 EngineCard，只换配置。"""
        self.engine_source_tag = StatusTag("", tone="muted", parent=self)
        self.engine_source_tag.setToolTip("当前转换使用的引擎 tag 是否已带 llama.cpp 转换源码")
        self.engines_path = PathRow(value="", directory=True, show_reset=True, browse_text="更改")
        self.engines_path.committed.connect(lambda text: self._save_path("engines_root", text))
        self.engines_path.reset.clicked.connect(lambda: self._save_path("engines_root", None))

        def load() -> list[EngineOption]:
            options: list[EngineOption] = []
            gpu = _llmw_compat.gpu_status(self.state.hardware, self.state.gpu_mib)
            for info in settings_logic.installed_engines():
                variant = settings_logic.engine_variant(info)
                compat = _llmw_compat.engine_compat(variant, gpu=gpu)
                tooltip = f"{info.tag} / {info.exe} / 来源：{info.source or '-'}"
                options.append(
                    EngineOption(
                        tag=info.tag,
                        exe=info.exe,
                        current=info.source == "current",
                        display_name=info.tag,
                        badge=variant,
                        badge_tone="muted" if variant == "CPU" else "accent",
                        badge_tooltip=f"构建类别：{variant} / {settings_logic.variant_hint(variant)}",
                        tooltip=tooltip,
                        compat_label=compat.label,
                        compat_tone=compat.tone,
                        compat_tooltip=f"本机适配：{compat.hint}",
                    )
                )
            return options

        config = EngineCardConfig(
            title="推理引擎（llama.cpp）",
            list_object_name="EngineList",
            empty_text="还没有已安装引擎；用「下载引擎」或「导入压缩包」添加。",
            load=load,
            use=self._use_engine_tag,
            install=lambda _card: self._open_engine_download(),
            install_text="下载引擎",
            install_tooltip="从 GitHub releases 选择 llama.cpp 版本与变体，下载到引擎目录",
            root_text=lambda: str(llmenv.engines_root()),
            row_use_tooltip="把该引擎设为默认引擎",
            row_current_tooltip="当前默认 llama.cpp 引擎",
            manage=lambda _card: self._open_engine_manager(),
            manage_tooltip="备份 / 删除引擎，清理下载缓存与 llama.cpp 源码",
            header_widgets=(self.engine_source_tag,),
            extra_actions=(
                EngineDirAction(
                    text="导入压缩包",
                    tooltip="从本地 llama.cpp / cudart zip 安装引擎；源码包会导入为转换源码",
                    callback=self._import_engine_archive,
                ),
            ),
            root_widget=lambda _parent: self.engines_path,
            refresh_root=self._refresh_engines_root,
        )
        card = EngineCard(config, self)
        self.engine_card = card
        self.engines_list = card.engines_list
        self.engines_empty = card.engines_empty
        self.engine_hint = card.engine_hint
        self.refresh_engine_button = card.refresh_button
        self.download_engine_button = card.install_button
        self.manage_engines_button = card.manage_button
        self.import_engine_archive_button = card.extra_buttons[0]
        return card

    def _refresh_engines_root(self, widget: QWidget) -> None:
        """目录行显示当前配置的引擎根（PathRow 可编辑，其它为只读）。"""
        for _name, path, _source in settings_logic.engine_rows():
            if isinstance(widget, PathRow):
                widget.set_value(path)
            else:
                widget.setText(path)
            break

    # ------------------------------------------------------------------ #
    # 刷新与保存
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """重新读取配置与引擎列表。"""
        self.storage_card.set_roots([location.path for location in _llmw_storage.locations()])
        for _name, path, _source in settings_logic.engine_rows():
            self.engines_path.set_value(path)
        self._refresh_migration_note()
        self.refresh_engines()
        self.refresh_engine_cards()
        self._refresh_convert_engine_options(self._convert_tag_text())
        self._refresh_engine_source()

    def refresh_engine_cards(self) -> None:
        """刷新 sd.cpp 引擎卡（画图 / 视频 / 音频共用同一份列表与默认 tag）。"""
        self.paint_engine_card.refresh()

    def refresh_engines(self) -> None:
        """刷新推理引擎列表（委托共享 EngineCard）。"""
        self.engine_card.refresh()

    def _save_path(self, key: str, value: str | None) -> None:
        """写入目录项并通知其它页（模型目录变化会触发重新扫描）。"""
        try:
            resolved = settings_logic.apply_path(key, value or None)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        label = "模型目录" if key == "models_dir" else "引擎目录"
        self.state.log(f"已自动保存{label}: {resolved}")
        self.status(f"已自动保存{label}：{resolved}")
        self.refresh()
        if key == "models_dir":
            self.state.refresh_models()

    def _set_engine_hint(self, text: str) -> None:
        """显示引擎操作反馈；无内容时不占位。"""
        self.engine_hint.setText(text)
        self.engine_hint.setVisible(bool(text))

    def _use_engine_tag(self, tag: str) -> None:
        """把指定 tag 的引擎设为默认，并刷新列表。"""
        try:
            chosen = settings_logic.select_engine(tag)
        except (OSError, ServeError) as exc:
            QMessageBox.critical(self, "切换失败", str(exc))
            return
        self.state.log(f"默认引擎已切换为 {chosen.tag} · {chosen.exe}")
        self.status(f"默认引擎：{chosen.tag}")
        self._set_engine_hint(f"默认引擎：{chosen.tag} · {chosen.exe}")
        self.refresh_engines()

    def _open_engine_manager(self) -> None:
        """打开（或聚焦）引擎管理对话框：备份 / 删除 / 清理集中在这里。"""
        if self._engine_manager_dialog is None:
            from fcmd.apps.llm._llmw_qt_engine_manage import EngineManageDialog

            dialog = EngineManageDialog(self)
            dialog.changed.connect(self._engine_manager_changed)
            self._engine_manager_dialog = dialog
        dialog = self._engine_manager_dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _engine_manager_changed(self) -> None:
        """管理对话框删除 / 清理后刷新引擎列表与源码状态。"""
        self.status("引擎目录已更新")
        self.refresh()

    def _ask_engine_tag(self, default: str) -> str | None:
        """询问引擎标签并做安全校验（只允许单级目录名）。"""
        text, accepted = QInputDialog.getText(self, "引擎标签", "标签（用于引擎目录名）：", text=default)
        if not accepted:
            return None
        tag = text.strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", tag):
            QMessageBox.warning(
                self, "标签不合法", "标签只能包含字母、数字、点、下划线和短横线，且不能包含路径分隔符。"
            )
            return None
        return tag

    def _after_engine_install(self, info: EngineInfo, action: str) -> None:
        """注册 / 安装成功后切换默认引擎并刷新列表。"""
        try:
            settings_logic.select_engine(info.tag)
        except (OSError, ServeError) as exc:
            QMessageBox.critical(self, "切换失败", str(exc))
            return
        self.state.log(f"{action}引擎 {info.tag} · {info.exe}")
        self._set_engine_hint(f"{action}引擎：{info.tag} · {info.exe}")
        self.status(f"{action}引擎：{info.tag}")
        self.refresh()

    def _open_engine_download(self) -> None:
        """打开版本列表对话框；安装成功后刷新已安装引擎列表。"""
        from fcmd.apps.llm._llmw_qt_engine_install import EngineDownloadDialog

        dialog = EngineDownloadDialog(self)
        dialog.installed.connect(self._on_engine_downloaded)
        dialog.exec()

    def _on_engine_downloaded(self, tag: str) -> None:
        """下载安装完成：刷新列表、写状态栏与日志。"""
        self.state.log(f"已下载引擎：{tag}")
        self.status(f"已下载引擎：{tag}")
        self._set_engine_hint(f"已下载引擎：{tag}")
        self.refresh()

    def _import_engine_dir(self) -> None:
        """选择已编译 llama.cpp 目录，复制到引擎目录并设为默认引擎。"""
        chosen = QFileDialog.getExistingDirectory(self, "选择 llama.cpp 目录")
        if not chosen:
            return
        source = Path(chosen)
        tag = self._ask_engine_tag(source.name or "local")
        if tag is None:
            return
        try:
            info, _actual = settings_logic.install_offline(tag, source, copy_directory=True)
        except (OSError, ServeError) as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self._after_engine_install(info, "已导入")

    def _import_engine_archive(self) -> None:
        """本地 zip：引擎包安装为引擎，源码包导入为转换源码（不安装成引擎）。"""
        paths, _selected = QFileDialog.getOpenFileNames(
            self,
            "选择 llama.cpp 压缩包",
            "",
            "压缩包 (*.zip);;所有文件 (*.*)",
        )
        if not paths:
            return
        archives = [Path(path) for path in paths]
        inspections: list[llmengine.ZipInspection] = []
        try:
            for archive in archives:
                inspections.extend(settings_logic.inspect_offline(archive))
        except (OSError, ServeError) as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        sources = [item for item in inspections if item.kind == "source"]
        engines = [item for item in inspections if item.kind != "source"]
        if sources and engines:
            QMessageBox.information(
                self,
                "请分开导入",
                "选择中同时包含引擎包与 llama.cpp 源码包；请先导入引擎包，再单独导入源码包。",
            )
            return
        if sources:
            self._import_convert_source(sources)
            return
        default = next((item.tag for item in engines if item.tag), archives[0].stem)
        tag = self._ask_engine_tag(default)
        if tag is None:
            return
        try:
            info = settings_logic.install_archives(tag, archives)
        except (OSError, ServeError) as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self._after_engine_install(info, "已导入")

    def _import_convert_source(self, sources: list[llmengine.ZipInspection]) -> None:
        """把源码 zip 复制到引擎根并设为转换源码；不产生引擎条目。"""
        if len(sources) > 1:
            QMessageBox.information(self, "一次一个源码包", "一次只能导入一个 llama.cpp 源码包；请分开选择。")
            return
        source = sources[0]
        if not confirm(
            self,
            "导入 llama.cpp 源码",
            f"{source.path.name} 是 llama.cpp 源码包，不会安装成引擎。\n将复制到引擎根并设为模型转换源码，确认继续？",
        ):
            return
        try:
            dest = llmconvert.import_source_archive(source.path)
        except (OSError, ServeError) as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self.convert_script.setText(str(dest))
        self._save_convert()
        self.state.log(f"已导入转换源码包：{dest}")
        self.status(f"已导入转换源码包：{dest.name}")
        self._set_engine_hint(f"转换源码已就绪：{dest}")


def _disk_location(location: _llmw_storage.StorageLocation) -> DiskLocation:
    """llmw 目录模型 → 公共磁盘卡展示模型（保留盘符 / 文件系统 / 下载目标文案）。"""
    return DiskLocation(
        path=location.path,
        label=location.disk_label,
        fstype=location.fstype,
        total=location.total,
        free=location.free,
        exists=location.exists,
        primary=location.primary,
        path_text=_llmw_storage.display_path(location.path),
        primary_label="下载目标",
    )
