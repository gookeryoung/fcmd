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


class ActionsMixin:
    """ADR 0046 P2/P3 迁移的窗口方法。"""

    def _open_selected(self) -> None:
        distro = self._selected()
        if distro is None:
            return
        try:
            env = _owner.cli._build_environment()
            command = _owner.build_command(
                wsl_path=_owner.cli._require_wsl(env),
                distro=distro.name,
                cwd=_owner.w.terminal_cwd(self.settings, distro.name),
                user=None,
                program=env.config.terminal_program,
                extra_args=env.config.terminal_extra_args,
            )
            self.launcher.launch(command)
        except (_owner.LaunchError, _owner.WslmanError) as exc:
            _owner.QMessageBox.critical(self, "打开失败", _owner.w._error_text(exc))
            return
        self._log(f"[打开] 已启动 {distro.name}")

    def _launch_keepalive(self, env: Any, name: str) -> None:
        """用看护进程把实例带起来，并等状态真正变成 Running（后台线程调用）。"""
        _owner._ensure_running(env, name)

    def _start_selected(self) -> None:
        distro = self._selected()
        if distro is None:
            self.set_page_status("distros", "未选中实例；先选中一行再启动")
            return
        # 不用行卡里缓存的 state 提前返回：列表可能是过期的（注册信息变了、实例刚被
        # 移动 / 迁移），真正的状态由后台新环境重新探测；确实在运行时 worker 会如实返回。

        def work(_cancel: threading.Event) -> str:
            env = _owner.cli._build_environment()
            target = _owner.cli._require_target(env, distro.name)
            if target.state == "running":
                return f"{target.name} 已在运行"
            self._launch_keepalive(env, target.name)
            return f"已启动 {target.name}"

        self._start("启动", work)

    def _restart_selected(self) -> None:
        """重启：先停止再重新拉起；只在运行中可用，忙碌时与其他动作一起禁用。"""
        distro = self._selected()
        if distro is None:
            return
        if distro.state != "running":
            self.set_page_status("distros", f"{distro.name} 未运行，无需重启")
            self._update_action_states()
            return
        if not _owner.confirm(self, "重启实例", f"重启 {distro.name}？正在运行的任务会被中断。"):
            return

        def work(_cancel: threading.Event) -> str:
            env = _owner.cli._build_environment()
            _owner.cli._run_step(env, ["--terminate", distro.name], action=f"停止 {distro.name}（重启）")
            target = _owner.cli._require_target(env, distro.name)
            self._launch_keepalive(env, target.name)
            return f"已重启 {target.name}"

        self._start("重启", work)

    def _terminate_selected(self) -> None:
        distro = self._selected()
        if distro is None:
            return
        if not _owner.confirm(self, "停止实例", f"停止 {distro.name}？正在运行的任务会被中断。"):
            return

        def work(_cancel: threading.Event) -> str:
            env = _owner.cli._build_environment()
            # CLI 子命令仍是 terminate，只有展示层文案叫「停止」。
            _owner.cli._run_step(env, ["--terminate", distro.name], action=f"停止 {distro.name}")
            return f"已停止 {distro.name}"

        self._start("停止", work)

    def _export_selected(self) -> None:
        """导出到任意位置：默认建议落到备份目录的同名 ``.tar``。"""
        distro = self._selected()
        if distro is None:
            return
        target, _ = _owner.QFileDialog.getSaveFileName(
            self,
            f"导出实例 {distro.name}",
            str(self._backup_dir_path() / f"{distro.name}.tar"),
            "tar 归档 (*.tar)",
        )
        if not target:
            return
        terminate_first = self._export_running_choice(distro)
        if terminate_first is None:
            return
        self._export_to(distro, _owner.Path(target), terminate_first=terminate_first, label="导出")

    def _backup_selected(self) -> None:
        """一键备份：直接导出到备份目录的 ``<实例>.tar``。

        备份目录就是镜像行「已备份」胶囊的判定来源：备份完成后胶囊与筛选立即生效，
        换机 / 重装 / 删实例前不用再临时挑路径。
        """
        distro = self._selected()
        if distro is None:
            return
        path = self._backup_dir_path() / f"{distro.name}.tar"
        if path.exists() and not _owner.confirm(
            self, "备份文件已存在", f"{path} 已存在，覆盖这个备份？\n\n（旧备份内容会被替换）"
        ):
            return
        terminate_first = self._export_running_choice(distro)
        if terminate_first is None:
            return
        self._export_to(distro, path, terminate_first=terminate_first, label="备份")

    def _export_running_choice(self, distro: Distro) -> bool | None:
        """运行中导出的二选一：``True`` 先停止、``False`` 接受风险、``None`` 取消。"""
        if distro.state != "running":
            return False
        answer = _owner.QMessageBox.question(
            self,
            "实例运行中",
            "运行中导出可能不一致。选择「是」先停止，选择「否」接受风险继续？",
            _owner.QMessageBox.StandardButton.Yes
            | _owner.QMessageBox.StandardButton.No
            | _owner.QMessageBox.StandardButton.Cancel,
            _owner.QMessageBox.StandardButton.Cancel,
        )
        if answer == _owner.QMessageBox.StandardButton.Cancel:
            return None
        return answer == _owner.QMessageBox.StandardButton.Yes

    def _export_to(self, distro: Distro, path: Path, *, terminate_first: bool, label: str) -> None:
        """导出流水线（与 CLI ``export`` 同口径）：预检 → 流式导出 → 原子改名的 ``.part``。

        ``label`` 只影响状态行 / 日志文案（「导出」/「备份」），执行细节一致。
        """

        def work(_cancel: threading.Event) -> str:
            env = _owner.cli._build_environment()
            current = _owner.cli._require_target(env, distro.name)
            _owner.cli._guard_running_export(
                current,
                terminate_first=terminate_first,
                allow_running=not terminate_first,
                command=f"fcmd wslman export {current.name} {path}",
            )
            estimate, estimated = _owner.distro_disk_bytes(current.base_path, current.version)
            progress_estimate, _ = _owner.distro_export_bytes(current.base_path, current.version)
            check = _owner.cli._preflight_for(env, path, estimate, estimated=estimated)
            if not check.ok:
                raise _owner.runtime_error(f"目标卷空间不足：{check.detail}", hint="换一个位置或清理磁盘")
            parts = _owner.cli._export_parts(path, "none")

            def report(done: int, total: int | None) -> None:
                self._export_bridge.event.emit((current.name, done, total))

            try:
                size, digest = _owner.cli._export_stream(
                    env,
                    current.name,
                    path,
                    parts,
                    level=6,
                    terminate_first=terminate_first,
                    estimate=progress_estimate if progress_estimate is not None else estimate,
                    progress=report,
                )
            except BaseException:
                _owner.cli._abort_parts([item for item in parts if item is not None], keep=env.config.keep_partial)
                raise
            return f"已{label} {path}（{_owner.format_bytes(size)}，sha256 {digest}）"

        self._start(label, work)

    def _migrate_selected(self) -> None:
        """把实例整体迁到另一个已配置的工作目录（导出 → 删除 → 导入，失败回滚）。"""
        distro = self._selected()
        if distro is None:
            return
        if distro.state == "running":
            _owner.QMessageBox.information(
                self,
                "请先停止实例",
                f"{distro.name} 正在运行。迁移会导出并重新导入整个实例，请先点「停止」再迁移。",
            )
            return
        roots = list(_owner.w.work_dir_paths(self.settings))
        current_root = _owner.w.work_dir_of(self.settings, distro.base_path)
        targets = [item for item in roots if current_root is None or not _owner.w.same_dir(item, current_root)]
        if not targets:
            _owner.QMessageBox.information(
                self,
                "没有其他工作目录",
                "先在设置页添加另一个工作目录，再迁移实例。",
            )
            return
        labels = [f"{item.name or item} · {item}" for item in targets]
        choice, accepted = _owner.QInputDialog.getItem(
            self,
            f"迁移 {distro.name}",
            "目标工作目录（会导入到它的 install 子目录）：",
            labels,
            0,
            False,
        )
        if not accepted or not choice:
            return
        target_root = targets[labels.index(choice)]
        target = target_root / "install" / distro.name
        if not _owner.confirm(
            self,
            "迁移实例",
            f"将 {distro.name} 整体迁移到：\n{target}\n\n"
            "迁移会先导出、删除原实例，再导入到新位置；任一步失败会自动回滚到原位置。\n"
            "实例必须已停止。继续？",
        ):
            return

        def work(_cancel: threading.Event) -> str:
            return self._migrate_worker(distro, target_root, target)

        self._start("迁移", work)

    def _migrate_worker(self, distro: Distro, target_root: _owner.Path, target: _owner.Path) -> str:
        """迁移工作线程：导出到目标卷 → 删除旧实例 → 导入；失败尝试导回原位置。"""
        env = _owner.cli._build_environment()
        current = _owner.cli._require_target(env, distro.name)
        if current.state == "running":
            raise _owner.runtime_error(f"实例 {current.name} 正在运行", hint="先停止实例再迁移")
        if current.base_path and _owner.w.same_dir(_owner.Path(current.base_path), target):
            return f"{current.name} 已经位于 {target}"
        problem = _owner.cli._dir_problem(target)
        if problem:
            raise _owner.invalid_argument(f"目标目录不可用：{problem}", hint="目标目录必须为空且位于本地磁盘")

        estimate, estimated = _owner.distro_disk_bytes(current.base_path, current.version)
        export_estimate, _ = _owner.distro_export_bytes(current.base_path, current.version)
        need = estimate + (export_estimate or 0)
        check = _owner.cli._preflight_for(env, target_root, need, estimated=estimated)
        if not check.ok:
            raise _owner.runtime_error(
                f"目标工作目录可用空间不足：{check.detail}",
                hint="换一个工作目录，或清理磁盘后重试",
            )

        original = _owner.Path(current.base_path) if current.base_path else None
        version = str(current.version) if current.version else ""
        # 临时包放在目标工作目录根下（与最终 vhdx 同一卷）：导入时不用跨盘读大文件，
        # 又不会把临时文件放进要求为空的 install/<实例> 目录里。
        temp = target_root / f".{current.name}-migrate.tar"
        part = _owner.cli.part_path(temp)
        target_root.mkdir(parents=True, exist_ok=True)
        with _owner.contextlib.suppress(OSError):
            part.unlink()

        def emit(message: str) -> None:
            if self._bridge is not None:
                self._bridge.event.emit(message)

        emit(f"[迁移] 导出 {current.name} → {temp}")
        try:
            _owner.cli._run_step(
                env,
                ["--export", current.name, str(part)],
                action=f"导出 {current.name}",
                hint="检查目标工作目录的空间与权限",
                timeout=3600.0,
            )
            _owner.cli.atomic_replace(part, temp)
            emit(f"[迁移] 删除旧实例 {current.name}（数据已导出到临时包）")
            _owner.cli._run_step(env, ["--unregister", current.name], action=f"删除实例 {current.name}")
            import_args = ["--import", current.name, str(target), str(temp)]
            if version:
                import_args.extend(["--version", version])
            target.mkdir(parents=True, exist_ok=True)
            emit(f"[迁移] 导入 {current.name} → {target}")
            _owner.cli._run_step(
                env,
                import_args,
                action=f"迁移 {current.name}",
                hint="导入失败会自动尝试导回原位置",
                timeout=3600.0,
            )
        except BaseException as exc:
            emit(f"[迁移] 导入失败，正在回滚到原位置 {original or '未知'}")
            with _owner.contextlib.suppress(Exception):
                _owner.cli._run_step(env, ["--unregister", current.name], action=f"清理半成品 {current.name}")
            rollback_error: str | None = None
            if original is None or not temp.is_file():
                rollback_error = f"缺少原位置或临时包（{temp}）"
            else:
                try:
                    original.mkdir(parents=True, exist_ok=True)
                    args = ["--import", current.name, str(original), str(temp)]
                    if version:
                        args.extend(["--version", version])
                    _owner.cli._run_step(env, args, action=f"回滚 {current.name}", timeout=3600.0)
                except Exception as rollback_exc:  # 回滚失败要让用户知道临时包仍在
                    rollback_error = _owner.w._error_text(rollback_exc)
            if rollback_error is not None:
                raise _owner.runtime_error(
                    f"迁移失败且回滚未完成：{rollback_error}",
                    hint=f'临时包保留在 {temp}；可手动执行 wsl --import {current.name} <原目录> "{temp}"',
                ) from exc
            raise _owner.runtime_error(
                f"迁移失败，已回滚到原位置 {original or '未知'}：{_owner.w._error_text(exc)}",
                hint="原实例已恢复；检查目标目录空间与权限后重试",
            ) from exc

        with _owner.contextlib.suppress(OSError):
            temp.unlink()
        after = _owner.cli._try_view(env)
        landed = after.find(current.name) if after is not None else None
        if landed is not None and landed.base_path and not _owner.w.same_dir(landed.base_path, target):
            return f"已迁移 {current.name}，但复核时磁盘位置仍为 {landed.base_path}；请刷新列表确认"
        return f"已迁移 {current.name} 到 {target}"
