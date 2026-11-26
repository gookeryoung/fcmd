"""Qt 常用控件包：卡片、折叠区、表单行、指标网格、日志区、表格与路径选择行。

这些控件是各窗口的「展示积木」：只做布局与最小交互，不含业务逻辑。
需要业务判断的窗口把自己的回调接到信号 / 按钮上，便于单测纯逻辑。

按职责拆成子模块，``from fcmd.gui.widgets import Card`` 仍可一次拿到全部公开控件：

- :mod:`fcmd.gui.widgets.text`：说明 / 数值 / 状态标签、等宽字体、分节标题与省略标签；
- :mod:`fcmd.gui.widgets.tables`：只读表格与只读单元格；
- :mod:`fcmd.gui.widgets.buttons`：按钮、图标按钮、胶囊开关与确认框；按钮共用
  ``ButtonStateMixin``（``set_text`` / ``set_icon_name`` / ``set_kind`` / ``set_style_name``）；
- :mod:`fcmd.gui.widgets.tabs`：药丸页签与数量徽标；
- :mod:`fcmd.gui.widgets.rows`：可展开行卡、行列表与展开态 mixin；
- :mod:`fcmd.gui.widgets.cards`：卡片、滚动页、折叠区、互斥选项卡片；
- :mod:`fcmd.gui.widgets.forms`：表单网格与路径输入行；
- :mod:`fcmd.gui.widgets.layouts`：动作条用的流式布局（宽度不足自动换行）；
- :mod:`fcmd.gui.widgets.metrics`：指标网格、分段负载条、加载条与进度行；
- :mod:`fcmd.gui.widgets.log`：只读日志区 + 工具条；
- :mod:`fcmd.gui.widgets.storage`：通用磁盘 / 目录卡片网格（Steam 式多根目录管理）
  与左右成对目录卡（`StoragePairCard`，filesyncw 文件夹对）；
- :mod:`fcmd.gui.widgets.splitter`：分栏容器与尺寸持久化；
- :mod:`fcmd.gui.widgets.messages`：错误 / 警告 / 信息提示与确认框（统一走 QMessageBox）；
- :mod:`fcmd.gui.widgets.pickers`：文件 / 目录选择（取消返回空串，不用解元组）；
- :mod:`fcmd.gui.widgets.dialogs`：对话框外壳（边距 / 表单 / 按钮行）与按内容自适应；
- :mod:`fcmd.gui.widgets.zoom`：缩放倍率纯函数与按窗口等比缩放的图像预览标签；
- :mod:`fcmd.gui.widgets.empty`：列表 / 表格无数据时的居中空态。

约定：

- 卡片统一用 ``QFrame#Card``，标题由主题 QSS 上色；
- 数值 / 说明类文字用 :class:`~fcmd.gui.widgets.text.HintLabel`（次要色）与
  :func:`~fcmd.gui.widgets.text.value_label`（强调）；
- 日志区固定 ``QPlainTextEdit#Log`` + 工具条（复制 / 清空 / 导出），
  与既有日志区行为对齐；
- 全部控件类标注 ``pragma: no cover``（CI 无显示环境，不追求控件覆盖率）。
"""

from __future__ import annotations

