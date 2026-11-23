"""llmw 模型行卡 / 详情视图（ADR 0046 P4）。"""

from __future__ import annotations

import fcmd.apps.llm._llmw_qt_center as _owner

from ._compat import (
    Any,
    Callable,
    ItemRow,
    ModelFile,
    ModelInfo,
    ModelSummary,
    Path,
    QWidget,
)


class _ModelItemRow(ItemRow):  # pragma: no cover - Qt 控件
    """浏览结果行：共享 :class:`ItemRow` 骨架 + 模型自己的标签与行内动作。

    标题是仓库名，副标题是参数 / 体积，右侧是数值摘要与下载 / 转换 / 量化 /
    快捷启动按钮；发布方、发布时间、量化精度与状态胶囊都往共享骨架的插槽里加。
    行本身只放关键内容，展开后在行下方显示详细信息，列表可以铺满整页宽度。
    """

    downloadRequested = _owner.Signal(str)
    convertRequested = _owner.Signal(str)
    quantizeRequested = _owner.Signal(str)
    startRequested = _owner.Signal(str)

    def __init__(  # noqa: PLR0912, PLR0913 - 行状态位由列表统一透传
        self,
        item: ModelSummary,
        parent: QWidget | None = None,
        *,
        installed: bool = False,
        convertible: bool = False,
        converted: bool = False,
        convert_blocked: str = "",
        quant_tags: tuple[str, ...] = (),
        quantizable: bool = False,
        quant_blocked: bool = False,
        startable: bool = False,
        paint_startable: bool = False,
        default_quant: str = "",
        serve_path: Path | None = None,
        downloadable: bool = True,
        incomplete: bool = False,
        owner: str = "",
        size_text: str | None = None,
        same_name: int = 1,
        progress_text: str = "",
    ) -> None:
        title, subtitle, metrics = _owner._llmw_models.model_item_lines(item)
        if size_text:
            metrics = size_text
        super().__init__(
            title,
            subtitle,
            elide=_owner.Qt.TextElideMode.ElideMiddle,
            expand_hint="展开详细信息",
            collapse_hint="收起详细信息",
            # 标题显示的是模型名（省略在行内），复制按钮给完整仓库名（组织/模型）。
            copy_text=item.repo,
            parent=parent,
        )
        self.repo = item.repo
        self.serve_path = serve_path
        self._downloadable = downloadable
        self._convertible = convertible
        self._converted = converted
        self._convert_blocked = convert_blocked
        self._quantizable = quantizable
        self._quant_blocked = quant_blocked
        self._incomplete = incomplete
        self.setToolTip(item.repo)
        self.title_label.setToolTip(item.repo)

        if owner:
            owner_label = _owner.QLabel(owner, self.header)
            owner_label.setObjectName("OwnerTag")
            owner_label.setToolTip(f"发布方 {owner}")
            self.add_title_widget(owner_label)
        published = _owner._llmw_models.model_published_text(item)
        if published:
            published_label = _owner.QLabel(published, self.header)
            published_label.setObjectName("Hint")
            published_label.setToolTip(f"模型发布时间：{_owner._llmw_models.format_timestamp(item.updated)}")
            self.add_note_widget(published_label)
        if installed and not incomplete:
            self.add_tag(_owner.StatusTag("已安装", tone="success", parent=self.header))
        elif incomplete:
            self.add_tag(_owner.StatusTag("部分下载", tone="warning", parent=self.header))
        if not downloadable:
            self.add_tag(_owner.StatusTag("仅本地", tone="muted", parent=self.header))
        if any(tag.upper().startswith("UD-") for tag in quant_tags):
            self.add_tag(_owner.StatusTag("UD", tone="accent", parent=self.header))
        if same_name > 1:
            self.add_tag(_owner.StatusTag(f"同名 {same_name}", tone="muted", parent=self.header))
        if converted:
            self.add_tag(_owner.StatusTag("已转换", tone="success", parent=self.header))
        elif convert_blocked:
            blocked_tag = _owner.StatusTag("不可转换", tone="warning", parent=self.header)
            blocked_tag.setToolTip(convert_blocked)
            self.add_tag(blocked_tag)
        if quant_blocked:
            self.add_tag(_owner.StatusTag("不可量化", tone="warning", parent=self.header))
        visible_tags = list(quant_tags)
        if default_quant in visible_tags:
            visible_tags.remove(default_quant)
            visible_tags.insert(0, default_quant)
        for tag in visible_tags[:4]:
            chip = _owner.QLabel(tag, self.header)
            if tag == default_quant:
                chip.setText(f"{tag} · 上次启动")
                chip.setToolTip("快捷启动默认使用该量化精度（记住用户上次启动过的精度）")
            else:
                chip.setToolTip(f"已量化精度 {tag}")
            chip.setObjectName("QuantTag")
            self.add_tag(chip)
        hidden_tags = visible_tags[4:]
        if hidden_tags:
            hidden_chip = _owner.QLabel(f"+{len(hidden_tags)}", self.header)
            hidden_chip.setObjectName("QuantTag")
            hidden_chip.setToolTip("其他量化：" + "、".join(hidden_tags))
            self.add_tag(hidden_chip)
        if incomplete and progress_text:
            progress = _owner.QLabel(progress_text, self.header)
            progress.setObjectName("DownloadProgress")
            progress.setToolTip(progress_text)
            self.add_tag(progress)

        self.set_metrics(metrics)
        self.convert_button: Any = None
        if convertible and not converted:
            # 次要动作（转换 / 量化）用 Ghost，主操作（启动服务 / 下载）保持填充色，
            # 一行有多个按钮时不会互相抢焦点。
            self.convert_button = _owner.button("转换", kind="ghost")
            self.convert_button.clicked.connect(lambda: self.convertRequested.emit(self.repo))
            self.add_action(self.convert_button)
        self.quant_button: Any = None
        if quantizable:
            self.quant_button = _owner.button("量化", kind="ghost")
            self.quant_button.clicked.connect(lambda: self.quantizeRequested.emit(self.repo))
            self.add_action(self.quant_button)
        self.start_button: Any = None
        if startable:
            self.start_button = _owner.button("启动服务", kind="primary")
            self.start_button.setToolTip(
                "启动画图服务（sd.cpp）：切到运行页「画图」并加载该扩散模型"
                if paint_startable
                else "选择量化精度并启动服务；默认使用上次启动过的精度"
            )
            self.start_button.clicked.connect(lambda: self.startRequested.emit(self.repo))
            self.add_action(self.start_button)
        self.download_button: Any = None
        # 已安装且无 .part 的模型视为已全部下载完成：不再显示下载按钮。
        if downloadable and not (installed and not incomplete):
            self.download_button = _owner.button("继续下载" if incomplete else "下载")
            if incomplete:
                self.download_button.setToolTip("继续下载未完成的文件（.part 保留）")
            self.download_button.clicked.connect(lambda: self.downloadRequested.emit(self.repo))
            self.add_action(self.download_button)

    @property
    def downloadable(self) -> bool:
        """能否对应到远程仓库；仅本地条目为 False，详情不读在线清单。"""
        return self._downloadable

    def set_download_state(self, *, busy: bool, active: bool, queued: bool = False) -> None:
        """下载中禁用；未完成显示「继续下载」，未安装显示「下载」，排队时显示「排队中…」。"""
        if self.download_button is None:
            return
        self.download_button.setEnabled(not busy)
        if active:
            text = "下载中…"
        elif queued:
            text = "排队中…"
        else:
            text = "继续下载" if self._incomplete else "下载"
        self.download_button.set_text(text)

    def set_convert_state(self, *, busy: bool, active: bool) -> None:
        """转换中禁用按钮；已转换行保持不可点击的「已转换」。"""
        if self.convert_button is None:
            return
        if self._converted:
            self.convert_button.setEnabled(False)
            self.convert_button.set_text("已转换", tone="success")
            return
        if self._convert_blocked:
            self.convert_button.setEnabled(False)
            self.convert_button.set_text("不可转换")
            return
        self.convert_button.setEnabled(not busy)
        self.convert_button.set_text("转换中…" if active else "转换")

    def set_quant_state(self, *, busy: bool, active: bool) -> None:
        """量化中禁用按钮；无候选时保持不可量化的禁用态。"""
        if self.quant_button is None:
            return
        if self._quant_blocked:
            self.quant_button.setEnabled(False)
            self.quant_button.set_text("不可量化")
            return
        self.quant_button.setEnabled(not busy)
        self.quant_button.set_text("量化中…" if active else "量化")

    def set_serve_state(self, *, busy: bool, running: bool) -> None:
        """快捷启动按钮：任务进行中禁用，已运行时显示「已启动」。

        禁用态的主按钮底色会变成浅色（``$surface_hover``），图标必须同步换成
        ``muted``：否则浅色主题下保留下来的 ``on_accent`` 白勾在白底上几乎看不见。
        """
        if self.start_button is None:
            return
        enabled = not busy and not running
        self.start_button.setEnabled(enabled)
        self.start_button.set_text(
            "已启动" if running else "启动服务",
            tone="on_accent" if enabled else "muted",
        )


