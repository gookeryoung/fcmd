"""wslman 窗口方法（ADR 0046 P2/P3 机械迁移）。"""

from __future__ import annotations

import fcmd.apps.wslman._wslmanw_qt as _owner

from ._compat import (  # noqa: F401
    Any,
    Distro,
    ImageEntry,
    ItemRow,
    Mapping,
    Path,
    QDialog,
    QListWidgetItem,
    QPushButton,
    QWidget,
    Sequence,
    _files,
    _initenv,
    _open,
    threading,
)


class SettingsMixin:
    """ADR 0046 P2/P3 迁移的窗口方法。"""

    def _build_settings_page(self) -> QWidget:
        """设置页：工作目录 + 下载源 / 校验 + 打开方式。

        工作目录排在最上方（用户最常改的一项）；下载 / 备份 / 安装目录不再单独配置，
        也不在界面上逐项展示，统一按工作目录推演。页面用 :class:`PageScroll` 包裹，
        窗口变矮时滚动而不是压扁控件。
        """
        page = _owner.PageBody(self, spacing=0)
        outer = page.body
        scroll = _owner.PageScroll(page)
        outer.addWidget(scroll)
        layout = scroll.body

        layout.addWidget(self._build_work_card())

        download = _owner.Card("下载")
        form = _owner.FormGrid(download)
        self.mirror_ids = _owner._mirrors.identifiers()
        self.mirror = _owner.QComboBox(download)
        self.mirror.addItems([_owner._mirrors.label(ident) for ident in self.mirror_ids])
        index = self.mirror_ids.index(self.settings.mirror) if self.settings.mirror in self.mirror_ids else 0
        self.mirror.setCurrentIndex(index)
        self.mirror.setToolTip("国内镜像源；条目缺少 sha256 时只走官方源")
        self.mirror.currentIndexChanged.connect(self._on_mirror_changed)

        self.expected_sha = _owner.QLineEdit("", download)
        self.expected_sha.setPlaceholderText("可选：64 位十六进制摘要；留空用官方清单")
        self.expected_sha.setToolTip("只对下一次下载生效，不写入配置")

        # 并发下载上限：默认 2 个（保守值），超出上限的任务在队列里排队、可单独取消。
        self.download_limit_combo = _owner.QComboBox(download)
        for value in range(_owner.w.MIN_DOWNLOADS, _owner.w.MAX_DOWNLOADS + 1):
            self.download_limit_combo.addItem(f"{value} 个", value)
        self.download_limit_combo.setCurrentIndex(
            max(0, _owner.w.download_limit(self.settings.max_downloads) - _owner.w.MIN_DOWNLOADS)
        )
        self.download_limit_combo.setToolTip("同时进行的镜像下载数上限；其余任务排队等待，也能单独取消")
        self.download_limit_combo.currentIndexChanged.connect(self._on_download_limit_changed)

        # 同一行两对：下载源 + 并发上限；校验单独一行（摘要较长，需要整行宽度）。
        form.add_pair("下载源", self.mirror, "并发下载", self.download_limit_combo)
        form.add_pair("校验 sha256", self.expected_sha)
        download.body.addWidget(form)
        download.body.addWidget(
            _owner.hint_label(
                f"镜像归档统一存到工作目录的 download 子目录（见上方「工作目录」卡片）。{_owner.w._DOWNLOAD_HINT}"
            )
        )
        layout.addWidget(download)

        layout.addWidget(self._build_openers_card())
        layout.addStretch(1)
        return page

    def _build_openers_card(self) -> _owner.Card:
        """「打开方式」卡片：IDE / AI Agent 两类的当前条目与增删改。

        条目在设置页维护，文件页只按当前选中的条目启动；选择与自定义模板都写进
        ``wslmanw.json``，下次启动直接沿用。右上「智能检测」扫描宿主机与当前实例，
        只补全没被用户手改过的条目。
        """
        card = _owner.Card("打开方式")
        self.opener_detect_button = _owner.button("智能检测", kind="ghost", icon="search")
        self.opener_detect_button.setToolTip(
            "在 Windows 宿主机（PATH 与常见安装目录）和当前 WSL 实例内（command -v）"
            "查找已安装的 IDE / AI Agent，补全没被手动改过的命令；手动改过的条目不会被覆盖。"
        )
        self.opener_detect_button.clicked.connect(lambda: self._detect_openers(force=True))
        card.add_title_action(self.opener_detect_button)
        form = _owner.FormGrid(card)
        self.opener_combos: dict[str, _owner.QComboBox] = {}
        self.opener_tags: dict[str, _owner.StatusTag] = {}
        self.opener_edit_buttons: dict[str, QPushButton] = {}
        self.opener_delete_buttons: dict[str, QPushButton] = {}
        for kind in _owner._open.KINDS:
            row = _owner.QWidget(card)
            row_layout = _owner.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            combo = _owner.QComboBox(row)
            # 不给固定大宽度：窄窗口下这一行还要放状态胶囊与三个按钮，固定 220px
            # 会把卡片最小宽度撑出视口（内容被右边界裁掉）。让下拉框吃剩余宽度，
            # 长条目靠 tooltip 与下拉列表查看。
            combo.setMinimumWidth(120)
            combo.setSizePolicy(_owner.QSizePolicy.Policy.Expanding, _owner.QSizePolicy.Policy.Fixed)
            combo.setSizeAdjustPolicy(_owner.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(10)
            combo.setToolTip(f"文件页「用 {_owner._open.KIND_LABELS[kind]} 打开」使用的条目")
            combo.currentIndexChanged.connect(lambda _index, item=kind: self._on_opener_selected(item))
            row_layout.addWidget(combo, 1)
            tag = _owner.StatusTag("", tone="muted", parent=row)
            tag.setVisible(False)
            row_layout.addWidget(tag)
            add = _owner.button("添加…", kind="ghost")
            add.setToolTip(f"新增自定义 {_owner._open.KIND_LABELS[kind]}打开方式")
            add.clicked.connect(lambda _checked=False, item=kind: self._opener_add(item))
            row_layout.addWidget(add)
            edit = _owner.button("编辑…", kind="ghost")
            edit.setToolTip("修改选中条目的名称、运行位置或命令模板")
            edit.clicked.connect(lambda _checked=False, item=kind: self._opener_edit(item))
            row_layout.addWidget(edit)
            remove = _owner.button("删除", kind="danger")
            remove.setToolTip("删除选中的条目（内置项也可以删除）")
            remove.clicked.connect(lambda _checked=False, item=kind: self._opener_delete(item))
            row_layout.addWidget(remove)
            form.add_row(_owner._open.KIND_LABELS[kind], row)
            self.opener_combos[kind] = combo
            self.opener_tags[kind] = tag
            self.opener_edit_buttons[kind] = edit
            self.opener_delete_buttons[kind] = remove
            self._reload_opener_combo(kind)
        card.body.addWidget(form)
        card.body.addWidget(
            _owner.hint_label(
                "模板占位符：{dir} 实例内绝对路径、{unc} Windows UNC 路径、{distro} 实例名、{home} 实例家目录。"
                "Windows 宿主机条目需要 {dir} 或 {unc}；WSL 内条目以当前目录启动，可不写占位符。"
            )
        )
        self.opener_detect_hint = _owner.hint_label("智能检测会检查宿主机与本页当前实例；切换实例后自动重检。")
        card.body.addWidget(self.opener_detect_hint)
        self._sync_opener_actions()
        return card

    def _reload_opener_combo(self, kind: str) -> None:
        combo = self.opener_combos[kind]
        current = _owner.w.selected_opener(self.settings, kind)
        combo.blockSignals(True)
        combo.clear()
        for opener in _owner.w.openers_for(self.settings, kind):
            combo.addItem(self._opener_label(opener), opener.key)
            combo.setItemData(combo.count() - 1, opener.command, _owner.Qt.ItemDataRole.ToolTipRole)
        if current is not None:
            combo.setCurrentIndex(max(combo.findData(current.key), 0))
        combo.blockSignals(False)
        tag = self.opener_tags[kind]
        tag.setVisible(current is not None)
        if current is not None:
            label, tone, detail = self._opener_install_state(current)
            tag.set_status(label, tone)
            tag.setToolTip(f"{current.name} 的命令模板：{current.command}\n{detail}")

    def _opener_install_state(self, opener: _owner._open.Opener) -> tuple[str, str, str]:
        """条目在当前环境（宿主机 / WSL 内）的安装状态：文案、色调与 tooltip。"""
        builtin = any(item.key == opener.key for item in _owner._open.default_openers(opener.kind))
        if not builtin:
            return "自定义", "muted", "自定义条目按命令模板启动；智能检测只补全内置预设。"
        data = self._opener_detections.get(self._detect_instance_name())
        if data is None:
            if self._detecting:
                return "检测中", "warning", "正在检查宿主机与当前实例…"
            return (
                _owner._open.HOST_LABELS[opener.host],
                "accent" if opener.host == _owner._open.HOST_WINDOWS else "warning",
                "点右上「智能检测」查看宿主机 / WSL 内是否已安装。",
            )
        host_map, wsl_map = data
        if opener.host == _owner._open.HOST_WINDOWS:
            here, other, other_label = (
                host_map.get(opener.key),
                wsl_map.get(opener.key),
                "WSL 内",
            )
        else:
            here, other, other_label = (
                wsl_map.get(opener.key),
                host_map.get(opener.key),
                "宿主机",
            )
        if here is not None and here.found:
            return f"{_owner._open.HOST_LABELS[opener.host]}已安装", "success", f"路径：{here.path}"
        if other is not None and other.found:
            return (
                f"仅{other_label}已安装",
                "warning",
                f"{other_label}路径：{other.path}；当前条目的运行位置是 {_owner._open.HOST_LABELS[opener.host]}，"
                "可编辑条目切换运行位置。",
            )
        return "未安装", "muted", "宿主机与 WSL 内均未找到（可能未安装，或安装在非标准位置）。"

    def _opener_label(self, opener: _owner._open.Opener) -> str:
        builtin = any(item.key == opener.key for item in _owner._open.default_openers(opener.kind))
        return f"{opener.name}（内置）" if builtin else opener.name

    def _sync_opener_actions(self) -> None:
        for kind in _owner._open.KINDS:
            has = bool(_owner.w.openers_for(self.settings, kind))
            self.opener_edit_buttons[kind].setEnabled(has)
            self.opener_delete_buttons[kind].setEnabled(has)
            self.opener_combos[kind].setEnabled(has)
            self.opener_combos[kind].setToolTip(
                "还没有条目：点「添加…」创建"
                if not has
                else f"文件页「用 {_owner._open.KIND_LABELS[kind]} 打开」使用的条目"
            )

    def _open_settings_page(self) -> None:
        """设置页入口：按当前实例做一次智能检测（已有缓存直接复用）。"""
        self._settings_seen = True
        self._sync_opener_detection()

    def _detect_instance_name(self) -> str:
        """智能检测使用的实例：优先当前选中行，其次列表第一项。"""
        distro = self._selected()
        if distro is not None:
            return distro.name
        return self._rows[0][0] if self._rows else ""

    def _sync_opener_detection(self) -> None:
        """实例变化后刷新标签；没有该实例的检测缓存就重新检测。"""
        if not self._settings_seen or not hasattr(self, "opener_detect_button"):
            return
        name = self._detect_instance_name()
        if name and name not in self._opener_detections:
            self._detect_openers()
            return
        for kind in _owner._open.KINDS:
            self._reload_opener_combo(kind)

    def _detect_openers(self, *, force: bool = False) -> None:
        """检测宿主机与当前实例内的 IDE / AI Agent；结果按实例缓存。"""
        if self._detecting or not hasattr(self, "opener_detect_button"):
            return
        name = self._detect_instance_name()
        cached = self._opener_detections.get(name)
        if cached is not None and not force:
            self._apply_detections(cached, log=False)
            return
        current = self.view.find(name) if self.view is not None and name else None
        # 未知状态按“未运行”处理：宁可用缓存，也不隐式启动实例。
        stopped = current is None or current.state != "running"
        include_wsl = bool(name) and not stopped
        if stopped and force:
            if not _owner.confirm(
                self,
                "智能检测",
                f"检测 {name} 实例内的 IDE / AI Agent 会执行 wsl -d 命令并启动实例。继续？",
            ):
                return
            include_wsl = True
        self._detecting = True
        self._detect_token += 1
        token = self._detect_token
        self.opener_detect_button.setEnabled(False)
        self.opener_detect_button.set_text("检测中…")
        suffix = "" if include_wsl else "（实例未运行，复用上次结果）"
        self.opener_detect_hint.setText(f"正在检查宿主机与 {name or '当前环境'}{suffix}…")
        for kind in _owner._open.KINDS:
            self._reload_opener_combo(kind)
        _owner.submit(
            lambda: self._detect_worker(name, include_wsl=include_wsl),
            on_done=lambda result: self._detect_done(token, name, result),
            on_error=self._detect_error,
            on_finished=self._detect_finished,
        )

    def _detect_worker(  # noqa: PLR0912 - 宿主 / WSL / 缓存三条路径合并
        self,
        name: str,
        *,
        include_wsl: bool = True,
    ) -> tuple[dict[str, _owner._open.Detection], dict[str, _owner._open.Detection], str, dict[str, str] | None]:
        """后台探测：宿主机 PATH / 常见安装目录 + 当前实例 ``command -v``。

        ``include_wsl=False``（实例未运行）时只读上次持久化的路径，绝不执行
        ``wsl -d``；只有显式检测并确认后才会启动实例。
        """
        host: dict[str, _owner._open.Detection] = {}
        for kind in _owner._open.KINDS:
            for opener in _owner._open.default_openers(kind):
                host.setdefault(opener.key, _owner._open.detect_host(opener.key))
        wsl: dict[str, _owner._open.Detection] = {}
        warning = ""
        fresh: dict[str, str] | None = None
        if name:
            found: dict[str, str] = {}
            if include_wsl:
                try:
                    env = _owner.cli._build_environment()
                    wsl_exe = _owner.cli._require_wsl(env)
                    result = env.runner.run(
                        [wsl_exe, "-d", name, "--exec", "bash", "-lc", _owner._open.detect_script()],
                        timeout=120.0,
                    )
                except _owner.WslmanError as exc:
                    warning = str(exc)
                else:
                    if result.returncode == 0:
                        found = _owner._open.parse_detect(_owner.decode_console(result.stdout))
                        fresh = found
                    else:
                        warning = f"实例 {name} 内检测失败（退出码 {result.returncode}）"
            if not found:
                found = dict(_owner.w.opener_paths_for(self.settings, name))
                if found and not warning:
                    warning = f"实例 {name} 未运行，复用上次检测结果"
                elif not found and not warning:
                    warning = f"实例 {name} 未运行；启动后可重新检测"
            for kind in _owner._open.KINDS:
                for opener in _owner._open.default_openers(kind):
                    wsl.setdefault(opener.key, _owner._open.detect_wsl(opener.key, found))
        return host, wsl, warning, fresh

    def _detect_done(
        self,
        token: int,
        name: str,
        result: tuple[dict[str, _owner._open.Detection], dict[str, _owner._open.Detection], str, dict[str, str] | None],
    ) -> None:
        if self._closing or token != self._detect_token:
            return
        host, wsl, warning, fresh = result
        if fresh is not None and name:
            self._save_opener_paths(name, fresh)
        self._opener_detections[name] = (host, wsl)
        self._apply_detections((host, wsl), log=True)
        installed: list[str] = []
        missing: list[str] = []
        for kind in _owner._open.KINDS:
            for opener in _owner._open.default_openers(kind):
                here = (host if opener.host == _owner._open.HOST_WINDOWS else wsl).get(opener.key)
                (installed if here is not None and here.found else missing).append(opener.name)
        summary = (
            f"检测完成（宿主机 + {name or '未选择实例'}）："
            f"{('、'.join(installed)) or '无'} 可用；{('、'.join(missing)) or '无'} 未找到。"
        )
        if warning:
            summary += f" WSL 内检测未完成：{warning}（标签仅反映宿主机结果）"
        self.opener_detect_hint.setText(summary)
        self._log(f"[打开方式] {summary}")

    def _detect_error(self, message: str) -> None:
        if self._closing:
            return
        self.opener_detect_hint.setText(f"智能检测失败：{message}")
        self._log(f"[打开方式] 智能检测失败：{message}")

    def _detect_finished(self) -> None:
        self._detecting = False
        if not hasattr(self, "opener_detect_button"):
            return
        self.opener_detect_button.set_text("智能检测")
        self.opener_detect_button.setEnabled(True)
        for kind in _owner._open.KINDS:
            self._reload_opener_combo(kind)

    def _apply_detections(
        self,
        data: tuple[dict[str, _owner._open.Detection], dict[str, _owner._open.Detection]],
        *,
        log: bool,
    ) -> None:
        """把检测到的绝对路径补进「没被用户手改过」的内置条目。"""
        host, wsl = data
        for kind in _owner._open.KINDS:
            openers = _owner.w.openers_for(self.settings, kind)
            current = _owner.w.selected_opener(self.settings, kind)
            changed = False
            updated: list[_owner._open.Opener] = []
            for opener in openers:
                if not opener.builtin or opener.user_edited:
                    updated.append(opener)
                    continue
                found = (host if opener.host == _owner._open.HOST_WINDOWS else wsl).get(opener.key)
                if found is None or not found.found or not found.command or found.command == opener.command:
                    updated.append(opener)
                    continue
                updated.append(
                    _owner._open.Opener(
                        opener.key,
                        opener.name,
                        opener.kind,
                        opener.host,
                        found.command,
                        True,
                        opener.note,
                        _owner._open.ORIGIN_DETECTED,
                    )
                )
                changed = True
                if log:
                    self._log(f"[打开方式] {opener.name}：补全为 {found.host_label} 检测到的路径")
            if changed:
                self._store_openers(kind, tuple(updated), current.key if current is not None else "")
        for kind in _owner._open.KINDS:
            self._reload_opener_combo(kind)

    def _store_openers(
        self,
        kind: str,
        openers: Sequence[_open.Opener],
        selected: str,
        *,
        hidden: Sequence[str] | None = None,
    ) -> None:
        """写回内存设置并立即落盘，避免关窗前异常导致选择丢失。"""
        self.settings = _owner.w.with_openers(self.settings, kind, openers, selected, hidden)
        self._reload_opener_combo(kind)
        self._sync_opener_actions()
        self._sync_files_actions()
        try:
            _owner.w.save_settings(self.settings, self._own_settings_path)
        except OSError as exc:
            _owner.QMessageBox.warning(self, "保存失败", f"打开方式已生效，但写入设置文件失败：\n{exc}")

    def _on_opener_selected(self, kind: str) -> None:
        key = self.opener_combos[kind].currentData() or ""
        self._store_openers(kind, _owner.w.openers_for(self.settings, kind), str(key))

    def _opener_add(self, kind: str) -> None:
        dialog = _owner._OpenerDialog(self, kind)
        if dialog.exec() != _owner.QDialog.DialogCode.Accepted:
            return
        name, host, command = dialog.values()
        openers = _owner.w.openers_for(self.settings, kind)
        key = _owner._open.new_key(name, [item.key for item in openers])
        opener = _owner._open.Opener(key, name, kind, host, command, origin=_owner._open.ORIGIN_USER)
        self._store_openers(kind, (*openers, opener), key)

    def _opener_edit(self, kind: str) -> None:
        current = _owner.w.selected_opener(self.settings, kind)
        if current is None:
            return
        dialog = _owner._OpenerDialog(self, kind, current)
        if dialog.exec() != _owner.QDialog.DialogCode.Accepted:
            return
        name, host, command = dialog.values()
        openers = tuple(
            # 用户改过的条目记成 user：之后的智能检测不会再覆盖这条命令。
            _owner._open.Opener(item.key, name, kind, host, command, item.builtin, item.note, _owner._open.ORIGIN_USER)
            if item.key == current.key
            else item
            for item in _owner.w.openers_for(self.settings, kind)
        )
        self._store_openers(kind, openers, current.key)

    def _opener_delete(self, kind: str) -> None:
        current = _owner.w.selected_opener(self.settings, kind)
        if current is None:
            return
        if not _owner.confirm(
            self,
            f"删除{_owner._open.KIND_LABELS[kind]}打开方式",
            f"删除「{current.name}」？\n\n命令模板：{current.command}",
        ):
            return
        openers = tuple(item for item in _owner.w.openers_for(self.settings, kind) if item.key != current.key)
        hidden = (*_owner.w.hidden_openers(self.settings, kind), current.key) if current.builtin else None
        self._store_openers(kind, openers, "", hidden=hidden)

    def _build_work_card(self) -> _owner.Card:
        """「工作目录」卡片：llmw 同款多磁盘卡，可添加 / 移除多个目录并指定默认。

        默认目录排在第一张卡上：新下载、新安装落到它的 ``download`` / ``install``
        子目录；备份仍然按默认目录推演。外层与其他设置项一样套设置卡（标题 + 边框），
        里面的磁盘卡、卡上的「更改 / 打开」与多目录操作保持原样。
        """
        card = _owner.Card("工作目录")
        grid = _owner.DiskGrid(card, add_text="添加目录", max_columns=2, min_count=1)
        grid.addRequested.connect(self._add_work_dir)
        grid.removeRequested.connect(self._remove_work_dir)
        grid.primaryRequested.connect(self._set_default_work_dir)
        grid.openRequested.connect(self._open_work_root)
        self.work_grid = grid
        card.body.addWidget(grid)
        self._refresh_work_card()
        return card

    def _refresh_work_card(self) -> None:
        """按当前设置重画磁盘卡：默认目录在第一张，其余按设置顺序排。"""
        roots = list(_owner.w.work_dir_paths(self.settings))
        self.work_grid.set_locations(_owner.default_disk_locations(roots, primary_label="默认工作目录"))

    def _store_work_dirs(self, dirs: Sequence[str | Path], default: str | Path, message: str) -> None:
        """写回多目录与默认项，立即落盘；实例行上的归属目录同步刷新。

        重操作（刷新 / 迁移 / 下载等）进行中不重建列表：``self.view`` 可能正被
        工作线程改写，此时只记「列表待刷新」，任务收尾后自动重算；「刷新」也可
        随时手动重读注册信息。
        """
        self.settings = _owner.w.with_work_dirs(self.settings, dirs, default)
        self._refresh_work_card()
        if hasattr(self, "distro_list"):
            if self._busy:
                self._list_pending = True
                self.set_page_status("distros", "工作目录已更新；任务进行中，列表待刷新")
            else:
                self._list_pending = False
                self._refresh_rows()
        try:
            _owner.w.save_settings(self.settings, self._own_settings_path)
        except OSError as exc:
            _owner.QMessageBox.warning(self, "保存失败", f"工作目录已生效，但写入设置文件失败：\n{exc}")
            return
        self.set_page_status("settings", message)
        self._log(f"[设置] {message}")

    def _add_work_dir(self) -> None:
        """添加一个工作目录（重复目录不重复添加）。"""
        roots = list(_owner.w.work_dir_paths(self.settings))
        chosen = _owner.QFileDialog.getExistingDirectory(
            self, "添加工作目录", str(roots[0] if roots else _owner.Path.home())
        )
        if not chosen:
            return
        if any(_owner.w.same_dir(chosen, item) for item in roots):
            self.set_page_status("settings", f"目录已在列表中：{chosen}")
            return
        self._store_work_dirs([*roots, chosen], roots[0], f"已添加工作目录：{chosen}")

    def _remove_work_dir(self, path: str) -> None:
        """从列表移除工作目录（至少保留一个，不删磁盘文件）。"""
        roots = list(_owner.w.work_dir_paths(self.settings))
        if len(roots) <= 1:
            self.set_page_status("settings", "至少要保留一个工作目录")
            return
        target = _owner.Path(path)
        if not _owner.confirm(
            self,
            "移除工作目录",
            f"从列表移除以下目录？\n{target}\n\n只从设置里移除，磁盘文件不会被删除。",
        ):
            return
        remaining = [item for item in roots if not _owner.w.same_dir(item, target)]
        if len(remaining) == len(roots):
            self.set_page_status("settings", f"目录不在列表中：{target}")
            return
        self._store_work_dirs(remaining, remaining[0], f"已移除工作目录：{target}")

    def _set_default_work_dir(self, path: str) -> None:
        """把目录设为默认：新下载 / 新安装落到它的子目录。"""
        roots = list(_owner.w.work_dir_paths(self.settings))
        target = _owner.Path(path)
        chosen = next((item for item in roots if _owner.w.same_dir(item, target)), None)
        if chosen is None:
            return
        rest = [item for item in roots if not _owner.w.same_dir(item, chosen)]
        self._store_work_dirs(
            [chosen, *rest],
            chosen,
            f"默认工作目录已切换为：{chosen}；新下载 / 新安装落到这里",
        )

    def _open_work_root(self, root: str | None = None) -> None:
        """打开工作目录（不存在时先创建）。"""
        target = _owner.Path(root) if root else _owner.w.work_dir_path(self.settings)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _owner.QMessageBox.critical(self, "打开失败", f"无法创建目录：{target}\n{exc}")
            return
        _owner.QDesktopServices.openUrl(_owner.QUrl.fromLocalFile(str(target)))
