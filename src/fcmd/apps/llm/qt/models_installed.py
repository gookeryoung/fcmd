"""llmw 模型页方法（ADR 0046 P4）。"""

from __future__ import annotations

import fcmd.apps.llm._llmw_qt_center as _owner

from ._compat import (
    FolderInfo,
    ImportPlan,
    Path,
)


class ModelsInstalledMixin:
    """ADR 0046 P4 迁移的模型页方法。"""

    def refresh_installed(self) -> None:
        """后台扫描模型目录，记录已安装仓库（用于勾选标志与置顶）。

        扫描可能遍历大量 GGUF 头与转换产物，放到线程池执行；完成后一次性
        套用快照并重绘当前结果，切页 / 下载完成时界面不会被同步扫描卡住。
        扫描进行中再次请求（典型场景：刚下完一个模型）先记下标记，
        当前扫描结束后立即重扫，避免应用下载前的旧快照而停在未下载状态。
        """
        if self._scanning_installed:
            self._rescan_installed = True
            return
        self._scanning_installed = True
        self._begin_loading("installed", "正在扫描本地模型…")
        _owner.submit(
            self._scan_installed,
            on_done=self._apply_installed,
            on_error=self._installed_error,
            on_finished=self._installed_finished,
        )

    @staticmethod
    def _scan_installed() -> _owner._InstalledSnapshot:  # noqa: PLR0912 - 一次扫描归集安装 / 转换 / 量化状态
        """在线程池里扫描本地模型目录（不碰任何 Qt 控件）。"""
        try:
            models = []
            seen_repos: set[str] = set()
            for root in _owner._llmconfig.models_dirs():
                for model in _owner.scan_local_models(root):
                    if model.repo.casefold() in seen_repos:
                        continue
                    seen_repos.add(model.repo.casefold())
                    models.append(model)
        except OSError:
            return _owner._InstalledSnapshot({}, set(), {}, {}, {}, {}, {}, {}, set(), {})
        installed = {
            model.repo.casefold(): _owner.ModelSummary(
                repo=model.repo,
                name=model.repo.rsplit("/", 1)[-1],
                storage_size=model.size,
            )
            for model in models
        }
        partial = {model.repo.casefold(): model for model in models if not model.complete}
        convertible: dict[str, tuple[Path, Path, bool]] = {}
        blocked: dict[str, str] = {}
        quant_candidates: dict[str, tuple[Path, ...]] = {}
        quant_tags: dict[str, tuple[str, ...]] = {}
        quantized: dict[str, list[tuple[str, Path]]] = {}
        try:
            product_ggufs = sorted(_owner._llmconfig.products_dir().glob("*.gguf"))
        except OSError:
            product_ggufs = []
        for model in models:
            key = model.repo.casefold()
            if _owner.llmconvert.is_hf_safetensors_dir(model.path):
                output = _owner._llmw_convert.default_output_path(model.path)
                convertible[key] = (model.path, output, output.is_file())
            elif (model.path / "model_index.json").is_file():
                blocked[key] = "diffusers 多组件模型（model_index.json），当前仅支持单目录 HF transformers safetensors"
            full: list[Path] = []
            tags: list[str] = []
            # 仓库自身的 GGUF +（已转换时）converted/<名称>/ 里的转换产物：转换后的
            # f16 GGUF 与它的量化变体都落在这个目录，不扫就会「已转换但无法启动 / 量化」。
            folders = [model.path]
            convert_info = convertible.get(key)
            if convert_info is not None and convert_info[1].parent.is_dir():
                folders.append(convert_info[1].parent)
            for gguf in sorted(path for folder in folders for path in folder.glob("*.gguf")):
                precision = _owner.detect_precision(gguf, tensors=False)
                if precision.quantized is True:
                    label = precision.filename_label or precision.label.removesuffix(" 量化")
                    if label not in tags:
                        tags.append(label)
                    entries = quantized.setdefault(key, [])
                    if all(existing != label for existing, _path in entries):
                        entries.append((label, gguf))
                elif _owner.quantize_input_error(precision) is None:
                    full.append(gguf)
            bases = {_owner.llmqnt.strip_f16_suffix(path.stem) for path in full}
            for product in product_ggufs:
                if not bases or not any(product.stem.startswith(base + "-") for base in bases):
                    continue
                precision = _owner.detect_precision(product, tensors=False)
                if precision.quantized is True:
                    label = precision.filename_label or precision.label.removesuffix(" 量化")
                    if label not in tags:
                        tags.append(label)
                    entries = quantized.setdefault(key, [])
                    if all(existing != label for existing, _path in entries):
                        entries.append((label, product))
            if full:
                quant_candidates[key] = tuple(full)
            if tags:
                quant_tags[key] = tuple(tags)
        usable = {model.repo.casefold() for model in models if model.weights > 0 or model.partial > 0}
        # 扩散模型（Flux / Qwen-Image 等）不进 llama.cpp 扫描，也没有量化 GGUF；
        # 单独扫描一次扩散资产，把主模型映射回仓库，供「启动服务」走 sd.cpp。
        paint_models: dict[str, Path] = {}
        for asset in _owner._llmpaint.scan_assets(_owner._llmconfig.models_dirs()).main_models():
            for model in models:
                try:
                    asset.path.relative_to(model.path)
                except ValueError:
                    continue
                paint_models.setdefault(model.repo.casefold(), asset.path)
                break
        return _owner._InstalledSnapshot(
            installed,
            set(installed),
            partial,
            convertible,
            blocked,
            quant_candidates,
            quant_tags,
            {repo: tuple(entries) for repo, entries in quantized.items()},
            usable,
            paint_models,
        )

    def _apply_installed(self, snapshot: _owner._InstalledSnapshot) -> None:
        """套用扫描结果；若已有结果行则就地重绘已安装 / 可转换 / 可量化标志。"""
        self._installed_models = snapshot.models
        self._installed_repos = snapshot.repos
        self._usable_repos = snapshot.usable
        self._partial_models = snapshot.partial
        self._convertible = snapshot.convertible
        self._convert_blocked = snapshot.blocked
        self._quant_candidates = snapshot.quant_candidates
        self._quant_tags = snapshot.quant_tags
        self._quantized_files = snapshot.quantized
        self._paint_models = snapshot.paint_models
        pending = self._pending_convert
        self._pending_convert = None
        if pending:
            if pending.casefold() in self._convertible:
                self.start_convert(pending)
            else:
                self._log(f"导入完成，但未检测到可自动转换的 safetensors：{pending}")
        if self._last_result is not None and not self._searching:
            self._render_results()

    def _installed_error(self, message: str) -> None:
        self._log(f"扫描本地模型失败: {message}")

    def _installed_finished(self) -> None:
        self._scanning_installed = False
        self._end_loading("installed")
        if self._rescan_installed:
            # 下载完成 / 切页发生在扫描途中：旧快照不含新文件，结束后续一次。
            self._rescan_installed = False
            self.refresh_installed()

    def _pick_model_folder(self) -> None:
        path = _owner.QFileDialog.getExistingDirectory(self, "导入模型文件夹")
        if not path:
            return
        try:
            info = _owner.inspect_folder(_owner.Path(path))
        except (OSError, ValueError) as exc:
            _owner.QMessageBox.warning(self, "无法识别文件夹", str(exc))
            return
        proceed, convert = self._ask_folder_import(info)
        if proceed:
            self.start_import([_owner.Path(path)], convert=convert)

    def _ask_folder_import(self, info: FolderInfo) -> tuple[bool, bool]:
        """按文件夹类型给出不同选项，返回 ``(是否导入, 是否导入后转换)``。"""
        box = _owner.QMessageBox(self)
        box.setWindowTitle("导入模型文件夹")
        healthy = info.kind in (_owner.FolderKind.TENSORS, _owner.FolderKind.MIXED, _owner.FolderKind.GGUF)
        box.setIcon(_owner.QMessageBox.Icon.Information if healthy else _owner.QMessageBox.Icon.Warning)
        box.setText(f"检测到：{info.label}")
        box.setInformativeText(info.hint)
        convert_button = None
        if info.can_convert:
            convert_button = box.addButton("导入并转换", _owner.QMessageBox.ButtonRole.AcceptRole)
            import_button = box.addButton("仅导入", _owner.QMessageBox.ButtonRole.ActionRole)
        elif info.kind is _owner.FolderKind.GGUF:
            import_button = box.addButton("导入", _owner.QMessageBox.ButtonRole.AcceptRole)
        else:
            import_button = box.addButton("仍然导入", _owner.QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", _owner.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(import_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is convert_button:
            return True, True
        if clicked is import_button:
            return True, False
        return False, False

    def start_import(self, sources: list[Path], *, convert: bool = False) -> None:
        """把选中的 GGUF / 文件夹复制进模型目录，完成后刷新本地标识。"""
        try:
            plans = _owner.plan_import(sources, _owner._llmconfig.models_dir())
        except (OSError, ValueError) as exc:
            self.status(f"无法导入：{exc}")
            _owner.QMessageBox.warning(self, "无法导入模型", str(exc))
            return
        if not plans:
            return
        conflicts = [plan for plan in plans if plan.target.exists()]
        if conflicts and not self._confirm_import_overwrite(conflicts):
            return
        self._import_convert = convert
        self._begin_loading("import", f"正在导入 {len(plans)} 项模型…")
        self.status(f"正在导入 {len(plans)} 项模型…")
        self._log("开始导入：" + "、".join(str(plan.source) for plan in plans))
        _owner.submit(
            self._import_work,
            plans,
            on_done=self._import_done,
            on_error=self._import_error,
            on_finished=self._import_finished,
        )

    def _confirm_import_overwrite(self, conflicts: list[ImportPlan]) -> bool:
        names = "、".join(plan.target.name for plan in conflicts[:5])
        more = f" 等 {len(conflicts)} 项" if len(conflicts) > 5 else ""
        answer = _owner.QMessageBox.question(
            self,
            "目标已存在",
            f"模型目录中已存在：{names}{more}\n继续将覆盖同名文件并合并目录，确定导入？",
        )
        return answer == _owner.QMessageBox.StandardButton.Yes

    @staticmethod
    def _import_work(plans: tuple[ImportPlan, ...]) -> tuple[str, ...]:
        """后台线程：逐项复制，返回导入后的目标路径。"""
        copied: list[str] = []
        for plan in plans:
            copied.append(str(_owner.copy_import(plan, overwrite=True)))
        return tuple(copied)

    def _import_done(self, targets: tuple[str, ...]) -> None:
        convert = self._import_convert
        self._import_convert = False
        if convert and len(targets) == 1:
            self._pending_convert = _owner.Path(targets[0]).name
        self.status(f"已导入 {len(targets)} 项模型，可继续转换或启动")
        self._log("已导入模型：" + "、".join(targets))

    def _import_error(self, message: str) -> None:
        self.status(f"导入失败：{message}")
        self._log(f"导入失败: {message}")
        _owner.QMessageBox.warning(self, "导入失败", message)

    def _import_finished(self) -> None:
        self._end_loading("import")
        self.refresh_installed()
        self.state.refresh_models()
