"""wslman 共享控件 / 段落（ADR 0046 P2/P3 迁移）。"""

from __future__ import annotations

import fcmd.apps.wslman._wslmanw_qt as _owner

from ._compat import Distro, ImageEntry, ItemRow, Path, QPushButton, QWidget


class _DistroItemRow(ItemRow):  # pragma: no cover - Qt 控件
    """实例行卡：共享 :class:`ItemRow` 骨架 + 实例自己的状态位。

    标题是实例名，副标题行是「版本 + 对应的在线镜像」；状态胶囊与默认胶囊
    贴在同一行行尾，右侧是磁盘占用、行内启停按钮与展开箭头。详情（BasePath /
    虚拟磁盘 / 占用 / 默认用户 / 来源）由 ``_DistroDetailView`` 挂到
    ``detail_layout`` 就地显开，同一时间只展开一行。
    """

    startStopRequested = _owner.Signal(str)
    configRequested = _owner.Signal(str)

    def __init__(
        self,
        distro: Distro,
        size: int | None,
        estimated: bool,
        work_dir: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(distro.name, "", parent=parent)
        self.distro = distro
        self.size = size
        self.estimated = estimated
        self.work_dir = work_dir
        self.backed_up = False
        self.state_tag = _owner.StatusTag("", tone="muted", parent=self.header)
        self.add_tag(self.state_tag)
        self.repair_tag = _owner.StatusTag("需修复", tone="warning", parent=self.header)
        self.repair_tag.setToolTip("注册表 BasePath 下找不到磁盘文件；先点「修复注册」再启动")
        self.add_tag(self.repair_tag)
        self.backup_tag = _owner.StatusTag("已备份", tone="accent", parent=self.header)
        self.backup_tag.setToolTip("备份目录里有该实例的导出包（<实例名>.tar / .tar.gz）；可在实例页「备份」")
        self.backup_tag.setVisible(False)
        self.add_tag(self.backup_tag)
        self.default_tag = _owner.StatusTag("默认", tone="accent", parent=self.header)
        self.add_tag(self.default_tag)
        # 发行版图标：按实例名挑对应发行版，识别不出回落通用图标（不留空、不显示问号）。
        # 与动作按钮同一套 ``icons`` 线图标，尺寸 / 色调跟随主题。
        self.icon_label = _owner.QLabel(self.header)
        self.icon_label.setObjectName("DistroIcon")
        self.icon_label.setToolTip(distro.name)
        self.icon_label.setAttribute(_owner.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.title_row.insertWidget(0, self.icon_label)
        _owner._qt_icons.set_icon(self.icon_label, _owner._qt_icons.distro_icon(distro.name), tone="accent")
        # 对应的在线镜像（自定义导入 / 匹配不到时给「本地导入」）放在副标题行，
        # 跟版本同一排：状态胶囊也在副标题行尾，两行各自独立，窄窗口或大字体现
        # 也不会和「镜像 …」挤在一起。超长时右侧省略，完整文案走 tooltip。
        self.image_label = _owner.ElidedLabel("", mode=_owner.Qt.TextElideMode.ElideRight, parent=self.header)
        self.image_label.setObjectName("Hint")
        self.image_label.setVisible(False)
        # 吃掉版本与状态胶囊之间的剩余宽度，长镜像名右侧省略，不会挤到状态胶囊。
        self.add_note_widget(self.image_label, stretch=1)

        # 启停是动态动作：待机是主按钮（启动），运行中是红色危险按钮（停止）。
        # 行卡只显示图标，中文动作名称放 tooltip，避免右侧动作区被文字撑宽。
        self.start_stop_button = _owner.button("", kind="primary", icon="run")
        self.start_stop_button.clicked.connect(lambda: self.startStopRequested.emit(distro.name))
        self.add_action(self.start_stop_button)
        # 配置放在启停右侧：设为默认、终端默认目录等实例级设置不强制用户先选中再跑工具栏。
        self.config_button = _owner.button(
            "",
            kind="ghost",
            icon="settings",
            tooltip=f"配置 {distro.name}：默认发行版、终端目录、初始化环境与 SSH 迁移",
        )
        self.config_button.clicked.connect(lambda: self.configRequested.emit(distro.name))
        self.add_action(self.config_button)
        self._busy = False

        self.update_distro(distro, size, estimated)

    def _work_dir_text(self) -> str:
        """副标题里的工作目录：显示目录名；不属于任何已配置目录时如实说明。"""
        if self.work_dir is None:
            return "未在已配置目录"
        return self.work_dir.name or str(self.work_dir)

    def _row_tooltip(self) -> str:
        """完整磁盘位置与所属工作目录；副标题空间有限，完整路径放 tooltip。"""
        place = self.distro.base_path or "未知"
        lines = [self.distro.name, f"注册表磁盘位置：{place}"]
        if self.distro.needs_repair:
            lines.append("状态：需修复注册（当前 BasePath 下找不到磁盘文件，实例无法启动）")
            if self.distro.repair_vhd:
                lines.append(f"可用磁盘：{self.distro.repair_vhd}")
        if self.work_dir is None:
            lines.append("工作目录：未在已配置目录")
        else:
            lines.append(f"工作目录：{self.work_dir}")
        if self.backed_up:
            lines.append("备份：备份目录里有该实例的导出包")
        return "\n".join(lines)

    def set_backed_up(self, backed_up: bool) -> None:
        """就地刷新「已备份」胶囊（轻量状态轮询不重建行）。"""
        self.backed_up = backed_up
        self.backup_tag.setVisible(backed_up)
        self.setToolTip(self._row_tooltip())

    def update_distro(self, distro: Distro, size: int | None, estimated: bool) -> None:
        """就地刷新状态 / 占用 / 默认标记与启停按钮（轮询不重建行）。"""
        self.distro = distro
        self.size = size
        self.estimated = estimated
        self.setToolTip(self._row_tooltip())
        self.set_title(distro.name)
        # 与 llmw 模型行同构：左侧副标题只放简短口径（版本 + 所属工作目录）；
        # 右侧数值放磁盘占用。所属目录按实例实际 BasePath 归属，找不到就如实说明。
        self.set_subtitle(f"v{distro.version or '未知'} · {self._work_dir_text()}")
        self.set_metrics(_owner.format_bytes(size, estimated=estimated))
        self.state_tag.set_status(_owner.state_label(distro), _owner._state_tone(distro.state))
        self.repair_tag.setVisible(distro.needs_repair)
        self.backup_tag.setVisible(self.backed_up)
        self.default_tag.setVisible(distro.default)
        self._apply_action_state()

    def update_image(self, entry: ImageEntry | None, *, known: bool) -> None:
        """标注该实例对应的在线镜像；``known=False`` 表示清单还没加载（留空）。"""
        text = _owner.w.distro_image_text(self.distro, (entry,) if entry is not None else (), known=known)
        self.image_label.setText(text)
        self.image_label.setVisible(bool(text))
        if entry is None:
            self.image_label.setToolTip("未匹配到在线镜像（自定义导入或已下架）" if known else "")
            return
        arches = "/".join(entry.arches) or "任意架构"
        sources = _owner.w.image_source_text(entry)
        self.image_label.setToolTip(f"{entry.friendly_name or entry.name}\n可用架构：{arches}\n来源：{sources}")

    def _apply_action_state(self) -> None:
        """启停按钮的图标 / 变体 / 色调与 tooltip：运行中给红色的「停止」，否则「启动」。

        禁用态（任务进行中 / 状态不可操作）统一用 ``muted`` 图标色调：主按钮与危险
        按钮的禁用底色都会变浅，保留 ``on_accent`` 的白勾或 ``danger`` 的红标在白底上
        会看不清。行内按钮不显示文字，动作名在 tooltip。
        """
        can_start, can_stop = _owner.w.action_states(self.distro.state)
        if self.distro.needs_repair:
            can_start = can_stop = False
        enabled = (can_start or can_stop) and not self._busy
        if can_stop:
            self.start_stop_button.set_kind("danger")
            tone = "danger" if enabled else "muted"
            text = "停止"
            icon = "stop"
        else:
            self.start_stop_button.set_kind("primary")
            tone = "on_accent" if enabled else "muted"
            text = "启动"
            icon = "run"
        self.start_stop_button.set_icon_name(icon, tone=tone)
        self.start_stop_button.setEnabled(enabled)
        tip = "需先修复注册" if self.distro.needs_repair else f"{text} {self.distro.name}"
        self.start_stop_button.setToolTip(tip)
        self.config_button.setEnabled(not self._busy)

    def set_busy(self, busy: bool) -> None:
        """任务进行中禁用行内启停 / 配置按钮；结束后按当前状态重新计算。"""
        self._busy = busy
        self._apply_action_state()


class _DistroDetailView(QWidget):  # pragma: no cover - Qt 控件
    """展开行里的实例详情：字段网格 + 长路径可选中复制。

    同一个实例在行之间复用：展开哪一行就挂到哪一行的 ``detail_layout`` 下。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = _owner.QVBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 10)
        layout.setSpacing(6)
        separator = _owner.QFrame(self)
        separator.setFrameShape(_owner.QFrame.Shape.HLine)
        layout.addWidget(separator)

        grid = _owner.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.values: dict[str, _owner.QLabel] = {}
        for index, label in enumerate(_owner.w._DETAIL_LABELS):
            row, column = divmod(index, 2)
            grid.addWidget(
                _owner.hint_label(f"{label}：", wrap=False), row, column * 2, _owner.Qt.AlignmentFlag.AlignTop
            )
            value = _owner.QLabel("—", self)
            value.setWordWrap(True)
            value.setTextInteractionFlags(_owner.Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(value, row, column * 2 + 1, _owner.Qt.AlignmentFlag.AlignTop)
            self.values[label] = value
        layout.addLayout(grid)

    def render(self, distro: Distro, size: int | None, estimated: bool) -> None:
        """回填字段；未知字段统一显示 ``—``。"""
        fields = dict(_owner.w.detail_fields(distro, size, estimated))
        for label, widget in self.values.items():
            text = fields.get(label, "—")
            widget.setText(text)
            widget.setToolTip(text)


class _ImageItemRow(ItemRow):  # pragma: no cover - Qt 控件
    """镜像列表行卡：共享 :class:`ItemRow` 骨架 + 镜像自己的动作与标记。

    标题是镜像名，副标题是友好名 / 架构 / 来源，右侧是来源摘要、已安装 / 已备份 /
    sha256 胶囊与行内下载 / 安装按钮。下载地址与短字段由 ``_ImageDetailView`` 挂到
    ``detail_layout`` 就地展开，列表铺满整页宽度。
    """

    downloadRequested = _owner.Signal(str)
    installRequested = _owner.Signal(str)
    cancelRequested = _owner.Signal()

    def __init__(
        self,
        entry: ImageEntry,
        parent: QWidget | None = None,
        *,
        mirror_text: str = "",
        mirror_tip: str = "",
    ) -> None:
        title, subtitle, metrics = _owner.w.image_item_lines(entry)
        super().__init__(
            title,
            subtitle,
            elide=_owner.Qt.TextElideMode.ElideMiddle,
            expand_hint="展开详情与下载地址",
            collapse_hint="收起详情",
            # 镜像行的状态列会出现「仅 wsl --install」这类长文案：和它在同一列的
            # 短文案也按同一宽度留位，上下行的分隔线 / 动作按钮才不会错位。
            metrics_samples=("仅 wsl --install", "已下载 100%"),
            parent=parent,
        )
        self.entry = entry
        self.setToolTip(entry.name)
        self.title_label.setToolTip(entry.name)
        self._busy = False
        self._active = False
        for text, tone in _owner.w.image_marks(entry):
            tag = _owner.StatusTag(text, tone=tone, parent=self.header)
            # 「已安装 / 已备份 / 已下载 / sha256」的含义收进 tooltip，行内只留短标签。
            hint = _owner.w.IMAGE_MARK_HINTS.get(text)
            if hint:
                tag.setToolTip(hint)
            self.add_tag(tag)
        # 可用镜像源（WSLUI 清单）：副标题后一行，窄窗口右侧省略，完整 URL 走 tooltip。
        self.mirror_label = _owner.ElidedLabel(mirror_text, mode=_owner.Qt.TextElideMode.ElideRight, parent=self.header)
        self.mirror_label.setObjectName("Hint")
        self.mirror_label.setVisible(bool(mirror_text))
        if mirror_tip:
            self.mirror_label.setToolTip(mirror_tip)
        self.add_note_widget(self.mirror_label, stretch=1)
        self.set_metrics(metrics)

        # 每行自带动作按钮：未下载 →「下载」；已下载未安装 →「安装」；已安装 → 无按钮。
        # 下载 / 安装进行中时该行按钮变成「取消」，不再依赖页面底部的动作条。
        self.action = self._action_kind(entry)
        self.download_button: QPushButton | None = None
        if self.action:
            # 「安装」不命中关键词（避免抢走「安装本地镜像」的磁盘图标），显式给下载图标。
            self.download_button = _owner.button(
                self._action_text(), kind="primary", icon="download" if self.action == "install" else None
            )
            self.download_button.setToolTip(self._action_tooltip())
            self.download_button.clicked.connect(self._on_action)
            self.add_action(self.download_button)

    @staticmethod
    def _action_kind(entry: ImageEntry) -> str:
        """``""``（无需动作）/ ``download`` / ``install``。"""
        if entry.installed:
            return ""
        if entry.downloaded:
            return "install"
        return "download" if entry.downloads else "install"

    def _action_text(self) -> str:
        return "安装" if self.action == "install" else "下载"

    def _action_tooltip(self) -> str:
        if self.action == "install":
            if self.entry.downloaded:
                return f"用已下载的镜像包安装：{self.entry.archive}" if self.entry.archive else "用已下载的镜像包安装"
            return _owner.w._DOWNLOAD_HINT
        return "下载镜像归档；完成后该行变为「已下载」，可直接安装"

    def _on_action(self) -> None:
        # 按钮在任务进行中会变成「取消」：此时必须发取消信号，而不是再发一次
        # 下载 / 安装（后者会被窗口的忙态挡下，用户会觉得点了没反应）。
        if self._busy and self._active:
            self.cancelRequested.emit()
            return
        if self.action == "install":
            self.installRequested.emit(self.entry.name)
        else:
            self.downloadRequested.emit(self.entry.name)

    def set_download_state(
        self,
        *,
        busy: bool,
        active: bool = False,
        queued: bool = False,
        enabled: bool = True,
    ) -> None:
        """按下载队列刷新按钮：有任务的显示「取消」（排队中同样可取消），其余按忙态禁用。"""
        self._busy = busy
        self._active = busy and active
        if self.download_button is None:
            return
        if self._active:
            self.download_button.setText("取消")
            if queued:
                self.download_button.setToolTip("取消排队中的这项下载")
            else:
                self.download_button.setToolTip("取消本次下载（已下载的部分保留 .part，可续传）")
            self.download_button.setEnabled(True)
            return
        self.download_button.setText(self._action_text())
        self.download_button.setToolTip(self._action_tooltip())
        self.download_button.setEnabled(enabled)


class _ImageDetailView(QWidget):  # pragma: no cover - Qt 控件
    """展开行里的镜像详情：短字段网格 + 可换行的下载地址。

    同一个实例在行之间复用：展开哪一行就挂到哪一行的 ``detail_layout`` 下。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = _owner.QVBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 10)
        layout.setSpacing(6)
        separator = _owner.QFrame(self)
        separator.setFrameShape(_owner.QFrame.Shape.HLine)
        layout.addWidget(separator)

        grid = _owner.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.values: dict[str, _owner.QLabel] = {}
        for index, label in enumerate(_owner.w._IMAGE_DETAIL_FIELDS):
            row, column = divmod(index, 2)
            grid.addWidget(
                _owner.hint_label(f"{label}：", wrap=False), row, column * 2, _owner.Qt.AlignmentFlag.AlignTop
            )
            value = _owner.QLabel("—", self)
            value.setWordWrap(True)
            value.setTextInteractionFlags(_owner.Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(value, row, column * 2 + 1, _owner.Qt.AlignmentFlag.AlignTop)
            self.values[label] = value
        layout.addLayout(grid)
        # 「已备份」是镜像行的状态胶囊之一，含义写在这里：备份目录里有同名导出包。
        layout.addWidget(
            _owner.hint_label(
                "已安装 = 本机已有同名实例；已备份 = 备份目录里有 <镜像名>.tar / .tar.gz"
                "（实例页「备份」生成，可直接安装恢复）",
                wrap=True,
            )
        )
        layout.addWidget(_owner.hint_label("下载地址", wrap=False))
        self.url = _owner.QPlainTextEdit(self)
        self.url.setReadOnly(True)
        self.url.setMinimumHeight(72)
        self.url.setLineWrapMode(_owner.QPlainTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(self.url)

    def render(self, entry: ImageEntry, sources: tuple[tuple[str, str], ...] = ()) -> None:
        """回填选中条目的详情；未知字段统一显示 ``—``。

        ``sources`` 是该条目的全部下载源 ``(名称, URL)``（WSLUI 多源 + 国内镜像 +
        官方源），只有展开时才回填到「镜像源」字段：折叠行只写数量，避免把十几个源
        铺到行卡里。
        """
        values = dict(_owner.w.image_details(entry))
        values["镜像源"] = _owner.w.mirror_lines(sources) or "—"
        for label, widget in self.values.items():
            widget.setText(values.get(label, "—"))
        self.url.setPlainText(values.get("下载地址", "—"))