from fcmd.gui.widgets.buttons import (
    Button,
    ButtonStateMixin,
    IconButton,
    LinkButton,
    ToggleChip,
    button,
    confirm,
    icon_button,
    link_button,
    set_button_icon,
    set_button_kind,
    set_button_style_name,
    set_button_text,
)
from fcmd.gui.widgets.cards import (
    Card,
    ChoiceCard,
    ChoiceGroup,
    Collapsible,
    PageScroll,
    fit_dialog_to_content,
)
from fcmd.gui.widgets.dialogs import DialogShell, FormDialog, action_row, dialog_footer
from fcmd.gui.widgets.empty import EmptyState
from fcmd.gui.widgets.forms import FormGrid, PathField, PathInputMixin, PathRow
from fcmd.gui.widgets.layouts import FlowLayout
from fcmd.gui.widgets.log import LogView
from fcmd.gui.widgets.messages import ask, error, info, warn
from fcmd.gui.widgets.metrics import (
    KeyValueGrid,
    LoadingBar,
    MetricGrid,
    ProgressBar,
    ProgressRow,
    SegmentBar,
    segment_band,
    segment_fill,
)
from fcmd.gui.widgets.pickers import pick_dir, pick_file, pick_files, save_file
from fcmd.gui.widgets.responsive import ContentHeightGuard, ResponsiveLabel, fit_flow_widths
from fcmd.gui.widgets.rows import ItemRow, RowDetailMixin, RowListWidget, SelectionRow
from fcmd.gui.widgets.sidebar_list import SidebarActionButton, SidebarEntry, SidebarList
from fcmd.gui.widgets.splitter import QuietSplitter, apply_splitter_sizes, make_splitter, splitter_sizes
from fcmd.gui.widgets.storage import (
    DiskAddTile,
    DiskGrid,
    DiskLocation,
    DiskTile,
    StorageCard,
    StoragePairCard,
    StorageRoots,
    default_disk_locations,
)
from fcmd.gui.widgets.tables import make_table, make_table_item, make_tree, make_tree_item
from fcmd.gui.widgets.tabs import PillTabBar, badge_text, pill_tabs, tab_widget
from fcmd.gui.widgets.text import (
    ElidedLabel,
    HintLabel,
    MonoLabel,
    SectionHeader,
    SectionTitle,
    StatusTag,
    ToneLabelMixin,
    ValueLabel,
    hint_label,
    mono_font,
    mono_label,
    value_label,
)
from fcmd.gui.widgets.zoom import ImagePreviewLabel, clamp_zoom, fit_zoom, zoom_in, zoom_out

__all__ = [
    "Button",
    "ButtonStateMixin",
    "Card",
    "ChoiceCard",
    "ChoiceGroup",
    "Collapsible",
    "ContentHeightGuard",
    "DialogShell",
    "DiskAddTile",
    "DiskGrid",
    "DiskLocation",
    "DiskTile",
    "ElidedLabel",
    "EmptyState",
    "FlowLayout",
    "FormDialog",
    "FormGrid",
    "HintLabel",
    "IconButton",
    "ImagePreviewLabel",
    "ItemRow",
    "KeyValueGrid",
    "LinkButton",
    "LoadingBar",
    "LogView",
    "MetricGrid",
    "MonoLabel",
    "PageScroll",
    "PathField",
    "PathInputMixin",
    "PathRow",
    "PillTabBar",
    "ProgressBar",
    "ProgressRow",
    "QuietSplitter",
    "ResponsiveLabel",
    "RowDetailMixin",
    "RowListWidget",
    "SectionHeader",
    "SectionTitle",
    "SegmentBar",
    "SelectionRow",
    "SidebarActionButton",
    "SidebarEntry",
    "SidebarList",
    "StatusTag",
    "StorageCard",
    "StoragePairCard",
    "StorageRoots",
    "ToggleChip",
    "ToneLabelMixin",
    "ValueLabel",
    "action_row",
    "apply_splitter_sizes",
    "ask",
    "badge_text",
    "button",
    "clamp_zoom",
    "confirm",
    "default_disk_locations",
    "dialog_footer",
    "error",
    "fit_dialog_to_content",
    "fit_flow_widths",
    "fit_zoom",
    "hint_label",
    "icon_button",
    "info",
    "link_button",
    "make_splitter",
    "make_table",
    "make_table_item",
    "make_tree",
    "make_tree_item",
    "mono_font",
    "mono_label",
    "pick_dir",
    "pick_file",
    "pick_files",
    "pill_tabs",
    "save_file",
    "segment_band",
    "segment_fill",
    "set_button_icon",
    "set_button_kind",
    "set_button_style_name",
    "set_button_text",
    "splitter_sizes",
    "tab_widget",
    "value_label",
    "warn",
    "zoom_in",
    "zoom_out",
]
