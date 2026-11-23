"""llmw 模型页方法（ADR 0046 P4）。"""

from __future__ import annotations

import fcmd.apps.llm._llmw_qt_center as _owner

from ._compat import (
    Any,
    Callable,
    ModelFile,
    Path,
    ProgressEvent,
    QuantOption,
    _llmw_models,
)


class ModelsJobsMixin:
    """ADR 0046 P4 迁移的模型页方法。"""

    def _sync_download_buttons(self) -> None:
        """逐行同步按钮：只有正在下载 / 排队的那一行禁用下载，转换 / 量化仍全局互斥。"""
        jobs = self._jobs
        queued = {repo.casefold() for repo, _files in self._queued}
        busy_global = bool(jobs) or self._converting or self._quantizing or self._listing_repo is not None
        for index in range(self.models.count()):
            row = self.models.itemWidget(self.models.item(index))
            if isinstance(row, _owner._ModelItemRow):
                key = row.repo.casefold()
                job = jobs.get(key)
                row.set_download_state(
                    busy=self._converting
                    or self._quantizing
                    or self._listing_repo == row.repo
                    or job is not None
                    or key in queued,
                    active=job is not None,
                    queued=key in queued and job is None,
                )
                row.set_convert_state(
                    busy=busy_global,
                    active=self._converting and row.repo == self._convert_active_repo,
                )
                row.set_quant_state(
                    busy=busy_global,
                    active=self._quantizing and row.repo == self._quant_active_repo,
                )
                running = row.serve_path is not None and self.state.is_running(row.serve_path)
                row.set_serve_state(busy=busy_global or self.state.busy, running=running)

    def _begin_quant_choice(self, repo: str, source: str) -> None:
        """后台读取文件清单，稍后弹出量化选择（不阻塞 GUI）。"""
        if self._listing_repo is not None:
            return
        self._listing_repo = repo
        self._sync_download_buttons()
        self.download_status.setVisible(True)
        self.download_bar.setValue(0)
        self.download_text.setText(f"读取 {repo} 的量化清单…")
        self._log(f"读取 {repo} 的量化清单…")
        _owner.submit(
            self._quant_options_work,
            repo,
            source,
            on_done=lambda options: self._choose_quant(repo, options),
            on_error=lambda message: self._quant_options_error(repo, message),
        )

    @staticmethod
    def _quant_options_work(repo: str, source: str) -> tuple[QuantOption, ...]:
        """读取量化清单；显式源失败时自动切到双源选优重试一次。"""
        try:
            selection = _owner.select_source(repo, source)  # type: ignore[arg-type]
        except _owner.ModelToolError:
            if source == "auto":
                raise
            selection = _owner.select_source(repo, "auto")
        return _owner.quant_options(list(selection.files))

    def _quant_options_error(self, repo: str, message: str) -> None:
        if self._listing_repo == repo:
            self._listing_repo = None
        self._sync_download_buttons()
        self.download_text.setText(f"读取清单失败：{message[:160]}")
        self.status(f"{repo} · 读取清单失败")
        self._log(f"读取量化清单失败 {repo}: {message}")

    def _choose_quant(self, repo: str, options: tuple[QuantOption, ...]) -> None:
        """下载选择：默认整仓全部文件，也可在弹窗里只下某个量化。"""
        if self._listing_repo == repo:
            self._listing_repo = None
        self._sync_download_buttons()
        if not options:
            self._log(f"{repo} 没有量化选项，下载整仓")
            self.start_download(repo, files=())
            return
        labels = ["全部文件（整仓，避免漏项）", *(option.display for option in options)]
        choice, accepted = _owner.QInputDialog.getItem(self, "选择下载内容", "下载哪些文件：", labels, 0, False)
        if not accepted or not choice:
            self._update_download_summary()
            self.status(f"{repo} · 已取消下载")
            self._log(f"已取消下载 {repo}：未选择下载内容")
            return
        if choice == labels[0]:
            self._log(f"下载 {repo} 全部文件")
            self.start_download(repo, files=())
            return
        index = labels.index(choice) - 1
        option = options[index]
        self._log(f"选择下载 {repo} · {option.label}")
        self.start_download(repo, files=option.files)

    def start_download(self, repo: str, files: tuple[ModelFile, ...] | None = None) -> None:
        """下载指定仓库到模型目录；未指定量化时先读取文件清单让用户选择。

        支持多仓库并行：正在下载的数量达到 ``download.parallel`` 上限时先排队，
        完成一个后自动开始下一个；同一仓库不会重复入队。
        """
        if self._converting or self._quantizing or not repo:
            return
        key = repo.casefold()
        if key in self._jobs or any(queued.casefold() == key for queued, _ in self._queued):
            self._log(f"{repo} 已在下载任务 / 队列中")
            return
        settings = _owner._llmw_models.settings_from_dict(self.state.settings.download)
        if files is None and not settings.include.strip() and not settings.exclude.strip():
            self._begin_quant_choice(repo, settings.source)
            return
        selected = tuple(files) if files is not None else ()
        if len(self._jobs) >= max(1, settings.parallel):
            self._queued.append((repo, selected))
            self._log(f"{repo} 已加入下载队列（并发上限 {settings.parallel}）")
            self.status(f"{repo} 已排队，等待空闲下载位…")
            self._update_download_summary()
            self._sync_download_buttons()
            return
        self._start_job(repo, selected, settings)

    def _start_job(
        self,
        repo: str,
        files: tuple[ModelFile, ...],
        settings: _llmw_models.DownloadGuiSettings,
    ) -> None:
        """创建并启动一个下载任务（``files`` 为空表示按设置 / 整仓下载）。"""
        dest = _owner._llmconfig.models_dir()
        key = repo.casefold()
        job = _owner._DownloadJob(
            repo=repo, target=dest / repo, bridge=_owner.ProgressBridge(self), speed=_owner.SpeedMeter()
        )
        job.bridge.progress.connect(lambda event, job=job: self._on_progress(job, event))
        self._jobs[key] = job
        # 多个模型并行时按上限分摊 workers，避免总连接数成倍膨胀。
        per_job = max(2, settings.workers // max(1, settings.parallel))
        self._log(f"开始下载 {repo} -> {job.target}（并发文件 {per_job}）")
        self._update_download_summary()
        self._sync_download_buttons()
        _owner.submit(
            self._download_work,
            repo,
            dest,
            settings.source,
            settings.include,
            settings.exclude,
            per_job,
            settings.verify,
            tuple(file.path for file in files),
            should_stop=lambda job=job: job.stop_requested,
            reporter=job.bridge.progress.emit,
            on_done=lambda target, key=key: self._download_done(key, target),
            on_error=lambda message, key=key: self._download_error(key, message),
            on_finished=lambda key=key: self._download_finished(key),
        )

    def _download_work(  # noqa: PLR0913, PLR0917 - 下载参数成组传入
        self,
        repo: str,
        dest: Path,
        source: str,
        include: str,
        exclude: str,
        workers: int,
        verify: bool,
        only_paths: tuple[str, ...] = (),
        *,
        should_stop: Callable[[], bool] | None = None,
        reporter: Callable[[ProgressEvent], None] | None = None,
    ) -> str:
        self._log(f"获取 {repo} 的 {source} 文件清单…")
        selection = _owner.select_source(repo, source)  # type: ignore[arg-type]
        choice = _owner.describe_selection(selection)
        self._log(f"{choice}，仓库共 {len(selection.files)} 个文件")
        self.status(choice)
        if only_paths:
            # 显式选中文件（文件列表 / 量化选项）优先于设置里的包含 / 排除掩码，
            # 否则选中的 VAE / 文本编码器可能被掩码过滤掉。
            wanted = set(only_paths)
            selected = [file for file in selection.files if file.path in wanted]
        else:
            selected = _owner.filter_files(
                list(selection.files),
                _owner._llmw_models.parse_patterns(include),
                _owner._llmw_models.parse_patterns(exclude),
            )
        if not selected:
            if only_paths:
                raise _owner.ModelToolError("选中的文件已不在仓库清单里，请刷新后重试")
            raise _owner.ModelToolError("过滤后没有文件，请检查设置中的包含 / 排除掩码")
        target = dest / repo
        self._log(f"待下载 {len(selected)} 个文件，目标 {target}")
        _owner.download_files(
            repo,
            selected,
            target,
            selection,
            workers=workers,
            verify=verify,
            reporter=reporter,
            should_stop=should_stop,
        )
        return str(target)

    def _on_progress(self, job: _owner._DownloadJob, event: ProgressEvent) -> None:
        if event.task != "总进度":
            return
        job.completed = event.completed
        job.total = event.total
        if event.total > 0:
            totals = dict(self.state.settings.download_totals or {})
            totals[job.repo.casefold()] = event.total
            self.state.settings.download_totals = totals
        job.speed.update(event.completed)
        self._update_download_summary()

    def _update_download_summary(self) -> None:
        """汇总所有并行任务：底部显示总进度 / 总速度 / 排队数。"""
        jobs = list(self._jobs.values())
        if not jobs:
            self.download_bar.setValue(0)
            self.download_text.clear()
            self.download_status.setVisible(False)
            self.stop_download_button.setVisible(False)
            self.stop_download_button.setEnabled(True)
            return
        completed = sum(job.completed for job in jobs)
        total = sum(job.total for job in jobs)
        speed = sum(job.speed.speed for job in jobs)
        if total > 0:
            self.download_bar.setValue(min(100, round(100 * completed / total)))
        size_text = (
            f"{_owner.human_size(completed)} / {_owner.human_size(total)}"
            if total > 0
            else _owner.human_size(completed)
        )
        eta = (total - completed) / speed if total > completed and speed > 0 else None
        eta_text = f" · 剩余 {_owner.format_eta(eta)}" if eta is not None else ""
        queue_text = f" · 排队 {len(self._queued)}" if self._queued else ""
        prefix = f"下载 {jobs[0].repo}" if len(jobs) == 1 else f"下载 {len(jobs)} 个模型"
        self.download_status.setVisible(True)
        self.download_text.setText(f"{prefix} · {size_text} · {_owner.format_speed(speed)}{eta_text}{queue_text}")
        self.stop_download_button.setVisible(True)
        self.stop_download_button.setEnabled(any(not job.stop_requested for job in jobs))
        self.stop_download_button.setToolTip("停止全部下载" if len(jobs) > 1 else "停止当前下载")

    def _drain_queue(self) -> None:
        """有空闲下载位时从队列取下一个任务。"""
        if not self._queued:
            return
        settings = _owner._llmw_models.settings_from_dict(self.state.settings.download)
        limit = max(1, settings.parallel)
        while self._queued and len(self._jobs) < limit:
            repo, files = self._queued.pop(0)
            self._start_job(repo, files, settings)

    def start_convert(self, repo: str) -> None:
        """把已安装的 HF safetensors 模型转换为 GGUF，进度复用底部状态区。"""
        if self._converting or bool(self._jobs) or self._quantizing or not repo:
            return
        info = self._convertible.get(repo.casefold())
        if info is None:
            return
        source, output, converted = info
        settings = _owner._llmw_convert.convert_settings_from_dict(self.state.settings.convert)
        if converted or (output.is_file() and not settings.overwrite):
            self.refresh_installed()
            self._render_results()
            return
        self._converting = True
        self._convert_active_repo = repo
        self._convert_cancel = _owner.threading.Event()
        self.download_status.setVisible(True)
        # 准备阶段（探测解释器 / 获取源码 / 检查架构）没有百分比：用忙碌条表示在动。
        self.download_bar.setRange(0, 0)
        self.download_text.setText(f"准备转换 {repo}…")
        self.stop_download_button.setText("停止转换")
        self.stop_download_button.setToolTip("停止转换（含 llama.cpp 源码下载）")
        self.stop_download_button.setEnabled(True)
        self.stop_download_button.setVisible(True)
        self._sync_download_buttons()
        self._log(f"开始转换 {source} -> {output}")
        self._bridge = _owner.ProgressBridge(self)
        self._bridge.progress.connect(self._on_convert_progress)
        form = _owner._llmw_convert._ConvertForm(
            source=str(source),
            output=str(output),
            outtype=settings.outtype,
            script=settings.script,
            python=settings.python,
            tag=settings.tag,
        )
        _owner.submit(
            self._convert_work,
            form,
            on_done=lambda produced: self._convert_done(repo, produced),
            on_error=lambda message: self._convert_error(repo, message),
            on_finished=self._convert_finished,
        )

    def _convert_work(self, form: _owner._llmw_convert._ConvertForm) -> list[str]:
        source = _owner.Path(form.source).expanduser()

        def report(text: str) -> None:
            """准备阶段进度：写日志并更新底部状态，避免长时间看起来「卡死」。"""
            self._log(text)
            if self._bridge is not None:
                self._bridge.progress.emit(text)

        cancel = self._convert_cancel
        # 解释器统一走 find_convert_python：留空用本应用解释器，设置页填了就只探测它，
        # 路径写错时在准备阶段就报明确错误，不会拖到源码下载 / 启动子进程之后。
        python = _owner.llmconvert.find_convert_python(python=form.python or None, on_status=report, cancel=cancel)
        plan = _owner.llmconvert.plan_convert(
            source,
            output=_owner.Path(form.output),
            outtype=form.outtype,
            script=_owner.Path(form.script).expanduser() if form.script else None,
            python=python,
            tag=form.tag or None,
            on_status=report,
            cancel=cancel,
        )
        last = -1

        def relay(line: str) -> None:
            nonlocal last
            percent = _owner._llmw_convert._progress_percent(line)
            if percent is None:
                self._log(line)
            elif percent >= last:
                last = percent
                self._bridge.progress.emit(percent) if self._bridge is not None else None

        return [
            str(path)
            for path in _owner.llmconvert.convert_model(plan, on_output=relay, on_status=report, cancel=cancel)
        ]

    def _on_convert_progress(self, value: object) -> None:
        if isinstance(value, int):
            if self.download_bar.maximum() == 0:
                self.download_bar.setRange(0, 100)
            self.download_bar.setValue(value)
            self.download_text.setText(f"转换 {self._convert_active_repo or '-'} · {value}%")
        elif isinstance(value, str) and value:
            # 准备阶段的状态行：解释器探测 / 下载源码 / clone / 启动脚本。
            self.download_text.setText(value)

    def _convert_done(self, repo: str, produced: list[str]) -> None:
        self.download_bar.setRange(0, 100)
        self.download_bar.setValue(100)
        self.stop_download_button.setVisible(False)
        self.download_text.setText(f"转换完成：{repo}")
        self._log(f"转换完成 {repo}: {'、'.join(produced)}")
        self.refresh_installed()
        self._render_results()

    def _convert_error(self, repo: str, message: str) -> None:
        self.download_bar.setRange(0, 100)
        self.stop_download_button.setVisible(False)
        if "已取消转换" in message:
            self.download_text.setText(f"已取消转换：{repo}")
            self._log(f"已取消转换 {repo}")
            return
        self.download_text.setText(f"转换失败：{repo} · {message}")
        self._log(f"转换失败 {repo}: {message}")

    def _convert_finished(self) -> None:
        self._converting = False
        self._convert_active_repo = None
        self._convert_cancel = None
        self.stop_download_button.setText("停止")
        self.stop_download_button.setToolTip("停止当前下载")
        self.stop_download_button.setVisible(False)
        self._sync_download_buttons()

    def _on_quick_serve(self, repo: str) -> None:
        """选择量化精度后快捷启动；只有一个精度时直接启动。"""
        if self._quantizing or bool(self._jobs) or self._converting:
            return
        key = repo.casefold()
        paint_path = self._paint_models.get(key)
        if paint_path is not None:
            self._log(f"快捷启动画图服务: {paint_path}")
            self.status(f"正在准备启动画图服务 {paint_path.name}…")
            self.state.request_paint_model(paint_path)
            return
        files = self._quantized_files.get(key, ())
        if not files:
            # 已转换但还没量化：直接启动转换产物（f16 GGUF 可由 llama-server 加载）。
            convert_info = self._convertible.get(key)
            if convert_info is not None and convert_info[2]:
                self._request_serve(convert_info[1], key)
            return
        if len(files) == 1:
            self._request_serve(files[0][1], key)
            return
        default = self._default_quant(key)
        menu = _owner.QMenu(self)
        for label, path in files:
            suffix = "（上次启动）" if label == default else ""
            action = menu.addAction(f"{label}{suffix}  {path.name}")
            action.triggered.connect(lambda _checked=False, target=path: self._request_serve(target, key))
        menu.exec(_owner.QCursor.pos())

    def _request_serve(self, path: Path, repo_key: str = "") -> None:
        """请求窗口切到运行页并启动指定量化模型，同时记住该仓库的精度选择。"""
        if repo_key:
            label = _owner._llmw_models.quant_precision_label(path)
            if label:
                choices = dict(self.state.settings.quant_choice or {})
                choices[repo_key] = label
                self.state.settings.quant_choice = choices
                self.state.persist()
        self._log(f"快捷启动: {path}")
        self.status(f"正在准备启动 {path.name}…")
        self.state.request_serve_model(path)

    def _on_quantize_requested(self, repo: str) -> None:
        """点击「量化」后弹出精度选项，选中后立即开始单精度量化。"""
        if self._quantizing or bool(self._jobs) or self._converting:
            return
        if repo.casefold() not in self._quant_candidates:
            return
        menu = _owner.QMenu(self)
        existing = self._quant_tags.get(repo.casefold(), ())
        for quant, desc, done in _owner.quantize_menu_items(existing):
            if done:
                action = menu.addAction(f"{quant}  {desc}（已量化）")
                action.setCheckable(True)
                action.setChecked(True)
                action.setEnabled(False)
                continue
            action = menu.addAction(f"{quant}  {desc}")
            action.triggered.connect(lambda _checked=False, quant=quant, repo=repo: self.start_quantize(repo, quant))
        menu.exec(_owner.QCursor.pos())

    def start_quantize(self, repo: str, quant: str) -> None:
        """对指定仓库中最大的全量 GGUF 执行一次量化。"""
        if self._quantizing or bool(self._jobs) or self._converting:
            return
        if quant.casefold() in {tag.casefold() for tag in self._quant_tags.get(repo.casefold(), ())}:
            self.status(f"{repo} · {quant} 已量化，无需重复执行")
            self._log(f"跳过重复量化 {repo}: {quant} 已存在")
            return
        files = self._quant_candidates.get(repo.casefold())
        if not files:
            return
        source = max(files, key=lambda path: path.stat().st_size if path.is_file() else 0)
        try:
            exe = _owner.llmqnt.resolve_quantize_exe()
            jobs = _owner.llmqnt.plan_jobs(source, [quant])
            pending, _skipped = _owner.llmqnt.pending_jobs(jobs)
        except (OSError, _owner.llmqnt.QuantizeError) as exc:
            self.download_status.setVisible(True)
            self.download_text.setText(f"无法量化：{repo} · {exc}")
            self._log(f"无法量化 {repo}: {exc}")
            return
        if not pending:
            self.download_status.setVisible(True)
            self.download_bar.setValue(100)
            self.download_text.setText(f"已存在：{repo} · {quant}")
            self.refresh_installed()
            self._render_results()
            return
        self._quantizing = True
        self._quant_active_repo = repo
        self.download_status.setVisible(True)
        self.download_bar.setValue(0)
        self.download_text.setText(f"准备量化 {repo} · {quant}…")
        self.stop_download_button.setVisible(False)
        self._sync_download_buttons()
        self._log(f"开始量化 {source} -> {quant}")
        self._bridge = _owner.ProgressBridge(self)
        self._bridge.progress.connect(self._on_quant_progress)
        _owner.submit(
            self._quant_work,
            pending,
            exe,
            on_done=lambda success: self._quant_done(repo, success),
            on_error=lambda message: self._quant_error(repo, message),
            on_finished=self._quant_finished,
        )

    def _quant_work(self, pending: list[Any], exe: Path) -> bool:
        success = True
        total = len(pending)
        for index, job in enumerate(pending, 1):
            try:
                _owner.llmqnt.run_job(job, exe, on_output=self._log)
            except _owner.llmqnt.QuantizeError as exc:
                success = False
                self._log(f"量化 {job.quant} 失败: {exc}")
            else:
                self._log(f"已生成: {job.output}")
            if self._bridge is not None:
                self._bridge.progress.emit(index * 100 // total)
        return success

    def _on_quant_progress(self, value: object) -> None:
        if isinstance(value, int):
            self.download_bar.setValue(value)
            self.download_text.setText(f"量化 {self._quant_active_repo or '-'} · {value}%")

    def _quant_done(self, repo: str, success: bool) -> None:
        self.download_bar.setValue(100 if success else self.download_bar.value())
        self.download_text.setText(f"量化{'完成' if success else '结束'}：{repo}")
        self._log(f"量化{'完成' if success else '结束'} {repo}")
        self.refresh_installed()
        self._render_results()

    def _quant_error(self, repo: str, message: str) -> None:
        self.download_text.setText(f"量化失败：{repo} · {message}")
        self._log(f"量化失败 {repo}: {message}")

    def _quant_finished(self) -> None:
        self._quantizing = False
        self._quant_active_repo = None
        self._sync_download_buttons()

    def _download_done(self, key: str, target: str) -> None:
        job = self._jobs.get(key)
        repo = job.repo if job is not None else key
        self._log(f"下载完成 {repo}: {target}")
        self.downloadFinished.emit()

    def _download_error(self, key: str, message: str) -> None:
        job = self._jobs.get(key)
        repo = job.repo if job is not None else key
        self._log(f"下载失败 {repo}: {message}")
        self.status(f"{repo} · 下载失败：{message[:120]}")

    def _download_finished(self, key: str) -> None:
        job = self._jobs.pop(key, None)
        if job is not None:
            self._log(f"下载结束 {job.repo}")
        self.state.persist()
        # 成功 / 失败 / 手动停止都会走到这里：重扫模型目录（成功后行会切到
        # 已下载 / 已安装状态；中途结束则重算 .part 进度），再重绘当前结果。
        self.refresh_installed()
        self._render_results()
        self._sync_download_buttons()
        self._drain_queue()
        self._update_download_summary()
        # 文件列表的状态列（已下载 / 部分下载）跟着刷新，不必重新拉在线清单。
        self.detail_view.refresh_file_states()

    def stop_download(self) -> None:
        if self._converting and self._convert_cancel is not None:
            self._convert_cancel.set()
            self.stop_download_button.setEnabled(False)
            self.download_text.setText("正在停止转换…")
            self._log("正在停止转换…")
            return
        if not self._jobs:
            return
        for job in self._jobs.values():
            job.stop_requested = True
        if self._queued:
            self._queued.clear()
            self._sync_download_buttons()
        self.stop_download_button.setEnabled(False)
        self.download_text.setText("正在停止下载…")
        self._log("正在停止下载…")
