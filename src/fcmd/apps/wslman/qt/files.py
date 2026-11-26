"""wslman 窗口方法（ADR 0046 P2/P3 机械迁移）。"""

from __future__ import annotations

import fcmd.apps.wslman._wslmanw_qt as _owner
from fcmd.gui.widgets import messages, pick_files, save_file

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


class FilesMixin:
    """ADR 0046 P2/P3 迁移的窗口方法。"""

    def _build_files_page(self) -> QWidget:
        """文件页：实例选择 + 可编辑路径栏 + 只读表格 + 上传下载 / 增删改。

        浏览走 UNC 根，不逐条执行命令；默认进入所选实例的家目录（``~``）。
        表格整页铺满（与实例 / 镜像页同构），所有 UNC / 文件操作都放到后台线程，
        避免网络路径卡住界面。
        """
        page = _owner.PageBody(self, spacing=8)
        body = page.body

        path_row = _owner.QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(6)
        path_row.addWidget(_owner.QLabel("实例"))
        self.files_distro = _owner.QComboBox(page)
        self.files_distro.setMinimumWidth(160)
        self.files_distro.setToolTip("选择要浏览的 WSL 实例")
        self.files_distro.currentIndexChanged.connect(self._on_files_distro_changed)
        path_row.addWidget(self.files_distro)
        self.files_up = _owner.button("", kind="ghost", icon="chevron-up", tooltip="回到上一级目录")
        self.files_up.clicked.connect(self._files_go_up)
        path_row.addWidget(self.files_up)
        self.files_home = _owner.button("", kind="ghost", icon="folder", tooltip="回到当前实例的家目录（~）")
        self.files_home.clicked.connect(self._files_go_home)
        path_row.addWidget(self.files_home)
        self.files_path = _owner.QLineEdit(page)
        self.files_path.setPlaceholderText("~ 或 /home/me/project；按 Enter 跳转")
        self.files_path.setToolTip("实例内的 POSIX 路径；~ 表示家目录")
        self.files_path.returnPressed.connect(self._files_commit_path)
        path_row.addWidget(self.files_path, 1)
        self.files_refresh = _owner.button("", kind="ghost", icon="refresh", tooltip="重新读取当前目录")
        self.files_refresh.clicked.connect(self._files_refresh_dir)
        path_row.addWidget(self.files_refresh)
        body.addLayout(path_row)

        self.files_status = _owner.ElidedLabel("选择实例后显示其家目录", parent=page)
        self.files_status.setObjectName("Hint")
        self.files_status.setSizePolicy(_owner.QSizePolicy.Policy.Ignored, _owner.QSizePolicy.Policy.Fixed)
        body.addWidget(self.files_status)

        self.files_table = _owner.make_table(
            ("名称", "类型", "大小", "修改时间"),
            widths=(320, 80, 110, 150),
            stretch_columns=(0,),
            parent=page,
        )
        self.files_table.setObjectName("FileTable")
        self.files_table.itemSelectionChanged.connect(self._sync_files_actions)
        self.files_table.itemDoubleClicked.connect(self._files_open_row)
        body.addWidget(self.files_table, 1)

        actions = _owner.QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.files_open_ide = _owner.button("", kind="ghost", icon="code", tooltip="用设置里选中的 IDE 打开当前目录")
        self.files_open_ide.clicked.connect(lambda: self._files_open_with(_owner._open.KIND_IDE))
        actions.addWidget(self.files_open_ide)
        self.files_open_agent = _owner.button(
            "", kind="ghost", icon="bot", tooltip="用设置里选中的 AI Agent 打开当前目录"
        )
        self.files_open_agent.clicked.connect(lambda: self._files_open_with(_owner._open.KIND_AGENT))
        actions.addWidget(self.files_open_agent)
        actions.addSpacing(8)
        self.files_upload = _owner.button(
            "", kind="primary", icon="upload", tooltip="把 Windows 上的文件复制到当前目录"
        )
        self.files_upload.clicked.connect(self._files_upload)
        actions.addWidget(self.files_upload)
        self.files_download = _owner.button(
            "", kind="ghost", icon="download", tooltip="把选中的文件复制到 Windows 磁盘"
        )
        self.files_download.clicked.connect(self._files_download)
        actions.addWidget(self.files_download)
        actions.addSpacing(8)
        self.files_mkdir = _owner.button("", kind="ghost", icon="plus", tooltip="在当前目录下新建文件夹")
        self.files_mkdir.clicked.connect(self._files_new_folder)
        actions.addWidget(self.files_mkdir)
        self.files_rename = _owner.button("", kind="ghost", icon="pencil", tooltip="重命名选中的文件或文件夹")
        self.files_rename.clicked.connect(self._files_rename)
        actions.addWidget(self.files_rename)
        actions.addStretch(1)
        self.files_delete = _owner.button(
            "", kind="danger", icon="trash", tooltip="删除选中的文件或文件夹（会二次确认）"
        )
        self.files_delete.clicked.connect(self._files_delete)
        actions.addWidget(self.files_delete)
        body.addLayout(actions)

        self._sync_files_actions()
        return page

    def _open_files_page(self) -> None:
        """文件页入口：同步实例下拉框；首次进入时读取家目录并列出内容。"""
        if self._files_loading or self._files_busy:
            return
        view = self.view
        names = [distro.name for distro in view.distros] if view is not None else []
        if not names:
            self._files_loaded = ""
            self._files_root = ""
            self._files_home = ""
            self._files_dir = ""
            self._files_entries = ()
            if self.files_distro.count():
                self.files_distro.blockSignals(True)
                self.files_distro.clear()
                self.files_distro.blockSignals(False)
            self.files_path.clear()
            self._render_files()
            self._set_files_status("暂无实例：先在「实例」页创建或导入后再浏览文件")
            self._files_loading = False
            self._sync_files_actions()
            return
        existing = [self.files_distro.itemText(index) for index in range(self.files_distro.count())]
        if existing != names:
            previous = self.files_distro.currentText()
            preferred = self._files_loaded if self._files_loaded in names else previous
            if preferred not in names:
                preferred = view.default if view is not None and view.default in names else names[0]
            self.files_distro.blockSignals(True)
            self.files_distro.clear()
            self.files_distro.addItems(names)
            self.files_distro.setCurrentIndex(max(self.files_distro.findText(preferred), 0))
            self.files_distro.blockSignals(False)
        name = self.files_distro.currentText()
        if name and (self._files_loaded != name or not self._files_dir):
            self._files_enter(name)

    def _on_files_distro_changed(self, _index: int) -> None:
        if self._files_busy or self._files_loading:
            return
        name = self.files_distro.currentText()
        if name:
            self._files_enter(name)

    def _files_enter(self, name: str) -> None:
        """切到某实例：清空旧目录状态并重新探测家目录。"""
        self._files_loaded = name
        self._files_root = ""
        self._files_home = ""
        self._files_user = ""
        self._files_dir = ""
        self._files_entries = ()
        self._render_files()
        self._files_load(name, None)

    def _files_load(self, name: str, path: str | None) -> None:
        """后台读取目录；``path=None`` 表示先探测家目录再列它。

        探测与列目录合并在同一个任务里，避免「先 wsl 取 HOME，再等 UNC 列目录」
        的两次跳转；用户快速切目录时用递增序号丢弃迟到的旧结果。
        """
        if not name or self._closing:
            return
        self._files_seq += 1
        seq = self._files_seq
        self._files_loading = True
        self._sync_files_actions()
        label = "家目录" if path is None else path
        self._set_files_status(f"正在读取 {name} 的{label}…")
        cached_root = self._files_roots.get(name, "")
        cached_home = self._files_home
        cached_user = self._files_user

        def work() -> tuple[str, str, str, str, tuple[_owner._files.FileEntry, ...]]:
            root = cached_root or _owner._files.pick_unc_root(name)
            user, home = cached_user, cached_home
            if not home:
                env = _owner.cli._build_environment()
                wsl_path = _owner.cli._require_wsl(env)
                result = env.runner.run(
                    [wsl_path, "-d", name, "--exec", "bash", "-lc", _owner._files.home_probe_script()],
                    timeout=60.0,
                )
                text = _owner.decode_console(result.stdout + result.stderr)
                user, detected = _owner._files.parse_home(text)
                if not detected:
                    tail = _owner._initenv.tail_lines(text, 3)
                    raise _owner.runtime_error(
                        f"无法读取 {name} 的家目录（退出码 {result.returncode}）",
                        hint=tail[-1] if tail else f"确认实例可以启动：wsl -d {name} --exec true",
                    )
                home = detected
            target = _owner._files.normalize_path(path if path is not None else home, home)
            entries = _owner._files.list_entries(_owner._files.to_local(root, target))
            return root, user, home, target, entries

        _owner.submit_daemon(
            work,
            on_done=lambda payload: self._files_loaded_ok(seq, name, payload),
            on_error=lambda message: self._files_loaded_error(seq, name, message),
            on_finished=lambda: self._files_load_finished(seq),
        )

    def _files_loaded_ok(
        self,
        seq: int,
        name: str,
        payload: tuple[str, str, str, str, tuple[_owner._files.FileEntry, ...]],
    ) -> None:
        if self._closing or seq != self._files_seq:
            return
        root, user, home, target, entries = payload
        self._files_roots[name] = root
        self._files_root = root
        self._files_home = home
        self._files_user = user
        self._files_dir = target
        self._files_entries = entries
        self.files_path.setText(_owner._files.display_path(target, home))
        self._render_files()
        who = f" · {user}" if user else ""
        self._set_files_status(f"{name}{who} · {_owner._files.display_path(target, home)} · {len(entries)} 项")

    def _files_loaded_error(self, seq: int, name: str, message: str) -> None:
        if self._closing or seq != self._files_seq:
            return
        # 导航失败时保留上一级已列出的内容与路径栏，只把错误写进状态行；
        # 首次进入失败时本来就没有内容，表格保持为空。
        self._set_files_status(f"{name}：{message}")
        self._log(f"[文件] {message}")

    def _files_load_finished(self, seq: int) -> None:
        if self._closing or seq != self._files_seq:
            return
        self._files_loading = False
        self._sync_files_actions()

    def _render_files(self) -> None:
        """按 ``_files_entries`` 重绘表格；选中清空，避免操作落到旧行。"""
        self.files_table.setRowCount(0)
        self.files_table.setRowCount(len(self._files_entries))
        for row, entry in enumerate(self._files_entries):
            name_item = _owner.make_table_item(entry.name)
            name_item.setToolTip(entry.name)
            self.files_table.setItem(row, 0, name_item)
            self.files_table.setItem(row, 1, _owner.make_table_item(_owner._files.type_text(entry)))
            size_item = _owner.make_table_item(_owner._files.size_text(entry))
            size_item.setTextAlignment(_owner.Qt.AlignmentFlag.AlignRight | _owner.Qt.AlignmentFlag.AlignVCenter)
            self.files_table.setItem(row, 2, size_item)
            self.files_table.setItem(row, 3, _owner.make_table_item(_owner._files.format_mtime(entry)))
        self.files_table.clearSelection()
        self._sync_files_actions()

    def _files_selected(self) -> _files.FileEntry | None:
        row = self.files_table.currentRow()
        if 0 <= row < len(self._files_entries):
            return self._files_entries[row]
        return None

    def _files_selected_local(self) -> tuple[Path, _owner._files.FileEntry] | None:
        entry = self._files_selected()
        if entry is None or not self._files_root or not self._files_dir:
            return None
        target = _owner._files.to_local(self._files_root, _owner._files.join_path(self._files_dir, entry.name))
        return target, entry

    def _sync_files_actions(self) -> None:
        """按「是否加载完成 / 是否选中 / 是否任务中」同步按钮可用性。"""
        busy = self._files_busy or self._files_loading
        loaded = bool(self._files_dir)
        entry = self._files_selected()
        self.files_distro.setEnabled(not busy)
        self.files_path.setEnabled(not busy)
        self.files_upload.setEnabled(loaded and not busy)
        self.files_mkdir.setEnabled(loaded and not busy)
        self.files_refresh.setEnabled(loaded and not busy)
        self.files_up.setEnabled(loaded and self._files_dir != "/" and not busy)
        self.files_home.setEnabled(loaded and not busy)
        self.files_download.setEnabled(entry is not None and not busy)
        self.files_rename.setEnabled(entry is not None and not busy)
        self.files_delete.setEnabled(entry is not None and not busy)
        for kind, widget in (
            (_owner._open.KIND_IDE, self.files_open_ide),
            (_owner._open.KIND_AGENT, self.files_open_agent),
        ):
            opener = _owner.w.selected_opener(self.settings, kind)
            widget.setEnabled(opener is not None and loaded and not busy)
            widget.setToolTip(self._open_tooltip(kind, opener))

    def _open_tooltip(self, kind: str, opener: _owner._open.Opener | None) -> str:
        label = _owner._open.KIND_LABELS[kind]
        if opener is None:
            return f"还没有{label}打开方式：到「设置 → 打开方式」添加"
        where = _owner._open.HOST_LABELS[opener.host]
        return f"用 {opener.name} 打开当前目录（{where}）\n命令模板：{opener.command}"

    def _files_open_with(self, kind: str) -> None:
        """按当前选中条目在正在浏览的目录打开 IDE / AI Agent。

        Windows 宿主机的 GUI 程序直接按 argv 启动；WSL 内的终端程序先确认命令
        存在，再通过终端 + ``--cd`` 进入当前目录。两条路径失败都弹窗说明原因，
        不静默失败。
        """
        if self._closing or self._files_busy or self._files_loading:
            return
        distro = self._files_loaded or self.files_distro.currentText()
        directory = self._files_dir
        opener = _owner.w.selected_opener(self.settings, kind)
        if opener is None:
            messages.info(
                self,
                f"尚未配置{_owner._open.KIND_LABELS[kind]}打开方式",
                f"到「设置 → 打开方式」添加或选中一个{_owner._open.KIND_LABELS[kind]}条目后再试。",
            )
            return
        if not distro or not directory:
            return
        root = self._files_root
        if not root:
            try:
                root = _owner._files.pick_unc_root(distro)
            except _owner.WslmanError as exc:
                messages.error(self, "打开失败", _owner.w._error_text(exc))
                return
            self._files_roots[distro] = root
        unc = str(_owner._files.to_local(root, directory))
        home = self._files_home or "/"
        label = f"用 {_owner._open.KIND_LABELS[kind]} 打开"
        if opener.host == _owner._open.HOST_WINDOWS:
            self._files_open_windows(opener, label, directory=directory, unc=unc, distro=distro, home=home)
            return
        self._set_files_status(f"{label}：正在 {distro} 里启动 {opener.name}…")
        self._files_busy = True
        self._sync_files_actions()

        def work() -> str:
            env = _owner.cli._build_environment()
            wsl_path = _owner.cli._require_wsl(env)
            probe = env.runner.run(
                [
                    wsl_path,
                    "-d",
                    distro,
                    "--exec",
                    "bash",
                    "-lc",
                    _owner._open.probe_script(opener, dir=directory, unc=unc, distro=distro, home=home),
                ],
                timeout=60.0,
            )
            found = _owner.decode_console(probe.stdout + probe.stderr).strip()
            exe = _owner._open.command_name(opener, dir=directory, unc=unc, distro=distro, home=home)
            if probe.returncode != 0 or not found:
                raise _owner.runtime_error(
                    f"{distro} 里找不到 {exe}",
                    hint=f"先在实例里安装 {opener.name}，或在「设置 → 打开方式」把命令模板改成实际可用的命令",
                )
            command = _owner.build_command(
                wsl_path=wsl_path,
                distro=distro,
                cwd=directory,
                program=env.config.terminal_program,
                extra_args=env.config.terminal_extra_args,
                run=(
                    "--exec",
                    "bash",
                    "-lc",
                    _owner._open.wsl_command(opener, dir=directory, unc=unc, distro=distro, home=home),
                ),
            )
            self.launcher.launch(command)
            return f"已在 {distro}:{directory} 启动 {opener.name}"

        def runner() -> tuple[bool, str]:
            try:
                return True, work()
            except Exception as exc:  # 后台任务统一转成界面文案
                return False, _owner.w._error_text(exc)

        _owner.submit_daemon(
            runner,
            on_done=lambda payload: self._files_open_done(label, payload),
            on_finished=self._files_task_finished,
        )

    def _files_open_windows(  # noqa: PLR0913 - 目标信息按目录 / UNC / 实例 / 家目录逐项展开
        self,
        opener: _owner._open.Opener,
        label: str,
        *,
        directory: str,
        unc: str,
        distro: str,
        home: str,
    ) -> None:
        """Windows 宿主机程序：先确认可执行文件在 PATH，再按 argv 无窗口启动。"""
        try:
            argv = _owner._open.windows_argv(opener, dir=directory, unc=unc, distro=distro, home=home)
            exe = argv[0]
            if _owner.shutil.which(exe) is None and not _owner.Path(exe).is_file():
                raise _owner.runtime_error(
                    f"没有找到 {exe}",
                    hint=f"确认 {opener.name} 已安装并加入 PATH；或在「设置 → 打开方式」把命令模板改成完整路径",
                )
            _owner.spawn_gui(_owner._open.wrap_windows_argv(argv))
        except (_owner.WslmanError, _owner.LaunchError, OSError) as exc:
            message = _owner.w._error_text(exc)
            self._set_files_status(f"{label}失败：{message}")
            messages.error(self, label, message)
            return
        self._set_files_status(f"{label}：已启动 {opener.name}（{distro}:{directory}）")
        self._log(f"[文件] 用 {opener.name} 打开 {distro}:{directory}")

    def _files_open_done(self, label: str, payload: tuple[bool, str]) -> None:
        if self._closing:
            return
        ok, message = payload
        if not ok:
            self._set_files_status(f"{label}失败：{message}")
            messages.error(self, label, message)
            return
        self._set_files_status(f"{label}完成：{message}")
        self._log(f"[文件] {message}")

    def _set_files_status(self, text: str) -> None:
        self.files_status.setText(text)
        self.files_status.setToolTip(text)

    def _files_commit_path(self) -> None:
        """路径栏回车：按输入跳转（支持 ``~`` 与相对家目录的路径）。"""
        if self._files_busy or self._files_loading or not self._files_loaded:
            return
        text = self.files_path.text().strip()
        if not text:
            return
        try:
            target = _owner._files.normalize_path(text, self._files_home or "/")
        except _owner.WslmanError as exc:
            messages.warn(self, "路径不可用", _owner.w._error_text(exc))
            return
        self._files_load(self._files_loaded, target)

    def _files_go_up(self) -> None:
        if self._files_busy or self._files_loading or self._files_dir in {"", "/"}:
            return
        self._files_load(self._files_loaded, _owner._files.parent_path(self._files_dir))

    def _files_go_home(self) -> None:
        if self._files_busy or self._files_loading or not self._files_home:
            return
        self._files_load(self._files_loaded, self._files_home)

    def _files_refresh_dir(self) -> None:
        if self._files_busy or self._files_loading or not self._files_loaded:
            return
        self._files_load(self._files_loaded, self._files_dir or None)

    def _files_open_row(self, item: Any) -> None:
        """双击文件夹进入下一级；双击文件不做动作（下载走底部按钮）。"""
        row = item.row()
        if not 0 <= row < len(self._files_entries):
            return
        entry = self._files_entries[row]
        if not entry.is_dir:
            return
        self._files_load(self._files_loaded, _owner._files.join_path(self._files_dir, entry.name))

    def _files_run(self, label: str, work: Any) -> None:
        """在守护线程执行文件操作；失败统一转成可读文案并弹窗。"""
        if self._files_busy or self._files_loading or self._closing:
            return
        self._files_busy = True
        self._sync_files_actions()
        self._set_files_status(f"{label}…")

        def runner() -> tuple[bool, str]:
            try:
                return True, work()
            except Exception as exc:  # 后台任务统一转成界面文案
                return False, _owner.w._error_text(exc)

        _owner.submit_daemon(
            runner,
            on_done=lambda payload: self._files_task_done(label, payload),
            on_finished=self._files_task_finished,
        )

    def _files_task_done(self, label: str, payload: tuple[bool, str]) -> None:
        if self._closing:
            return
        ok, message = payload
        if not ok:
            self._set_files_status(f"{label}失败：{message}")
            messages.error(self, label, message)
            return
        self._set_files_status(f"{label}完成：{message}")
        self._log(f"[文件] {message}")
        if self._files_loaded and self._files_dir:
            self._files_load(self._files_loaded, self._files_dir)

    def _files_task_finished(self) -> None:
        if self._closing:
            return
        self._files_busy = False
        self._sync_files_actions()

    def _files_new_folder(self) -> None:
        if self._files_busy or self._files_loading or not self._files_dir:
            return
        name, accepted = _owner.QInputDialog.getText(self, "新建文件夹", "文件夹名称：")
        if not accepted:
            return
        try:
            clean = _owner._files.check_name(name)
        except _owner.WslmanError as exc:
            messages.warn(self, "名称不可用", _owner.w._error_text(exc))
            return
        root, directory = self._files_root, self._files_dir
        home = self._files_home

        def work() -> str:
            target = _owner._files.create_dir(_owner._files.to_local(root, directory), clean)
            return f"已在 {_owner._files.display_path(directory, home)} 新建 {target.name}"

        self._files_run("新建文件夹", work)

    def _files_rename(self) -> None:
        selected = self._files_selected_local()
        if selected is None:
            return
        path, entry = selected
        new_name, accepted = _owner.QInputDialog.getText(self, "重命名", f"{entry.name} 的新名称：", text=entry.name)
        if not accepted:
            return
        try:
            clean = _owner._files.check_name(new_name)
        except _owner.WslmanError as exc:
            messages.warn(self, "名称不可用", _owner.w._error_text(exc))
            return
        if clean == entry.name:
            return

        def work() -> str:
            target = _owner._files.rename_entry(path, clean)
            return f"{entry.name} → {target.name}"

        self._files_run("重命名", work)

    def _files_delete(self) -> None:
        selected = self._files_selected_local()
        if selected is None:
            return
        path, entry = selected
        kind = "文件夹" if entry.is_dir else "文件"
        suffix = "（含其中全部内容）" if entry.is_dir else ""
        if not _owner.confirm(self, "删除确认", f"删除{kind} {entry.name}{suffix}？此操作不可恢复。"):
            return
        root = self._files_root

        def work() -> str:
            _owner._files.delete_entry(path, _owner._files.to_local(root, "/"))
            return f"已删除{kind} {entry.name}"

        self._files_run("删除", work)

    def _files_upload(self) -> None:
        if self._files_busy or self._files_loading or not self._files_dir:
            return
        paths = pick_files(
            self,
            f"上传到 {self.files_path.text() or self._files_dir}",
            "",
            "所有文件 (*.*)",
        )
        if not paths:
            return
        root, directory = self._files_root, self._files_dir
        home = self._files_home

        def work() -> str:
            copied = _owner._files.upload_files(
                [_owner.Path(path) for path in paths], _owner._files.to_local(root, directory)
            )
            return f"已上传 {len(copied)} 个文件到 {_owner._files.display_path(directory, home)}"

        self._files_run("上传", work)

    def _files_download(self) -> None:
        selected = self._files_selected_local()
        if selected is None:
            return
        path, entry = selected
        if entry.is_dir:
            messages.info(
                self,
                "暂不支持下载文件夹",
                "文件夹下载暂不支持；请进入文件夹后选择具体文件。",
            )
            return
        target = save_file(self, f"下载 {entry.name}", str(_owner.Path.home() / entry.name))

        if not target:
            return

        def work() -> str:
            saved = _owner._files.download_file(path, _owner.Path(target), overwrite=True)
            return f"已下载到 {saved}"

        self._files_run("下载", work)
