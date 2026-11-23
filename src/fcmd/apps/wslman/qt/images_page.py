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


class ImagesPageMixin:
    """ADR 0046 P2/P3 迁移的窗口方法。"""

    def _open_images_page(self) -> None:
        """镜像页入口：磁盘缓存秒开（有就画），随后按需后台更新。

        与 llmw 模型列表一致（陈旧回显 + 后台刷新）：断网 / 首次拉取较慢时
        用户先看到上一次的清单与更新时间，而不是空白列表；缓存仍在 30 分钟
        新鲜期内则直接当结果用，要强制刷新点「更新列表」。
        """
        if not self.image_entries:
            cache = self._restore_images_cache()
            self._images_cache_fresh = cache is not None and (
                _owner.time.time() - cache.fetched_at <= _owner._images.CACHE_TTL_SECONDS
            )
        if self.image_entries and self._images_cache_fresh:
            # 新鲜缓存直接当结果用：不重复请求，要强制刷新点「更新列表」。
            self._images_from_cache = False
            return
        self._load_images()

    def _build_images_page(self) -> QWidget:
        """镜像页：筛选条（含手动更新）+ 搜索行 + 单行状态 + 行卡列表 + 下载动作。

        布局与 llmw 模型页同构：固定行都是单行控件（不换行、不抢高度），列表吃满
        剩余空间并自带滚动，底部动作行只放下载相关控件。这样窗口压到最小高度时
        也不会出现「标签被压扁、底部动作行与列表重叠」。
        """
        page = _owner.PageBody(self, spacing=8)
        body = page.body

        filters = _owner.QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(6)
        self.image_filter_group = _owner.QButtonGroup(self)
        self.image_filter_group.setExclusive(True)
        self.image_filter_buttons: dict[str, QPushButton] = {}
        for key, label in _owner.w.IMAGE_FILTERS:
            chip = _owner.button(label)
            chip.setObjectName("FilterChip")
            chip.setCheckable(True)
            chip.setCursor(_owner.Qt.CursorShape.PointingHandCursor)
            # ``_checked`` 必须有默认值：PySide 会按「可零参调用」挑 ``clicked()`` 重载。
            chip.clicked.connect(lambda _checked=False, key=key: self._on_image_filter_changed(key))
            self.image_filter_group.addButton(chip)
            filters.addWidget(chip)
            self.image_filter_buttons[key] = chip
        filters.addStretch(1)
        self.image_install_local = _owner.button("安装镜像…", kind="ghost")
        self.image_install_local.setToolTip(f"安装磁盘上已有的镜像包；{_owner.w._DOWNLOAD_HINT}")
        self.image_install_local.clicked.connect(self._install_local)
        filters.addWidget(self.image_install_local)
        self._buttons.append(self.image_install_local)
        self._button_by_text["安装镜像…"] = self.image_install_local
        self.image_manage = _owner.button("管理镜像…", kind="ghost")
        self.image_manage.setToolTip("管理下载目录里的镜像包：导出、删除、清理缓存与未完成下载")
        self.image_manage.clicked.connect(self._open_image_manager)
        filters.addWidget(self.image_manage)
        self._buttons.append(self.image_manage)
        self._button_by_text["管理镜像…"] = self.image_manage
        # 手动更新（对齐 llmw「更新模型」）：绕过 30 分钟缓存重新拉两个在线源。
        self.images_refresh = _owner.button("更新列表", kind="ghost")
        self.images_refresh.setToolTip("重新获取 wsl --list --online 与官方镜像清单（绕过 30 分钟缓存）")
        self.images_refresh.clicked.connect(self._manual_update_images)
        filters.addWidget(self.images_refresh)
        body.addLayout(filters)

        search_row = _owner.QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(6)
        search_row.addWidget(_owner.QLabel("搜索"))
        self.image_search = _owner.QLineEdit(self)
        self.image_search.setClearButtonEnabled(True)
        self.image_search.setPlaceholderText("按名称 / 友好名过滤…")
        self.image_search.setMinimumWidth(220)
        self.image_search.textChanged.connect(self._schedule_image_filter)
        search_row.addWidget(self.image_search, 1)
        self.image_sort = _owner.QComboBox(self)
        for key, label in _owner.w.IMAGE_SORTS:
            self.image_sort.addItem(label, key)
        self.image_sort.setCurrentIndex(max(self.image_sort.findData(self.settings.image_sort), 0))
        self.image_sort.currentIndexChanged.connect(self._on_image_sort_changed)
        search_row.addWidget(self.image_sort)
        self.image_source = _owner.QComboBox(self)
        for key, label in _owner.w.IMAGE_SOURCES:
            self.image_source.addItem(label, key)
        self.image_source.setCurrentIndex(max(self.image_source.findData(self.settings.image_source), 0))
        self.image_source.currentIndexChanged.connect(self._on_image_source_changed)
        search_row.addWidget(self.image_source)
        search_row.addStretch(0)
        body.addLayout(search_row)

        # 状态行固定单行（中间省略 + tooltip）：换行标签在矮窗口里会被压扁，
        # 文字叠到下一行控件上，是「底部重叠」的直接来源。
        self.images_status = _owner.ElidedLabel("打开本页时先显示本地缓存，再自动更新清单", parent=page)
        self.images_status.setObjectName("Hint")
        # 垂直策略固定：状态行永远只占一行高度，不参与窗口变高时的剩余空间分配。
        self.images_status.setSizePolicy(_owner.QSizePolicy.Policy.Ignored, _owner.QSizePolicy.Policy.Fixed)
        body.addWidget(self.images_status)

        # 列表铺满整页：详情通过行内展开显示（与 llmw 模型列表同构），不自带横向滚动。
        self.image_list = _owner.RowListWidget(page)
        self.image_list.setObjectName("ImageResultList")
        self.image_list.setSpacing(4)
        self.image_list.setMinimumHeight(200)
        self.image_list.setSelectionMode(_owner.RowListWidget.SelectionMode.SingleSelection)
        self.image_list.setHorizontalScrollBarPolicy(_owner.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.image_list.currentRowChanged.connect(self._on_image_selected)
        self.image_list.itemDoubleClicked.connect(self._on_image_double_clicked)
        self.image_detail_view = _owner._ImageDetailView(page)
        self.image_detail_view.setVisible(False)
        self.bind_row_detail("images", self.image_list, self.image_detail_view)
        body.addWidget(self.image_list, 1)

        self._filter_timer = _owner.QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._render_images)
        self.image_filter_buttons[self._image_filter_key].setChecked(True)

        return page
