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


class DistrosMixin:
    """ADR 0046 P2/P3 迁移的窗口方法。"""

    def _build_distros_page(self) -> QWidget:
        """实例页：动作行 + 行卡列表（详情在行内展开）。

        与 llmw 模型页同构：固定行只有动作条，列表吃满剩余空间并自带滚动；
        BasePath / 虚拟磁盘 / 占用等详情通过行内「展开」就地显示，不再占用
        下方分栏，实例多时也不会被详情面板压掉一半高度。
        """
        page = _owner.PageBody(self, spacing=8)
        body = page.body
        body.addLayout(self._build_distro_actions(page))

        self.distro_list = _owner.RowListWidget(page)
        self.distro_list.setObjectName("DistroResultList")
        self.distro_list.setSpacing(4)
        self.distro_list.setMinimumHeight(200)
        self.distro_list.setSelectionMode(_owner.RowListWidget.SelectionMode.SingleSelection)
        self.distro_list.setHorizontalScrollBarPolicy(_owner.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.distro_list.currentRowChanged.connect(self._on_distro_selected)
        self.distro_list.itemDoubleClicked.connect(self._on_distro_double_clicked)
        self.distro_detail_view = _owner._DistroDetailView(page)
        self.distro_detail_view.setVisible(False)
        self.bind_row_detail("distros", self.distro_list, self.distro_detail_view)
        body.addWidget(self.distro_list, 1)
        return page

    def _build_distro_actions(self, page: QWidget) -> _owner.FlowLayout:
        """实例动作行：按「终端 / 启停 → 导出 → 危险操作」分组。

        用流式布局而不是 ``QHBoxLayout``：窗口收到最小宽度时按钮整组换行，
        不会被压窄到文字被截断（``打开终端`` / ``修复注册`` 这类四字按钮尤其明显）。
        动作按钮只显示图标，中文名统一放 tooltip；「打开终端」右侧固定跟
        「用 IDE 打开」「用 PI 打开」，都使用实例的默认终端目录。
        """
        toolbar = _owner.FlowLayout(h_spacing=6, v_spacing=4)
        groups: tuple[tuple[tuple[str, Any, str, str, str], ...], ...] = (
            (
                ("打开终端", self._open_selected, "primary", "terminal", "打开终端（默认目录）"),
                ("用 IDE 打开", self._open_selected_ide, "", "code", "用 IDE 打开默认目录"),
                ("用 PI 打开", self._open_selected_pi, "", "bot", "用 PI 打开默认目录"),
            ),
            (
                ("重启", self._restart_selected, "", "reset", "重启实例"),
                ("刷新", self._refresh_distros, "ghost", "refresh", "刷新实例列表"),
                (
                    "修复注册",
                    self._repair_registrations,
                    "ghost",
                    "wrench",
                    "修复注册：用扫描到的 ext4.vhdx 重新注册失效实例",
                ),
            ),
            (
                (
                    "备份",
                    self._backup_selected,
                    "",
                    "archive",
                    "备份到备份目录（<实例>.tar，镜像页的「已备份」即指它）",
                ),
                ("导出", self._export_selected, "", "export", "导出实例到指定位置"),
                ("迁移", self._migrate_selected, "", "transfer", "迁移到其他工作目录"),
            ),
            (
                ("删除实例", self._unregister, "danger", "trash", "删除实例"),
                ("关闭全部", self._shutdown, "danger", "power", "关闭全部实例"),
            ),
        )
        for index, group in enumerate(groups):
            row = _owner.QWidget(page)
            row_layout = _owner.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)
            if index:
                separator = _owner.QLabel("│", row)
                separator.setObjectName("Hint")
                row_layout.addWidget(separator)
            for text, handler, kind, icon, tooltip in group:
                widget = _owner.button("", kind=kind, icon=icon, tooltip=tooltip)
                widget.clicked.connect(handler)
                row_layout.addWidget(widget)
                self._buttons.append(widget)
                self._button_by_text[text] = widget
            toolbar.addWidget(row)
        self.restart_button = self._button_by_text["重启"]
        self.repair_button = self._button_by_text["修复注册"]
        return toolbar

    def refresh(self, *, quiet: bool = False) -> None:
        """重新读取实例注册信息，并按当前工作目录配置重算每个实例的归属目录。

        ``quiet`` 用于注册信息变化后的自动重读：不写状态行 / 加载条，避免把用户刚
        看到的「安装完成 / 迁移完成」等任务结果覆盖成一句泛化的「刷新完成」。
        """
        if self._busy:
            self._refresh_pending = True
            return
        self._list_pending = False
        self._reload_pending = False
        self._reload_quiet = False
        self._start("刷新", self._load, quiet=quiet)

    def _on_reload_distros(self) -> None:
        """兼容旧调用名：实例页「刷新」的完整重读入口。"""
        self._refresh_distros()

    def _refresh_distros(self) -> None:
        """「刷新」：重新读取注册信息并扫描工作目录（BasePath → 归属目录与占用）。

        忙时不丢弃这次点击：标记完整刷新，任务收尾后自动执行。轻量状态轮询只重读
        ``wsl -l -v``，不会重算归属目录与 vhdx 占用，不能代替本动作。
        """
        if self._busy:
            self._reload_pending = True
            self._reload_quiet = False
            self.set_page_status("distros", "已有任务在执行；结束后自动刷新列表，也可稍后点「刷新」")
            return
        self.refresh()

    def _repair_registrations(self) -> None:
        """「修复注册」：用扫描到的 ext4.vhdx 重新注册失效实例。

        只处理 ``needs_repair`` 且找到同名 ``ext4.vhdx`` 的条目：先 ``wsl --unregister``
        清掉旧登记，再 ``wsl --import-in-place`` 现有磁盘。旧 BasePath 仍存在（可能还有
        其他文件）时拒绝自动删除，避免误删用户数据。
        """
        if self._busy or self.view is None:
            return
        targets = [distro for distro in self.view.distros if distro.needs_repair and distro.repair_vhd]
        if not targets:
            self.set_page_status("distros", "没有可自动修复的实例：未找到同名 ext4.vhdx")
            return
        lines = "\n".join(f"  {distro.name}  ←  {distro.repair_vhd}" for distro in targets)
        text = (
            f"将重新注册 {len(targets)} 个实例：\n\n{lines}\n\n"
            "流程：wsl --unregister（清掉失效登记）→ wsl --import-in-place（挂回现有磁盘）。\n"
            "旧 BasePath 目录若仍存在且非空，会跳过该实例，避免误删文件。继续？"
        )
        if not _owner.confirm(self, "修复注册", text):
            return
        snapshot = tuple(targets)
        default = self.view.default

        def work(cancel: threading.Event) -> str:  # noqa: PLR0912 - 逐项校验并汇总失败原因
            env = _owner.cli._build_environment()
            repaired: list[str] = []
            failed: list[str] = []
            for distro in snapshot:
                if cancel.is_set():
                    raise KeyboardInterrupt
                vhd = _owner.Path(distro.repair_vhd or "")
                if not vhd.is_file():
                    failed.append(f"{distro.name}: 磁盘不存在 {vhd}")
                    continue
                fresh = _owner.cli._view(env).find(distro.name)
                if fresh is not None and _owner.w.has_distro_files(fresh.base_path):
                    repaired.append(distro.name)
                    continue
                if fresh is not None and fresh.base_path and _owner.Path(fresh.base_path).exists():
                    base = _owner.Path(fresh.base_path)
                    try:
                        leftovers = any(base.iterdir())
                    except OSError:
                        leftovers = True
                    if leftovers:
                        failed.append(f"{distro.name}: 旧 BasePath 仍有文件，拒绝自动删除：{base}")
                        continue
                try:
                    if fresh is not None:
                        _owner.cli._run_step(env, ["--unregister", fresh.name], action=f"移除失效登记 {fresh.name}")
                    _owner.cli._run_step(
                        env,
                        ["--import-in-place", distro.name, str(vhd)],
                        action=f"重新注册 {distro.name}",
                        hint="确认 WSL 版本支持 --import-in-place，且磁盘是 ext4.vhdx",
                    )
                except _owner.WslmanError as exc:
                    failed.append(f"{distro.name}: {exc}")
                    continue
                repaired.append(distro.name)
            if default and default in repaired:
                try:
                    _owner.cli._run_step(env, ["--set-default", default], action=f"恢复默认实例 {default}")
                except _owner.WslmanError as exc:
                    failed.append(f"恢复默认实例 {default}: {exc}")
            message = f"修复完成：成功 {len(repaired)} 个"
            if repaired:
                message += f"（{'、'.join(repaired)}）"
            if failed:
                message += f"；失败 {len(failed)} 个：{'；'.join(failed)}"
            return message

        self._start("修复注册", work)

    def _open_selected_ide(self) -> None:
        """「用 IDE 打开」：在实例默认目录启动设置里选中的 IDE。"""
        self._open_selected_with(_owner._open.KIND_IDE)

    def _open_selected_pi(self) -> None:
        """「用 PI 打开」：固定用内置 PI，在实例默认目录启动。"""
        self._open_selected_with(_owner._open.KIND_AGENT, key="pi")

    def _open_selected_with(self, kind: str, *, key: str = "") -> None:
        """在选中实例的默认终端目录启动 IDE / AI Agent。

        ``key`` 为空时用设置页选中的条目；给了 ``key``（如 ``pi``）时只找该内置项。
        默认目录取实例配置的终端目录（``~`` 时先探测家目录），IDE 的 Windows
        程序按 UNC 路径启动，实例内程序经终端以 ``--cd`` 进入该目录。
        """
        distro = self._selected()
        if distro is None:
            self.set_page_status("distros", "未选中实例；先选中一行再打开")
            return
        openers = _owner.w.openers_for(self.settings, kind)
        if key:
            opener = next((item for item in openers if item.key == key), None)
        else:
            opener = _owner.w.selected_opener(self.settings, kind)
        if opener is None:
            name = "PI" if key == "pi" else _owner._open.KIND_LABELS[kind]
            _owner.QMessageBox.information(
                self,
                f"尚未配置{name}打开方式",
                f"到「设置 → 打开方式」添加或选中一个{name}条目后再试。",
            )
            return
        directory = _owner.w.terminal_cwd(self.settings, distro.name)
        label = f"用 {opener.name} 打开"

        def work(_cancel: threading.Event) -> str:
            env = _owner.cli._build_environment()
            wsl_path = _owner.cli._require_wsl(env)
            home = "/"
            if not directory.startswith("/"):
                probe = env.runner.run(
                    [wsl_path, "-d", distro.name, "--exec", "bash", "-lc", _owner._files.home_probe_script()],
                    timeout=60.0,
                )
                text = _owner.decode_console(probe.stdout + probe.stderr)
                _user, detected = _owner._files.parse_home(text)
                if not detected:
                    tail = _owner._initenv.tail_lines(text, 3)
                    raise _owner.runtime_error(
                        f"无法读取 {distro.name} 的家目录（退出码 {probe.returncode}）",
                        hint=tail[-1] if tail else f"确认实例可以启动：wsl -d {distro.name} --exec true",
                    )
                home = detected
            target = _owner._files.normalize_path(directory, home)
            root = _owner._files.pick_unc_root(distro.name)
            unc = str(_owner._files.to_local(root, target))
            if opener.host == _owner._open.HOST_WINDOWS:
                argv = _owner._open.windows_argv(opener, dir=target, unc=unc, distro=distro.name, home=home)
                exe = argv[0]
                if _owner.shutil.which(exe) is None and not _owner.Path(exe).is_file():
                    raise _owner.runtime_error(
                        f"没有找到 {exe}",
                        hint=f"确认 {opener.name} 已安装并加入 PATH；或在「设置 → 打开方式」把命令模板改成完整路径",
                    )
                _owner.spawn_gui(_owner._open.wrap_windows_argv(argv))
                return f"已在 {distro.name}:{target} 启动 {opener.name}"
            probe = env.runner.run(
                [
                    wsl_path,
                    "-d",
                    distro.name,
                    "--exec",
                    "bash",
                    "-lc",
                    _owner._open.probe_script(opener, dir=target, unc=unc, distro=distro.name, home=home),
                ],
                timeout=60.0,
            )
            found = _owner.decode_console(probe.stdout + probe.stderr).strip()
            exe = _owner._open.command_name(opener, dir=target, unc=unc, distro=distro.name, home=home)
            if probe.returncode != 0 or not found:
                raise _owner.runtime_error(
                    f"{distro.name} 里找不到 {exe}",
                    hint=f"先在实例里安装 {opener.name}，或在「设置 → 打开方式」把命令模板改成实际可用的命令",
                )
            command = _owner.build_command(
                wsl_path=wsl_path,
                distro=distro.name,
                cwd=target,
                program=env.config.terminal_program,
                extra_args=env.config.terminal_extra_args,
                run=(
                    "--exec",
                    "bash",
                    "-lc",
                    _owner._open.wsl_command(opener, dir=target, unc=unc, distro=distro.name, home=home),
                ),
            )
            self.launcher.launch(command)
            return f"已在 {distro.name}:{target} 启动 {opener.name}"

        self._start(label, work)

    def _load(self, _cancel: threading.Event) -> str:
        env = _owner.cli._build_environment()
        view, needs_repair, orphans = _owner.w.reconcile_distros(_owner.cli._view(env), self.settings)
        sizes: dict[str, tuple[int | None, bool]] = {}
        for distro in view.distros:
            base = str(_owner.Path(distro.repair_vhd).parent) if distro.repair_vhd else distro.base_path
            sizes[distro.name] = _owner.distro_disk_bytes(base, distro.version)
        self.view = view
        self.sizes = sizes
        message = f"已加载 {len(view.distros)} 个实例"
        if needs_repair:
            message += f"；{len(needs_repair)} 个实例需修复注册"
        if orphans:
            message += f"（{len(orphans)} 个已找到残余磁盘，可点「修复注册」）"
        return message

    def _selected(self) -> Distro | None:
        row = self._selected_row()
        return row.distro if row is not None else None

    def _selected_row(self) -> _owner._DistroItemRow | None:
        item = self.distro_list.currentItem()
        row = self.distro_list.itemWidget(item) if item is not None else None
        return row if isinstance(row, _owner._DistroItemRow) else None

    def _refresh_rows(self) -> None:
        """按当前视图重建行卡：保留选中行与展开行（只在名单变化时调用）。"""
        view = self.view
        selected = self._selected()
        expanded = self.expanded_row("distros")
        expanded_name = expanded.distro.name if isinstance(expanded, _owner._DistroItemRow) else ""
        self.detach_row_detail("distros")
        self.distro_list.clear()
        distros = tuple(view.distros) if view is not None else ()
        for distro in distros:
            size, estimated = self.sizes.get(distro.name, (None, False))
            base = str(_owner.Path(distro.repair_vhd).parent) if distro.repair_vhd else distro.base_path
            item = _owner.QListWidgetItem(self.distro_list)
            row = _owner._DistroItemRow(
                distro, size, estimated, _owner.w.work_dir_of(self.settings, base), self.distro_list
            )
            row.toggleRequested.connect(self._on_distro_toggle)
            row.startStopRequested.connect(self._toggle_distro_state)
            row.configRequested.connect(self._configure_distro)
            row.set_busy(self._busy)
            entry, known = self._distro_image(distro)
            row.update_image(entry, known=known)
            item.setSizeHint(_owner.QSize(0, row.sizeHint().height()))
            self.distro_list.setItemWidget(item, row)
        self.distro_list.sync_row_widths()
        target = 0 if distros else -1
        if selected is not None:
            found = next((index for index, distro in enumerate(distros) if distro.name == selected.name), None)
            if found is not None:
                target = found
        if target >= 0:
            self.distro_list.setCurrentRow(target)
        if expanded_name:
            for index in range(self.distro_list.count()):
                row = self.distro_list.itemWidget(self.distro_list.item(index))
                if isinstance(row, _owner._DistroItemRow) and row.distro.name == expanded_name:
                    self.expand_row("distros", row)
                    break
        self._show_details()

    def _distro_image(self, distro: Distro) -> tuple[ImageEntry | None, bool]:
        """``(命中的在线镜像, 清单是否已知)``；清单为空时行内不写「本地导入」。"""
        entries = self._catalog_entries()
        return _owner.w.match_distro_image(distro, entries), bool(entries)

    def _refresh_distro_images(self) -> None:
        """镜像清单变化后就地刷新每行的「镜像 …」说明（不重建列表）。"""
        for index in range(self.distro_list.count()):
            row = self.distro_list.itemWidget(self.distro_list.item(index))
            if isinstance(row, _owner._DistroItemRow):
                entry, known = self._distro_image(row.distro)
                row.update_image(entry, known=known)

    def _on_distro_selected(self, current: int) -> None:
        """选中行变化：只重抛光旧 / 新两行，再更新工具栏与状态栏摘要。"""
        self.distro_list.sync_selection(current)
        self._show_details()
        # 智能检测按实例缓存：切到另一个实例时刷新安装状态标签。
        self._sync_opener_detection()

    def _on_distro_double_clicked(self, item: QListWidgetItem) -> None:
        row = self.distro_list.itemWidget(item)
        if isinstance(row, _owner._DistroItemRow):
            self._on_distro_toggle(row)

    def _on_distro_toggle(self, row: _owner._DistroItemRow) -> None:
        """展开一行并在行下方挂详情；同一时间只展开一行（与镜像行一致）。"""
        self.toggle_row("distros", row)

    def render_row_detail(self, row: ItemRow) -> None:
        """展开时刷新详情内容（实例与镜像各自一套标题 / 指标）。"""
        if isinstance(row, _owner._DistroItemRow):
            size, estimated = self.sizes.get(row.distro.name, (None, False))
            self.distro_detail_view.render(row.distro, size, estimated)
        elif isinstance(row, _owner._ImageItemRow):
            self.image_detail_view.render(row.entry)

    def _show_details(self) -> None:
        """选中行变化时只更新状态栏摘要与工具栏启停按钮（详情在行内）。

        摘要属于**实例页**：3 秒状态轮询在任意页都会跑到这里，必须写页内固定 key，
        否则用户在镜像 / 设置页时会看到「Ubuntu · 已停止」这类实例摘要混进
        当前页的状态栏。
        """
        distro = self._selected()
        if distro is None:
            self.set_page_status_detail("distros", "")
            self._update_action_states()
            return
        size, estimated = self.sizes.get(distro.name, (None, False))
        self.set_page_status_detail("distros", _owner.w.summary_text(distro, size, estimated))
        self._update_action_states()

    def _update_action_states(self) -> None:
        if hasattr(self, "repair_button"):
            self.repair_button.setEnabled(self._repairable_count() > 0 and not self._busy)
        if self._busy:
            return
        distro = self._selected()
        _can_start, can_stop = _owner.w.action_states(distro.state if distro is not None else None)
        if distro is not None and distro.needs_repair:
            can_stop = False
        # 启动 / 停止只在行卡上操作，顶部不再重复；重启只对运行中的实例有意义。
        self.restart_button.setEnabled(can_stop)

    def _repairable_count(self) -> int:
        view = self.view
        if view is None:
            return 0
        return sum(1 for distro in view.distros if distro.needs_repair and distro.repair_vhd)

    def _toggle_distro_state(self, name: str) -> None:
        """行内「启动 / 停止」：聚焦该行后复用工具栏动作（口径完全一致）。"""
        row = self._row_for_distro(name)
        if row is None:
            return
        self.focus_row("distros", row)
        if row.distro.state == "running":
            self._terminate_selected()
        else:
            self._start_selected()

    def _row_for_distro(self, name: str) -> _owner._DistroItemRow | None:
        key = name.casefold()
        for index in range(self.distro_list.count()):
            row = self.distro_list.itemWidget(self.distro_list.item(index))
            if isinstance(row, _owner._DistroItemRow) and row.distro.name.casefold() == key:
                return row
        return None

    def _other_instance_names(self, name: str) -> tuple[str, ...]:
        """除当前实例外的已列出实例：作为 SSH 迁移的候选来源。"""
        key = name.casefold()
        names: list[str] = []
        for index in range(self.distro_list.count()):
            row = self.distro_list.itemWidget(self.distro_list.item(index))
            if isinstance(row, _owner._DistroItemRow) and row.distro.name.casefold() != key:
                names.append(row.distro.name)
        return tuple(names)

    def _configure_distro(self, name: str) -> None:
        """行内「配置」：保存终端默认目录，按需把实例设为默认发行版。"""
        row = self._row_for_distro(name)
        if row is None or self._busy:
            return
        self.focus_row("distros", row)
        dialog = _owner._DistroConfigDialog(
            self,
            row.distro,
            _owner.w.terminal_cwd(self.settings, row.distro.name),
            selected=_owner.w.init_task_keys(self.settings, row.distro.name),
            instances=self._other_instance_names(row.distro.name),
        )
        self._restore_dialog_geometry("distro_config", dialog)
        if dialog.exec() != _owner.QDialog.DialogCode.Accepted:
            self._save_dialog_geometry("distro_config", dialog)
            return
        self._save_dialog_geometry("distro_config", dialog)
        self._save_init_tasks(row.distro.name, dialog.init_env.selected_keys())
        make_default, cwd = dialog.payload()
        self._save_terminal_cwd(row.distro.name, cwd)
        if not make_default:
            self.set_page_status("distros", f"已保存 {row.distro.name} 的配置")
            return

        def work(_cancel: threading.Event) -> str:
            env = _owner.cli._build_environment()
            target = _owner.cli._require_target(env, row.distro.name)
            _owner.cli._run_step(env, ["--set-default", target.name], action=f"设为默认 {target.name}")
            return f"已把 {target.name} 设为默认发行版"

        self._start("设为默认", work)

    def _save_terminal_cwd(self, name: str, cwd: str) -> None:
        """把实例的终端默认目录写进窗口设置（``~`` 等同于未配置）。"""
        self.settings = _owner.w.with_terminal_cwd(self.settings, name, cwd)
        self._persist_settings(self.geometry_text())

    def _save_init_tasks(self, name: str, keys: Sequence[str]) -> None:
        """保存实例的初始化勾选，下次打开配置窗口沿用。"""
        self.settings = _owner.w.with_init_task_keys(self.settings, name, keys)

    def _save_package_mirror(self, name: str, mirror: str) -> None:
        """保存实例的软件包源（独立于系统镜像源）。"""
        self.settings = _owner.w.with_package_mirror(self.settings, name, mirror)
        self._persist_settings(self.geometry_text())

    def _save_env_status(self, name: str, status: _initenv.EnvStatus) -> None:
        """持久化环境探测结果：下次打开配置先回显，不再为看状态重启实例。"""
        self.settings = _owner.w.with_env_status(self.settings, name, status)
        self._persist_settings(self.geometry_text())

    def _save_ssh_status(self, name: str, snapshot: Any) -> None:
        """持久化「迁移 SSH 文件」上次扫描状态：打开配置直接回显，不再重扫来源。"""
        self.settings = _owner.w.with_ssh_status(self.settings, name, snapshot)
        self._persist_settings(self.geometry_text())

    def _save_opener_paths(self, name: str, found: Mapping[str, str]) -> None:
        """持久化实例内 ``command -v`` 结果：实例停止时智能检测直接复用。"""
        self.settings = _owner.w.with_opener_paths(self.settings, name, found)
        self._persist_settings(self.geometry_text())

    def _restore_dialog_geometry(self, key: str, dialog: QDialog) -> None:
        """对话框打开前恢复上次的位置与尺寸；未记录时保持按内容居中。"""
        saved = _owner.w.dialog_geometry(self.settings, key)
        if not saved:
            return
        _owner.restore_geometry(
            dialog,
            saved,
            default_size=(dialog.width(), dialog.height()),
            min_size=(max(dialog.minimumWidth(), 360), max(dialog.minimumHeight(), 240)),
        )

    def _save_dialog_geometry(self, key: str, dialog: QDialog) -> None:
        """关闭对话框时写回几何（取消打开也记位置，下次在原处打开）。"""
        geometry = getattr(dialog, "geometry", None)
        if not callable(geometry):
            return
        rect = geometry()
        text = _owner.format_geometry(rect.width(), rect.height(), rect.x(), rect.y())
        self.settings = _owner.w.with_dialog_geometry(self.settings, key, text)
        self._persist_settings(self.geometry_text())

    def _log(self, text: str) -> None:
        """追加一行任务日志（时间戳由 :meth:`format_log_line` 统一加）。"""
        self.log(text)

    def format_log_line(self, message: str) -> str:
        """任务日志前缀：``[HH:MM:SS]``。"""
        return f"[{_owner.time.strftime('%H:%M:%S')}] {message}"

    def _unregister(self) -> None:
        distro = self._selected()
        if distro is None:
            return
        dialog = _owner._UnregisterDialog(self, distro, self.sizes.get(distro.name, (None, False)))
        if dialog.exec() != _owner.QDialog.DialogCode.Accepted:
            return
        backup = dialog.backup_path()
        terminate_first = dialog.terminate_first.isChecked()

        def work(_cancel: threading.Event) -> str:
            env = _owner.cli._build_environment()
            target = _owner.cli._require_target(env, distro.name)
            if target.state == "running" and not terminate_first:
                raise _owner.invalid_argument(f"{target.name} 正在运行，请勾选「先停止」")
            estimate, estimated = _owner.distro_disk_bytes(target.base_path, target.version)
            with _owner.cli.TransferLock(target.name, directory=env.lock_dir):
                if backup is not None:
                    _owner.cli._export_for_backup(env, target.name, backup, estimate, estimated=estimated)
                if terminate_first:
                    _owner.cli._run_step(env, ["--terminate", target.name], action=f"停止 {target.name}")
                _owner.cli._run_step(env, ["--unregister", target.name], action=f"删除实例 {target.name}")
            return f"已删除实例 {target.name}" + (f"；备份在 {backup}" if backup else "")

        self._start("删除实例", work)

    def _shutdown(self) -> None:
        dialog = _owner._ShutdownDialog(self)
        if dialog.exec() != _owner.QDialog.DialogCode.Accepted:
            return

        def work(_cancel: threading.Event) -> str:
            env = _owner.cli._build_environment()
            _owner.cli._run_step(env, ["--shutdown"], action="wsl --shutdown")
            return "已关闭全部实例与 WSL 虚拟机"

        self._start("关闭全部", work)
