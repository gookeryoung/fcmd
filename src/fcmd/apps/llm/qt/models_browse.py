"""llmw 模型页方法（ADR 0046 P4）。"""

from __future__ import annotations

import fcmd.apps.llm._llmw_qt_center as _owner

from ._compat import (
    ModelSummary,
)


class ModelsBrowseMixin:
    """ADR 0046 P4 迁移的模型页方法。"""

    def _on_filter_changed(self, key: str) -> None:
        """切换浏览筛选：分类指标走服务端关键字，适合本机在本地按容量过滤。"""
        self._filter_key = key
        self._filter_query = next((query for name, _label, query in _owner._MODEL_FILTERS if name == key), "")
        self._persist_view_options()
        self.start_search(1)

    def _on_name_filter_changed(self, _text: str) -> None:
        """名称筛选输入防抖：停止输入 250ms 后才重建列表。"""
        self._filter_timer.start()

    def _apply_name_filter(self) -> None:
        text = self.name_filter.text().strip()
        if text == self._name_filter:
            return
        self._name_filter = text
        self._render_results()

    def _on_sort_changed(self, _index: int) -> None:
        key = self.sort_combo.currentData()
        if not isinstance(key, str) or key == self._sort_key:
            return
        self._sort_key = key
        self._persist_view_options()
        self.start_search(1)

    def _on_source_changed(self, _index: int) -> None:
        key = self.source_combo.currentData()
        if isinstance(key, str):
            self._source_filter = key
            self._persist_view_options()
            self._render_results()

    def _on_owner_changed(self, _index: int) -> None:
        owner = self.owner_combo.currentData()
        if isinstance(owner, str):
            self._owner_filter = owner
            self._persist_view_options()
            self._render_results()

    def _refresh_owner_combo(self, items: tuple[ModelSummary, ...]) -> None:
        current = self._owner_filter
        owners = sorted(
            {_owner._llmw_models.model_owner(item) for item in items if _owner._llmw_models.model_owner(item)},
            key=str.casefold,
        )
        self.owner_combo.blockSignals(True)
        self.owner_combo.clear()
        self.owner_combo.addItem("全部发布方", "")
        for owner in owners:
            self.owner_combo.addItem(owner, owner)
        index = self.owner_combo.findData(current)
        if index < 0:
            self._owner_filter = ""
            index = 0
        self.owner_combo.setCurrentIndex(index)
        self.owner_combo.blockSignals(False)

    def _on_group_toggled(self, checked: bool) -> None:
        self._group_same = checked
        self._persist_view_options()
        self._render_results()

    def _on_valid_toggled(self, checked: bool) -> None:
        self._valid_only = checked
        self._persist_view_options()
        self._render_results()

    def _persist_view_options(self) -> None:
        """把列表视图选项写回 llmw.json，避免每次启动重新选择。"""
        self.state.settings.model_view = {
            "filter": self._filter_key,
            "sort": self._sort_key,
            "source": self._source_filter,
            "owner": self._owner_filter,
            "group_same": self._group_same,
            "valid_only": self._valid_only,
        }
        self.state.persist()

    def _open_model_manager(self) -> None:
        """打开模型管理对话框：扫描已下载 / 未完成模型并批量处理。"""
        from fcmd.apps.llm._llmw_qt_model_manager import ModelManagerDialog

        dialog = ModelManagerDialog(
            _owner._llmconfig.models_dirs(),
            on_changed=self._model_manager_changed,
            on_clear_cache=self._cleanup_list_cache,
            parent=self,
        )
        dialog.exec()

    def _model_manager_changed(self) -> None:
        self.refresh_installed()
        self._render_results()

    def _cleanup_list_cache(self) -> None:
        self._list_cache.clear()
        self._log("已清理模型列表缓存")
        self.status("模型列表缓存已清理")

    def delete_model(self, repo: str) -> None:
        """删除本地模型目录；只允许模型根目录内的安全路径。"""
        if self._jobs or self._converting or self._quantizing or self._listing_repo is not None:
            self.status("任务进行中，暂不能删除模型")
            return
        models_root = _owner._llmconfig.models_dir()
        target = models_root.joinpath(*repo.split("/"))
        answer = _owner.QMessageBox.question(
            self,
            "删除本地模型",
            f"将删除本地模型目录（不可恢复）：\n{target}\n\n如果服务正在使用该模型，请先停止服务。确定继续？",
        )
        if answer != _owner.QMessageBox.StandardButton.Yes:
            return
        try:
            _owner.delete_local_model(models_root, repo)
        except _owner.ModelToolError as exc:
            self._log(f"删除模型失败 {repo}: {exc}")
            self.status(f"删除失败：{exc}")
            _owner.QMessageBox.critical(self, "删除失败", str(exc))
            return
        key = repo.casefold()
        for mapping in (
            self._installed_models,
            self._partial_models,
            self._convertible,
            self._convert_blocked,
            self._quant_candidates,
            self._quant_tags,
            self._quantized_files,
        ):
            mapping.pop(key, None)
        self._installed_repos.discard(key)
        choices = dict(self.state.settings.quant_choice or {})
        choices.pop(key, None)
        self.state.settings.quant_choice = choices or None
        last_model = self.state.settings.last_model
        if last_model and _owner.Path(last_model).is_relative_to(target):
            self.state.settings.last_model = None
        self.state.persist()
        self._log(f"已删除本地模型 {repo} -> {target}")
        self.status(f"已删除本地模型 {repo}")
        self.refresh_installed()
        self._render_results()

    def _begin_loading(self, key: str, text: str) -> None:
        """登记一项加载任务；同一时刻只显示一个加载条，任务结束才收起。"""
        self.state.begin_loading(key, text)

    def _end_loading(self, key: str) -> None:
        self.state.end_loading(key)

    def _default_quant(self, repo_key: str) -> str:
        """快捷启动默认精度：持久化的上次启动精度 → 上次运行模型 → 首个量化文件。"""
        files = self._quantized_files.get(repo_key, ())
        if not files:
            return ""
        labels = [label for label, _path in files]
        saved = (self.state.settings.quant_choice or {}).get(repo_key)
        if saved in labels:
            return saved
        last = self.state.settings.last_model
        if last:
            last_key = _owner._llmconfig.path_key(_owner.Path(last))
            for label, path in files:
                if _owner._llmconfig.path_key(path) == last_key:
                    return label
        return labels[0]

    def _local_capacity(self) -> int:
        """本机可承载的整仓体积（显存与系统内存取较大者，未知时为 0）。"""
        hardware = getattr(self.state, "hardware", None)
        gpu_mib = getattr(self.state, "gpu_mib", None) or (hardware.gpu_mib if hardware is not None else None)
        memory = hardware.memory_total if hardware is not None else 0
        gpu_bytes = (gpu_mib or 0) * 1024**2
        memory_bytes = int(memory * 0.8) if memory else 0
        return max(gpu_bytes, memory_bytes)

    def _installed_name_keys(self) -> set[str]:
        """已安装模型的名称（模型名 + 目录名），用于「适合本机」时保留同名官方条目。"""
        keys: set[str] = set()
        for key, summary in self._installed_models.items():
            if summary.name:
                keys.add(summary.name.casefold())
            keys.add(key.rsplit("/", 1)[-1])
        keys.discard("")
        return keys

    def manual_update(self) -> None:
        """手动更新：强制刷新当前页并重新扫描本地模型。"""
        if self._searching:
            self.status("模型列表正在更新，请稍候…")
            return
        self.status("正在更新模型列表…")
        self.refresh_installed()
        self.start_search(self._page, force=True)

    def _on_import_clicked(self) -> None:
        menu = _owner.QMenu(self)
        file_action = menu.addAction("导入 GGUF 文件…")
        file_action.triggered.connect(self._pick_gguf_files)
        dir_action = menu.addAction("导入模型文件夹…")
        dir_action.triggered.connect(self._pick_model_folder)
        menu.exec(_owner.QCursor.pos())

    def _pick_gguf_files(self) -> None:
        paths, _selected = _owner.QFileDialog.getOpenFileNames(
            self,
            "导入 GGUF 模型",
            "",
            "GGUF 模型 (*.gguf);;所有文件 (*.*)",
        )
        if paths:
            self.start_import([_owner.Path(path) for path in paths])