class _ModelDetailView(_owner.QWidget):  # pragma: no cover - Qt 控件
    """展开行里的详情：「详细信息」与「文件列表」两个页签。

    同一个实例在行之间复用，展开哪一行就挂到哪一行下面；元数据先展示，文件
    清单在第一次切到「文件列表」时才后台读取（仓库文件可能上百个，不拖慢展开）。
    ``heightChanged`` 供列表在页签高度变化时重算展开行高度。
    """

    heightChanged = _owner.Signal()

    def __init__(
        self,
        load_files: Callable[[str], list[ModelFile]] | None = None,
        download_files: Callable[[str, tuple[ModelFile, ...]], None] | None = None,
        local_dir: Callable[[str], Path | None] | None = None,
        parent: _owner.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.repo: str | None = None
        self._load_files = load_files
        self._download_files = download_files
        # 仓库在本地模型目录里的落盘位置：文件列表据此标「已下载 / 部分下载 / 未下载」。
        self._local_dir = local_dir
        self._files_root: Path | None = None
        self._files_repo: str | None = None
        self._files_list: list[ModelFile] = []
        self._files_loading = False
        self._online = True
        self._files: dict[str, ModelFile] = {}
        layout = _owner.QVBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 10)
        layout.setSpacing(6)

        self.error = _owner.hint_label("")
        self.error.setVisible(False)
        layout.addWidget(self.error)

        # 与运行页同一套药丸页签：没有页签上方的分隔线与 QTabBar 基线；
        # 容器底色用 $surface，与展开它的行卡同色（否则详情区会露出窗口底色）。
        self.tabs = _owner.tab_widget(self, object_name="RowTabs")
        layout.addWidget(self.tabs)

        meta_page = _owner.QWidget(self.tabs)
        meta_layout = _owner.QVBoxLayout(meta_page)
        meta_layout.setContentsMargins(0, 6, 0, 0)
        meta_layout.setSpacing(4)
        self.meta_grid = _owner.QGridLayout()
        self.meta_grid.setContentsMargins(0, 0, 0, 0)
        self.meta_grid.setHorizontalSpacing(10)
        self.meta_grid.setVerticalSpacing(2)
        self.meta_values: dict[str, _owner.QLabel] = {}
        for index, name in enumerate(
            (
                "中文名",
                "组织",
                "任务",
                "框架",
                "架构",
                "模型类型",
                "语言",
                "许可证",
                "下载",
                "星标",
                "大小",
                "版本",
                "更新时间",
                "推理支持",
            )
        ):
            row, column = divmod(index, 2)
            cell = _owner.QHBoxLayout()
            cell.setContentsMargins(0, 0, 0, 0)
            cell.addWidget(_owner.hint_label(f"{name}：", wrap=False))
            value = _owner.QLabel("-", meta_page)
            value.setTextInteractionFlags(_owner.Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            cell.addWidget(value, 1)
            self.meta_grid.addLayout(cell, row, column)
            self.meta_values[name] = value
        self.meta_grid.setColumnStretch(0, 1)
        self.meta_grid.setColumnStretch(1, 1)
        meta_layout.addLayout(self.meta_grid)
        meta_layout.addStretch(1)
        self.tabs.addTab(meta_page, "详细信息")

        files_page = _owner.QWidget(self.tabs)
        files_layout = _owner.QVBoxLayout(files_page)
        files_layout.setContentsMargins(0, 6, 0, 0)
        files_layout.setSpacing(4)
        self.files_status = _owner.hint_label("", wrap=True)
        self.files_status.setVisible(False)
        files_layout.addWidget(self.files_status)
        self.files_table = _owner.QTableWidget(0, 3, files_page)
        self.files_table.setObjectName("ModelFiles")
        self.files_table.setHorizontalHeaderLabels(["文件", "大小", "状态"])
        self.files_table.verticalHeader().setVisible(False)
        self.files_table.setEditTriggers(_owner.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.files_table.setSelectionBehavior(_owner.QAbstractItemView.SelectionBehavior.SelectRows)
        self.files_table.setSelectionMode(_owner.QAbstractItemView.SelectionMode.ExtendedSelection)
        # 行卡展开区里不另起盒子：表格与详情区同底色，只有表头 / 选中行有底色。
        self.files_table.setAlternatingRowColors(False)
        self.files_table.setShowGrid(False)
        header = self.files_table.horizontalHeader()
        header.setSectionResizeMode(0, _owner.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, _owner.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, _owner.QHeaderView.ResizeMode.ResizeToContents)
        self.files_table.setFixedHeight(220)
        self.files_table.itemSelectionChanged.connect(self._sync_files_actions)
        files_layout.addWidget(self.files_table)

        files_actions = _owner.QHBoxLayout()
        files_actions.setContentsMargins(0, 0, 0, 0)
        files_actions.addWidget(
            _owner.hint_label(
                "Ctrl / Shift 多选后可单独下载（如 VAE / 文本编码器）；「下载全部」只补未下载的", wrap=False
            )
        )
        files_actions.addStretch(1)
        self.files_download_button = _owner.button("下载所选", kind="ghost")
        self.files_download_button.setToolTip("只下载文件列表里选中的文件，不影响已下载的量化")
        self.files_download_button.setVisible(False)
        self.files_download_button.clicked.connect(self._download_selected)
        files_actions.addWidget(self.files_download_button)
        self.files_download_all_button = _owner.button("下载全部", kind="primary")
        self.files_download_all_button.setToolTip("下载列表里所有尚未下载的文件（已下载的跳过）")
        self.files_download_all_button.setVisible(False)
        self.files_download_all_button.clicked.connect(self._download_all)
        files_actions.addWidget(self.files_download_all_button)
        files_layout.addLayout(files_actions)
        # 表格高度固定（行高度可控），剩余空间留在下方，文件列表紧跟页签顶部。
        files_layout.addStretch(1)
        self.tabs.addTab(files_page, "文件列表")
        self.tabs.currentChanged.connect(self._on_tab_changed)

    # ------------------------------------------------------------------ #
    # 详细信息
    # ------------------------------------------------------------------ #
    def reset(self, repo: str, online: bool = True) -> None:
        """切换模型时清空旧内容，等后台详情回填；文件清单按需重新拉取。

        ``online=False``（仅本地模型）时不读在线清单：仓库名可能只是本地目录名，
        请求必然失败，文件列表只提示「仅本地」而不报错。
        """
        self.repo = repo
        self._online = online
        self.error.setVisible(False)
        self.error.setText("")
        if not online:
            self.error.setText("仅本地模型：无法确定远程仓库，不读取在线详情与文件清单。")
            self.error.setVisible(True)
        for value in self.meta_values.values():
            value.setText("-")
        if repo != self._files_repo:
            self._files_repo = None
            self._files = {}
            self._files_list = []
            self._files_root = None
            self.files_table.setRowCount(0)
            self.files_status.setText("")
            self.files_status.setVisible(False)
        else:
            # 同一个仓库重新展开（例如刚下载完）：清单不重拉，只刷新状态列。
            self._sync_file_states()
        self._sync_files_actions()
        if self.tabs.currentIndex() == 1:
            self._ensure_files()

    def set_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(True)
        self.heightChanged.emit()

    def render(self, info: ModelInfo) -> None:
        """回填仓库元数据。"""
        self.repo = info.summary.repo
        self.error.setVisible(False)
        self.error.setText("")
        for name, value in _owner._llmw_models.detail_pairs(info):
            if name in self.meta_values:
                self.meta_values[name].setText(value)
        self.heightChanged.emit()

    # ------------------------------------------------------------------ #
    # 文件列表
    # ------------------------------------------------------------------ #
    def _on_tab_changed(self, index: int) -> None:
        if index == 1:
            self._ensure_files()
        self.heightChanged.emit()

    def _ensure_files(self) -> None:
        repo = self.repo
        if not repo or self._files_loading:
            return
        if self._files_repo == repo:
            # 清单已读过：只按当前文件系统刷新状态（下载完成后回到这个页签）。
            self._sync_file_states()
            return
        if not self._online:
            self._files = {}
            self.files_table.setRowCount(0)
            self.files_status.setText("仅本地模型：无法确定远程仓库，不读取在线文件清单。")
            self.files_status.setVisible(True)
            self._sync_files_actions()
            self.heightChanged.emit()
            return
        if self._load_files is None:
            self.files_status.setText("当前没有可用的文件清单来源。")
            self.files_status.setVisible(True)
            return
        self._files_loading = True
        self._files_repo = repo
        self.files_status.setText(f"正在读取 {repo} 的文件清单…")
        self.files_status.setVisible(True)
        _owner.submit(
            self._load_files,
            repo,
            on_done=lambda files: self._files_ready(repo, files),
            on_error=lambda message: self._files_failed(repo, message),
            on_finished=self._files_finished,
        )

    def _files_finished(self) -> None:
        self._files_loading = False

    def _files_ready(self, repo: str, files: list[ModelFile]) -> None:
        if repo != self.repo:
            return
        # 记住这份清单属于哪个仓库：再次展开同一个仓库时可以只刷新状态、不重拉在线清单。
        self._files_repo = repo
        self.files_table.setRowCount(0)
        self._files = {}
        self._files_list = list(files)
        self._files_root = self._resolve_files_root(repo)
        total = 0
        for file in files:
            row = self.files_table.rowCount()
            self.files_table.insertRow(row)
            name = _owner.QTableWidgetItem(str(file.path))
            name.setToolTip(str(file.path))
            self.files_table.setItem(row, 0, name)
            self._files[str(file.path)] = file
            size = int(getattr(file, "size", 0) or 0)
            total += size
            size_item = _owner.QTableWidgetItem(_owner.human_size(size) if size else "-")
            size_item.setTextAlignment(_owner.Qt.AlignmentFlag.AlignRight | _owner.Qt.AlignmentFlag.AlignVCenter)
            self.files_table.setItem(row, 1, size_item)
            self.files_table.setItem(row, 2, self._state_item(file))
        pending = len(_owner._llmw_models.files_needing_download(self._files_root, self._files_list))
        downloaded = len(self._files_list) - pending
        summary = f"共 {len(files)} 个文件" + (f" · 合计 {_owner.human_size(total)}" if total else "")
        if self._files_root is not None:
            summary += f" · 已下载 {downloaded} / 未下载 {pending}"
        self.files_status.setText(summary)
        self.files_status.setVisible(True)
        self._sync_files_actions()
        self.heightChanged.emit()

    def _files_failed(self, repo: str, message: str) -> None:
        if repo != self.repo:
            return
        self._files_repo = None
        self._files = {}
        self._files_list = []
        self._files_root = None
        self.files_table.setRowCount(0)
        self.files_status.setText(f"读取文件清单失败：{message}")
        self.files_status.setVisible(True)
        self._sync_files_actions()
        self.heightChanged.emit()

    def _selected_files(self) -> tuple[ModelFile, ...]:
        """文件列表当前选中的文件（按行号排序，路径从 ``_files`` 映射取回）。"""
        model = self.files_table.selectionModel()
        rows = sorted({index.row() for index in model.selectedRows()}) if model is not None else []
        selected: list[ModelFile] = []
        for row in rows:
            item = self.files_table.item(row, 0)
            file = self._files.get(item.text()) if item is not None else None
            if file is not None:
                selected.append(file)
        return tuple(selected)

    def _resolve_files_root(self, repo: str) -> Path | None:
        """仓库在本地的落盘目录（本地位置未知时为 ``None``）。"""
        if self._local_dir is None or not repo:
            return None
        return self._local_dir(repo)

    def _state_item(self, file: ModelFile) -> _owner.QTableWidgetItem:
        """文件状态列：已下载 / 部分下载 / 未下载（本地目录未知时 ``-``）。"""
        state = _owner._llmw_models.file_local_state(self._files_root, file.path)
        item = _owner.QTableWidgetItem(state)
        item.setTextAlignment(_owner.Qt.AlignmentFlag.AlignRight | _owner.Qt.AlignmentFlag.AlignVCenter)
        item.setToolTip(
            "本地已存在完整文件"
            if state == _owner._llmw_models.FILE_STATE_DONE
            else "有 .part 断点，尚未下完"
            if state == _owner._llmw_models.FILE_STATE_PARTIAL
            else "本地没有这个文件"
            if state == _owner._llmw_models.FILE_STATE_MISSING
            else "无法确定本地目录"
        )
        return item

    def refresh_file_states(self) -> None:
        """按照前文件系统刷新每一行的状态列（不重新拉清单；下载完成后由页面调用）。"""
        self._sync_file_states()

    def _sync_file_states(self) -> None:
        if not self._files_list:
            return
        # 仓库 / 模型目录可能变化（设置里改过模型目录），每次刷新都重新解析。
        self._files_root = self._resolve_files_root(self.repo or "")
        for row, file in enumerate(self._files_list):
            item = self._state_item(file)
            old = self.files_table.item(row, 2)
            if old is None:
                self.files_table.setItem(row, 2, item)
            else:
                old.setText(item.text())
                old.setToolTip(item.toolTip())
        self._sync_files_actions()

    def _pending_files(self) -> tuple[ModelFile, ...]:
        """清单里还没下载完的文件（「下载全部」用）。"""
        return _owner._llmw_models.files_needing_download(self._files_root, self._files_list)

    def _sync_files_actions(self) -> None:
        """按「是否有清单 / 是否选中 / 还剩多少未下载」启用两个下载动作。"""
        visible = self._online and self._download_files is not None and bool(self._files)
        self.files_download_button.setVisible(visible)
        self.files_download_button.setEnabled(visible and bool(self._selected_files()))
        pending = self._pending_files() if visible else ()
        self.files_download_all_button.setVisible(visible)
        self.files_download_all_button.setEnabled(bool(pending))
        self.files_download_all_button.setToolTip(
            f"下载 {len(pending)} 个尚未下载的文件（已下载的跳过）" if pending else "全部文件已下载"
        )

    def _download_selected(self) -> None:
        repo = self.repo
        files = self._selected_files()
        if not repo or not files or self._download_files is None:
            return
        self._download_files(repo, files)

    def _download_all(self) -> None:
        """只补下「还没下载完」的文件：已下载的跳过，``.part`` 继续续传。"""
        repo = self.repo
        pending = self._pending_files()
        if not repo or not pending or self._download_files is None:
            return
        self._download_files(repo, pending)
