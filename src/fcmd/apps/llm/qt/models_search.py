"""llmw 模型页方法（ADR 0046 P4）。"""

from __future__ import annotations

import fcmd.apps.llm._llmw_qt_center as _owner

from ._compat import (
    ItemRow,
    ModelFile,
    ModelInfo,
    ModelListCache,
    ModelSearch,
    ModelSummary,
    Path,
    QListWidgetItem,
    Sequence,
)


class ModelsSearchMixin:
    """ADR 0046 P4 迁移的模型页方法。"""

    def start_search(self, page: int, *, force: bool = False) -> None:
        """按当前筛选拉取指定页；命中新鲜缓存时直接展示，``force`` 跳过缓存。"""
        if self._searching:
            # 切换分类时上一页请求还没回来：记下最新目标，等它结束后再拉，
            # 不能直接 return，否则 chip 切了但列表仍是旧分类 / 空列表。
            self._pending_search = (max(1, page), force)
            return
        target = max(1, page)
        key = _owner.cache_key(self._filter_query, target, _owner._PAGE_SIZE, self._sort_key)
        cached = self._list_cache.get(key)
        self._page = target
        if cached is not None:
            self._render_search(cached.search)
            if cached.fresh and not force:
                self._log(f"模型列表命中缓存（{cached.age:.0f}s 前）：{self._filter_query or '全部'}")
                return
        self._searching = True
        text = f"正在加载第 {self._page} 页…" if cached is None else f"正在刷新第 {self._page} 页…"
        self.status(text)
        self._begin_loading("search", text)
        _owner.submit(
            self._search_work,
            self._list_cache,
            key,
            self._filter_query,
            self._page,
            _owner._PAGE_SIZE,
            self._sort_key,
            on_done=self._search_online_done,
            on_error=self._search_error,
            on_finished=self._search_finished,
        )

    def _search_online_done(self, result: ModelSearch) -> None:
        """在线请求成功：记录更新时间后渲染（缓存命中不走这里）。"""
        self.state.mark_models_updated()
        self._render_search(result)

    @staticmethod
    def _search_work(  # noqa: PLR0913, PLR0917 - 缓存键与请求参数成组透传
        cache: ModelListCache,
        key: str,
        keyword: str,
        page: int,
        page_size: int,
        sort: str,
    ) -> ModelSearch:
        """后台线程：请求网络并把结果写入缓存，避免 GUI 线程落盘。"""
        terms = _owner._llmw_models.MULTI_SEARCH_TERMS.get(keyword)
        if terms is not None:
            # 文本 / 图像是合并分类：单一中文关键字往往漏掉官方仓库或偏向某一类，
            # 按细分并行检索后合并，再由 matches_filter 在本地精确归类。
            result = _owner.search_models_multi(terms, page, page_size, sort)
        else:
            result = _owner.search_models(keyword, page, page_size, sort)
        cache.put(key, result)
        return result

    def _search_finished(self) -> None:
        self._searching = False
        self._end_loading("search")
        if self._pending_search is not None:
            page, force = self._pending_search
            self._pending_search = None
            self.start_search(page, force=force)

    def _search_error(self, message: str) -> None:
        self.status("模型列表加载失败")
        self._log(f"模型列表加载失败: {message}")

    def _remember_items(self, items: Sequence[ModelSummary]) -> None:
        """记住已浏览过的条目：切到「全部 / 适合本机」时不能把刚看到的模型弄丢。"""
        for item in items:
            self._seen_items[item.repo.casefold()] = item
        overflow = len(self._seen_items) - _owner._SEEN_LIMIT
        for key in list(self._seen_items)[: max(0, overflow)]:
            del self._seen_items[key]

    def _render_search(self, result: ModelSearch) -> None:
        self._remember_items(result.items)
        self._last_result = result
        self._render_results()

    def open_repo(self, repo: str) -> None:
        """直接打开 ``组织/模型``：生成单行结果并就地展开详情。"""
        item = _owner.ModelSummary(repo=repo)
        self._remember_items((item,))
        self._last_result = _owner.ModelSearch(items=(item,), total=1)
        self._render_results()
        self._expand_repo(repo)

    def _render_results(self) -> None:  # noqa: PLR0912 - 列表组合筛选集中在一处
        result = self._last_result
        if result is None:
            return
        filter_label = next((label for key, label, _query in _owner._MODEL_FILTERS if key == self._filter_key), "")
        installed_items = tuple(self._installed_models.values())
        present = {item.repo.casefold() for item in result.items}
        # 分类搜索各拉各的关键字页；把浏览过的条目并回来，保证「全部 / 适合本机」
        # 至少包含刚在「画图 / 编程 / …」里看到的模型，不会一切分类就消失。
        remembered = [summary for key, summary in self._seen_items.items() if key not in present]
        if self._source_filter == "installed":
            base = list(installed_items)
        elif self._source_filter == "online":
            base = [*result.items, *remembered]
        else:
            base = [*result.items, *remembered] + [
                summary for key, summary in self._installed_models.items() if key not in present
            ]
        # 名称搜索是明确意图：即使停在「适合本机」也先展示全部命中，否则官方仓库
        # 会因整仓体积（非 GGUF）被容量过滤掉，用户会以为「搜不到」。
        if self._filter_key == "local" and not self._name_filter.strip():
            base = list(
                _owner._llmw_models.filter_by_local_fit(
                    tuple(base),
                    self._local_capacity(),
                    pinned=self._installed_name_keys(),
                )
            )
        elif self._filter_key not in ("all", ""):
            base = [item for item in base if _owner._llmw_models.matches_filter(item, self._filter_key)]
        items = _owner._llmw_models.dedupe_by_repo(base)
        self._refresh_owner_combo(items)
        if self._owner_filter:
            items = tuple(
                item
                for item in items
                if _owner._llmw_models.model_owner(item).casefold() == self._owner_filter.casefold()
            )
        items = _owner._llmw_models.filter_by_name(items, self._name_filter)
        hidden = 0
        if self._valid_only:
            before = len(items)
            items = tuple(item for item in items if _owner._llmw_models.is_usable_item(item, self._usable_repos))
            hidden = before - len(items)
        items = _owner._llmw_models.sort_browse_items(items, self._installed_repos, self._filter_key, self._sort_key)
        same_name_counts: dict[str, int] = {}
        if self._group_same:
            grouped = _owner._llmw_models.group_same_name(items, self._installed_repos)
            same_name_counts = {item.repo.casefold(): count for item, count in grouped}
            items = tuple(item for item, _count in grouped)
        selected = next(
            (item.repo for item in items if item.repo == self.current_repo),
            items[0].repo if items else None,
        )
        self._result_items = items
        self.detach_row_detail("models")
        self._detail_generation += 1
        self.current_repo = None
        self.current_info = None
        self.models.clear()
        for item in items:
            list_item = _owner.QListWidgetItem(self.models)
            list_item.setFlags(_owner.Qt.ItemFlag.ItemIsEnabled | _owner.Qt.ItemFlag.ItemIsSelectable)
            key = item.repo.casefold()
            installed = key in self._installed_repos
            partial = self._partial_models.get(key)
            incomplete = partial is not None
            progress_text = ""
            if partial is not None:
                total = (self.state.settings.download_totals or {}).get(key)
                if not total and item.storage_size > partial.size:
                    total = item.storage_size
                if total and total > 0:
                    percent = min(99, max(0, round(partial.size * 100 / total)))
                    progress_text = f"已下载 {percent}%"
                else:
                    progress_text = f"已下载 {_owner.human_size(partial.size)}"
            convert_info = self._convertible.get(key)
            quantizable = key in self._quant_candidates
            quantized = self._quantized_files.get(key, ())
            paint_path = self._paint_models.get(key)
            default_quant = self._default_quant(key)
            serve_path = next((path for label, path in quantized if label == default_quant), None)
            if serve_path is None and quantized:
                serve_path = quantized[0][1]
            # 已转换但还没量化时，转换产物（f16 GGUF）本身就能被 llama-server 加载。
            converted_gguf = convert_info[1] if convert_info is not None and convert_info[2] else None
            if serve_path is None:
                serve_path = converted_gguf
            repo_parts = item.repo.split("/")
            downloadable = len(repo_parts) == 2 and all(repo_parts)
            row = _owner._ModelItemRow(
                item,
                self.models,
                installed=installed and not incomplete,
                convertible=convert_info is not None,
                converted=bool(convert_info and convert_info[2]),
                convert_blocked=self._convert_blocked.get(key, ""),
                quant_tags=self._quant_tags.get(key, ()),
                quantizable=quantizable,
                quant_blocked=installed and not quantizable,
                startable=bool(quantized) or paint_path is not None or converted_gguf is not None,
                paint_startable=paint_path is not None,
                default_quant=default_quant,
                serve_path=serve_path,
                incomplete=incomplete,
                progress_text=progress_text,
                downloadable=downloadable,
                owner=_owner._llmw_models.model_owner(item),
                size_text=_owner.human_size(self._installed_models[key].storage_size)
                if installed and key in self._installed_models and self._installed_models[key].storage_size
                else None,
                same_name=same_name_counts.get(key, 1),
            )
            row.toggleRequested.connect(self._on_row_toggle)
            row.downloadRequested.connect(self.start_download)
            row.convertRequested.connect(self.start_convert)
            row.quantizeRequested.connect(self._on_quantize_requested)
            row.startRequested.connect(self._on_quick_serve)
            list_item.setSizeHint(_owner.QSize(0, row.sizeHint().height()))
            self.models.setItemWidget(list_item, row)
        self.models.sync_row_widths()
        self._sync_download_buttons()
        local_view = self._source_filter == "installed"
        pages = 1 if local_view else max(1, _owner.math.ceil(result.total / result.page_size)) if result.total else 1
        matched = f" · 名称筛选 {len(items)} 条" if self._name_filter else ""
        if hidden:
            matched += f" · 已隐藏无效 {hidden} 个"
        if hidden:
            matched += f" · 已隐藏无效 {hidden} 个"
        source_label = self.source_combo.currentText()
        total_text = f"已下载 {len(items)} 个" if local_view else f"{result.page}/{pages} · 共 {result.total:,} 条"
        self.status(f"{filter_label} · {source_label} · {total_text}{matched}")
        self.prev_button.setEnabled(not local_view and result.page > 1)
        self.next_button.setEnabled(not local_view and result.page < pages)
        if selected is not None:
            for row, item in enumerate(items):
                if item.repo == selected:
                    self.models.setCurrentRow(row)
                    break
        if not items:
            self._log("当前页没有匹配的模型，可切换筛选或翻页")

    def _on_model_selected(self, current: int) -> None:
        """选中行变化：只重抛光旧 / 新两行（几百行时逐行抛光会明显卡顿）。"""
        self.models.sync_selection(current)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        row = self.models.itemWidget(item)
        if isinstance(row, _owner._ModelItemRow):
            self._on_row_toggle(row)

    def _load_repo_files(self, repo: str) -> list[ModelFile]:
        """读取仓库文件清单：先按设置里的下载源，失败再换另一个源。"""
        source = _owner._llmw_models.settings_from_dict(self.state.settings.download).source
        errors: list[str] = []
        candidates = ("modelscope", "hf-mirror") if source == "auto" else (source, "modelscope", "hf-mirror")
        for candidate in dict.fromkeys(candidates):
            try:
                return _owner.fetch_files(repo, candidate)
            except _owner.ModelToolError as exc:
                errors.append(f"{candidate}: {exc}")
        raise _owner.ModelToolError("；".join(errors[-2:]) or "两个源都不可用")

    def _download_selected_files(self, repo: str, files: tuple[ModelFile, ...]) -> None:
        """按文件列表里选中的文件下载（VAE / 文本编码器等非量化权重）。"""
        self._log(f"按文件列表下载 {repo} 的 {len(files)} 个文件")
        self.start_download(repo, files=files)

    @staticmethod
    def _repo_local_dir(repo: str) -> Path:
        """仓库在本地模型目录里的落盘位置：优先已存在的目录，否则主目录。"""
        roots = _owner._llmconfig.models_dirs()
        for root in roots:
            candidate = root / repo
            if candidate.is_dir():
                return candidate
        return (roots[0] if roots else _owner._llmconfig.models_dir()) / repo

    def _on_detail_height_changed(self) -> None:
        """详情页签内容变化（切页签 / 文件清单加载完）后重算展开行高度。"""
        self.refresh_expanded_height("models")

    def _on_row_toggle(self, row: _owner._ModelItemRow) -> None:
        """展开一行并在行下方挂详情；同一时间只展开一行。"""
        self.toggle_row("models", row)

    def render_row_detail(self, row: ItemRow) -> None:
        """展开时按需拉取该仓库的详情（仅本地模型跳过网络请求）。"""
        if isinstance(row, _owner._ModelItemRow) and self.current_repo != row.repo:
            self.load_repo(row.repo, online=row.downloadable)

    def _expand_repo(self, repo: str) -> None:
        for index in range(self.models.count()):
            row = self.models.itemWidget(self.models.item(index))
            if isinstance(row, _owner._ModelItemRow) and row.repo == repo:
                if row.is_expanded():
                    self.focus_row("models", row)
                else:
                    self.toggle_row("models", row)
                return

    def load_repo(self, repo: str, online: bool = True) -> None:
        """加载仓库详情元数据；世代号防止旧请求覆盖新选择。

        ``online=False``（仅本地模型）时不发任何网络请求，详情只标出仅本地。
        """
        self._detail_generation += 1
        generation = self._detail_generation
        self.current_repo = repo
        self.current_info = None
        self.detail_view.reset(repo, online=online)
        self.refresh_expanded_height("models")
        if not online:
            self._log(f"{repo} 是仅本地模型，跳过在线详情与文件清单")
            return
        _owner.submit(
            self._detail_work,
            repo,
            on_done=lambda info: self._render_detail(generation, info),
            on_error=lambda message: self._detail_error(generation, repo, message),
        )

    @staticmethod
    def _detail_work(repo: str) -> ModelInfo:
        return _owner.fetch_model_info(repo)

    def _detail_error(self, generation: int, repo: str, message: str) -> None:
        if generation != self._detail_generation:
            return
        self.detail_view.set_error(f"详情加载失败：{message}")
        self.refresh_expanded_height("models")
        self._log(f"详情加载失败 {repo}: {message}")

    def _render_detail(self, generation: int, info: ModelInfo) -> None:
        if generation != self._detail_generation:
            return
        self.current_info = info
        self.detail_view.render(info)
        self.refresh_expanded_height("models")
        self._log(f"已加载模型详情: {info.summary.repo}")

    def _log(self, message: str) -> None:
        self.state.log(message)
