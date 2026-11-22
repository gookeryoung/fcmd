"""``fcmd.gui.qt.widgets`` 的离屏测试：按钮尺寸统一与卡片标题结构。

按钮的四种变体（默认 / 主操作 / 危险 / 次要）必须共用同一套内边距与最小高度，
否则在同一行里（例如「刷新模型列表」旁边）高矮不一。卡片标题是普通 ``QLabel``，
不再使用 ``QGroupBox::title`` 子控件，避免 QSS 背景与定位问题。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from fcmd.gui import theme as _guitheme
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.widgets import (
    Card,
    ChoiceGroup,
    Collapsible,
    ItemRow,
    LoadingBar,
    LogView,
    MetricGrid,
    PathRow,
    PillTabBar,
    ProgressRow,
    SegmentBar,
    SelectionRow,
    StatusTag,
    ToggleChip,
    button,
    icon_button,
    pill_tabs,
    section_header,
    segment_band,
    segment_fill,
    set_button_kind,
    set_button_text,
)

_BUTTON_KINDS = ("", "primary", "danger", "ghost")


@pytest.fixture(scope="module", autouse=True)
def _app() -> object:
    app = QApplication.instance() or QApplication([])
    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    return app


def test_button_variants_share_size_hint() -> None:
    """四种按钮变体在相同文案下尺寸一致（对齐「刷新模型列表」）。"""
    sizes = {kind: button("刷新模型列表", kind=kind).sizeHint() for kind in _BUTTON_KINDS}
    assert len({size.height() for size in sizes.values()}) == 1
    assert len({size.width() for size in sizes.values()}) == 1
    # 共享层运行时基准 = 字体行高 + 补偿（min-height 的内容高，随平台字体不同），
    # 控件总高再加 QSS 上下各 1px 边框。
    expected = _qt_theme.runtime_control_height(QApplication.instance()) + 2
    assert next(iter(sizes.values())).height() == expected


def test_ghost_button_keeps_padding_of_default() -> None:
    """次要按钮只改配色，不改内边距（否则一行里高矮不一）。"""
    text = _qt_theme.stylesheet(_guitheme.resolve_theme("dark-blue"))
    default_block = text.split("QPushButton {", 1)[1].split("}", 1)[0]
    ghost_block = text.split("QPushButton#Ghost {", 1)[1].split("}", 1)[0]
    assert "padding: 0 " in default_block
    assert "padding: 0 " in ghost_block
    assert "min-height" in default_block


def test_single_line_controls_share_height() -> None:
    """按钮、输入框、下拉与数值框在同一布局里等高：统一基准在共享层收敛。"""
    container = QWidget()
    layout = QVBoxLayout(container)
    combo = QComboBox()
    combo.addItem("1")
    controls = [button("确定"), QLineEdit(), combo, QSpinBox(), QDoubleSpinBox()]
    for control in controls:
        layout.addWidget(control)
    container.show()
    QApplication.processEvents()
    assert len({control.height() for control in controls}) == 1
    expected = _qt_theme.runtime_control_height(QApplication.instance()) + 2
    assert controls[0].height() == expected


def test_segment_fill_rounds_and_clamps() -> None:
    """格图点亮数：四舍五入、非零至少一格、不超过总格数。"""
    assert segment_fill(None, 20) == 0
    assert segment_fill(0.0, 20) == 0
    assert segment_fill(0.1, 20) == 1
    assert segment_fill(50.0, 20) == 10
    assert segment_fill(100.0, 20) == 20
    assert segment_fill(120.0, 20) == 20


def test_segment_band_thresholds() -> None:
    """负载档位与概览硬件卡一致：≥85 告急、≥60 偏紧、其余充足。"""
    assert segment_band(None) == "idle"
    assert segment_band(0.0) == "success"
    assert segment_band(59.9) == "success"
    assert segment_band(60.0) == "warning"
    assert segment_band(84.9) == "warning"
    assert segment_band(85.0) == "danger"
    assert segment_band(100.0) == "danger"


def test_segment_bar_tracks_percent() -> None:
    bar = SegmentBar(segments=10, height=10)
    bar.set_percent(37.0)
    assert bar.percent() == 37.0
    assert "37.0%" in bar.toolTip()
    bar.set_percent(-5.0)
    assert bar.percent() == 0.0
    bar.set_percent(120.0)
    assert bar.percent() == 100.0
    bar.set_percent(None)
    assert bar.percent() is None
    assert bar.toolTip() == "等待采样"


def test_status_tag_tone_and_status() -> None:
    """状态标签：默认 muted，set_tone 未知值回落，set_status 一次写文案与色。"""
    tag = StatusTag("未连接")
    assert tag.objectName() == "StatusTag"
    assert tag.tone() == "muted"
    tag.set_tone("success")
    assert tag.tone() == "success"
    tag.set_tone("not-a-tone")
    assert tag.tone() == "muted"
    tag.set_status("运行中", "success")
    assert tag.text() == "运行中"
    assert tag.tone() == "success"
    tag.set_status("已退出")
    assert tag.tone() == "success"


def test_segment_bar_paints_before_first_sample() -> None:
    """首帧（尚未采样）也能绘制，不能因档位 idle 抛 KeyError。"""
    bar = SegmentBar(segments=12, height=10)
    bar.resize(240, 10)
    assert not bar.grab().isNull()


def test_card_title_is_plain_label_inside_frame() -> None:
    card = Card("硬件信息")
    assert card.title() == "硬件信息"
    assert not card.title_label.isHidden()
    card.setTitle("")
    assert card.title() == ""
    assert card.title_label.isHidden()


def test_card_title_action_shares_title_row() -> None:
    """标题动作（如刷新）与标题同行，右侧靠齐，不额外占一行。"""
    card = Card("引擎")
    action = QPushButton("刷新")
    card.add_title_action(action)
    assert card.title_row is not None
    assert card.title_row.indexOf(action) >= 0
    assert card.title_row.itemAt(card.title_row.count() - 1).widget() is action


def test_card_body_accepts_widgets() -> None:
    card = Card("指标")
    grid = MetricGrid((("CPU", "cpu"),), parent=card)
    card.body.addWidget(grid)
    assert card.body.count() == 1
    grid.set_value("cpu", "10 %")
    assert grid.value("cpu") == "10 %"


def test_path_row_commits_on_editing_finished() -> None:
    """设置页自动保存监听 committed：输入中不落盘，失焦 / 回车后才提交。"""
    row = PathRow(value="D:/models", show_reset=True)
    seen: list[str] = []
    row.committed.connect(seen.append)
    row.edit.setText("D:/models2")
    assert seen == []
    row.edit.editingFinished.emit()
    assert seen == ["D:/models2"]


def test_icon_button_height_matches_default_button() -> None:
    """图标按钮高度跟随刷新等普通按钮，右侧工具栏不出现高矮不一。"""
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    container = QWidget()
    layout = QHBoxLayout(container)
    refresh = button("刷新")
    icon = icon_button("⚙", tooltip="生成参数")
    layout.addWidget(refresh)
    layout.addWidget(icon)
    container.show()
    QApplication.processEvents()
    try:
        assert icon.height() == refresh.height()
        assert icon.width() == icon.height()
    finally:
        container.deleteLater()


def test_pill_tab_bar_badge_reserves_width_and_paints() -> None:
    """页签数量徽标：占用右侧额外宽度、按数量绘制、清零后恢复原宽。"""
    from PySide6.QtWidgets import QTabWidget

    tabs = QTabWidget()
    try:
        pill_tabs(tabs)
        tabs.addTab(QWidget(), "对话")
        tabs.addTab(QWidget(), "编程")
        bar = tabs.tabBar()
        assert isinstance(bar, PillTabBar)

        tabs.resize(360, 120)
        tabs.show()
        QApplication.processEvents()
        plain = tabs.grab().toImage()
        before = bar.tabSizeHint(0).width()
        bar.set_badge(0, 3)
        QApplication.processEvents()
        assert bar.badge(0) == 3
        assert bar.tabSizeHint(0).width() == before + 18
        assert tabs.grab().toImage() != plain  # 徽标真的画上去了

        bar.set_badge(1, 120)  # 三位数按 99 显示，不影响其它页签
        assert bar.badge(1) == 120
        assert bar.tabSizeHint(0).width() == before + 18

        bar.set_badge(0, 0)
        assert bar.badge(0) == 0
        assert bar.tabSizeHint(0).width() == before
    finally:
        tabs.deleteLater()


def test_pill_tabs_share_one_theme_contract() -> None:
    """``pill_tabs`` 统一药丸页签：无基线、不拉伸、容器名可换成行卡专用底色。"""
    from PySide6.QtWidgets import QTabWidget

    tabs = QTabWidget()
    try:
        assert pill_tabs(tabs) is tabs
        bar = tabs.tabBar()
        assert tabs.objectName() == "PillTabs"
        assert bar.objectName() == "PillTabBar"
        assert bar.drawBase() is False
        assert bar.expanding() is False
        pill_tabs(tabs, object_name="RowTabs")
        assert tabs.objectName() == "RowTabs"
        assert bar.objectName() == "PillTabBar"
    finally:
        tabs.deleteLater()


def test_icon_button_is_square_and_checkable() -> None:
    """图标按钮用字形作图标，可作展开 / 收起开关。"""
    widget = icon_button("⚙", tooltip="生成参数", checkable=True)
    try:
        assert widget.text() == "⚙"
        assert widget.toolTip() == "生成参数"
        assert widget.isCheckable()
        assert widget.objectName() == "IconButton"
    finally:
        widget.deleteLater()


def test_toggle_chip_is_pill_with_icon_and_checkable() -> None:
    """模式胶囊：固定 30px 高（全圆角）、图标随选中状态在 muted / accent 间切换。"""
    chip = ToggleChip("画图", icon="paint", tooltip="生成图片")
    try:
        assert chip.objectName() == "ToggleChip"
        assert chip.text() == "画图"
        assert chip.toolTip() == "生成图片"
        assert chip.isCheckable()
        assert chip.height() == 30
        assert chip.icon().isNull() is False
        assert chip.property("fcmdIconName") == "paint"
        assert chip.property("fcmdIconTone") == "muted"
        chip.setChecked(True)
        assert chip.property("fcmdIconTone") == "accent"
        chip.setChecked(False)
        assert chip.property("fcmdIconTone") == "muted"
    finally:
        chip.deleteLater()


def test_toggle_chip_keeps_capsule_height_and_symmetric_padding() -> None:
    """胶囊应用 QSS 后仍是 30px 高（圆角才成胶囊），内容左右留白等宽。

    QSS ``min-height`` 算的是内容高度：通用 ``QToolButton`` 的上下 padding
    不归零会把胶囊撑到 38px，既不是胶囊也比同行 34px 按钮高，看起来发歪。
    """
    chip = ToggleChip("对话", icon="chat")
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.addWidget(chip)
    layout.addStretch(1)
    try:
        host.show()
        QApplication.processEvents()
        # 上屏后 QSS 才会把 min / max-height 换成控件约束，这里必须仍是 30。
        assert chip.height() == 30
        assert chip.minimumHeight() == 30
        assert chip.maximumHeight() == 30
        hint = chip.sizeHint()
        expected = 2 * 12 + chip.iconSize().width() + 6 + chip.fontMetrics().horizontalAdvance("对话")
        assert hint.width() == expected
        assert hint.height() == 30
        assert chip.minimumSizeHint() == hint
    finally:
        host.close()
        host.deleteLater()


def test_toggle_chip_content_color_follows_theme_tokens() -> None:
    """自绘内容色与 QSS 语义色一致：选中 accent、悬停 text、未选中 muted。"""
    theme = _qt_theme.active_theme()
    chip = ToggleChip("对话", icon="chat")
    try:
        assert chip._content_color().name() == theme.palette.muted
        QApplication.sendEvent(chip, QEvent(QEvent.Type.Enter))
        assert chip._content_color().name() == theme.palette.text
        assert chip.property("fcmdIconTone") == "text"
        QApplication.sendEvent(chip, QEvent(QEvent.Type.Leave))
        assert chip._content_color().name() == theme.palette.muted
        assert chip.property("fcmdIconTone") == "muted"
        chip.setChecked(True)
        assert chip._content_color().name() == theme.palette.accent
    finally:
        chip.deleteLater()


def test_collapsible_can_hide_header() -> None:
    """入口移到外部图标按钮时，分区内置标题隐藏但仍可程序化展开。"""
    section = Collapsible("连接参数", header=False)
    try:
        assert not section.toggle.isVisible()
        assert not section.is_expanded()
        section.set_expanded(True)
        assert section.is_expanded()
        assert not section.content.isHidden()
    finally:
        section.deleteLater()


def test_collapsible_set_title_updates_toggle() -> None:
    """文件清单等动态数量通过 set_title 更新折叠区标题。"""
    section = Collapsible("文件清单")
    assert section.toggle.text() == "文件清单"
    assert not section.is_expanded()
    section.set_title("文件清单（2 个 · 4.00 KB）")
    assert section.toggle.text() == "文件清单（2 个 · 4.00 KB）"
    section.set_expanded(True)
    assert section.is_expanded()


def test_section_header_keeps_section_title_contract() -> None:
    """分节标题继续使用 SectionTitle，右侧留给状态 / 动作控件。"""
    card = Card()
    header, label = section_header("系统信息", card)
    try:
        assert label.text() == "系统信息"
        assert label.objectName() == "SectionTitle"
        assert header.count() == 2
    finally:
        card.deleteLater()


def test_choice_group_selects_and_emits() -> None:
    """互斥选项组读写当前 key，程序化切换可按需发出 changed。"""
    group = ChoiceGroup((("a", "甲", "说明 A"), ("b", "乙", "说明 B")), current="a")
    seen: list[str] = []
    group.changed.connect(seen.append)
    try:
        assert group.key() == "a"
        group.set_key("b")
        assert group.key() == "b"
        assert seen == []
        group.set_key("a", emit=True)
        assert seen == ["a"]
        group.set_key("missing")
        assert group.key() == "a"
    finally:
        group.deleteLater()


def test_progress_row_keeps_actions_and_optional_bar() -> None:
    """进度行可挂动作按钮；纯状态场景可隐藏进度条。"""
    row = ProgressRow("就绪")
    action = button("下载")
    try:
        assert row.label.text() == "就绪"
        assert not row.bar.isHidden()
        row.add_action(action)
        row.set_text("")
        assert row.label.isHidden()
    finally:
        row.deleteLater()
    status_only = ProgressRow("测试完成", show_bar=False)
    try:
        assert status_only.bar.isHidden()
        assert status_only.label.text() == "测试完成"
    finally:
        status_only.deleteLater()


def _alignment(row: ItemRow, widget: QWidget) -> Qt.AlignmentFlag:
    """取行卡右侧某个控件在布局里的对齐方式（PySide 的 alignment() 不接受控件参数）。"""
    for index in range(row.header_row.count()):
        item = row.header_row.itemAt(index)
        if item is not None and item.widget() is widget:
            return item.alignment()
    raise AssertionError("控件不在行卡头部布局里")


def test_item_row_copy_button_is_optional_and_copies_full_text() -> None:
    """行卡的「复制全称」是可选配置：传文本才有按钮，点击复制到剪贴板并给反馈。"""
    from PySide6.QtGui import QGuiApplication

    plain = ItemRow("Qwen3.8-27B")
    try:
        assert plain.copy_text() == ""
        assert plain.copy_button() is None
    finally:
        plain.deleteLater()

    row = ItemRow("Qwen3.8-27B", copy_text="Qwen/Qwen3.8-27B")
    try:
        button = row.copy_button()
        assert button is not None
        assert button.isHidden() is False
        assert button.toolTip() == "复制 Qwen/Qwen3.8-27B"
        assert row.copy_text() == "Qwen/Qwen3.8-27B"

        button.click()
        clipboard = QGuiApplication.clipboard()
        assert clipboard is not None and clipboard.text() == "Qwen/Qwen3.8-27B"
        assert button.toolTip() == "已复制"

        # 关掉复制能力：按钮收起，不再占标题行
        row.set_copy_text("")
        assert row.copy_text() == ""
        assert button.isHidden() is True

        # 再次配置：同一个按钮复用，可复制新文本
        row.set_copy_text("Qwen/Qwen3-8B")
        assert button.isHidden() is False
        assert button.toolTip() == "复制 Qwen/Qwen3-8B"
        button.click()
        assert clipboard is not None and clipboard.text() == "Qwen/Qwen3-8B"
    finally:
        row.deleteLater()


def test_item_row_centers_actions_with_tag_or_title_line() -> None:
    """右侧动作与「标签行」居中对齐；只有一行标题时整行居中。"""
    row = ItemRow("Ubuntu", "v2")
    try:
        action = button("启动", kind="primary")
        row.add_action(action)
        assert _alignment(row, action) == Qt.AlignmentFlag.AlignBottom
        tag = StatusTag("已停止", tone="muted")
        row.add_tag(tag)
        # 有第二行（版本 / 标签）时按钮与它底部对齐，中线基本重合
        assert _alignment(row, action) == Qt.AlignmentFlag.AlignBottom
        assert _alignment(row, row.expand_button) == Qt.AlignmentFlag.AlignBottom
    finally:
        row.deleteLater()

    plain = ItemRow("Debian", "")
    try:
        act = button("启动")
        plain.add_action(act)
        # 空副标题不占位：单行行卡不再多出一条空白行
        assert plain.sub_label.isHidden() is True
        assert _alignment(plain, act) == Qt.AlignmentFlag.AlignVCenter
        plain.set_subtitle("v1")
        assert plain.sub_label.isHidden() is False
        assert _alignment(plain, act) == Qt.AlignmentFlag.AlignBottom
        plain.set_subtitle("")
        assert plain.sub_label.isHidden() is True
        assert _alignment(plain, act) == Qt.AlignmentFlag.AlignVCenter
    finally:
        plain.deleteLater()


def test_selection_row_toggles_property() -> None:
    """行控件选中态由 mixin 统一切换 selected 动态属性。"""
    row = QFrame()
    try:
        SelectionRow.set_selected(row, True)
        assert row.property("selected") == "true"
        SelectionRow.set_selected(row, False)
        assert row.property("selected") == "false"
    finally:
        row.deleteLater()


def test_item_row_builds_slots_and_expands() -> None:
    """``ItemRow`` 提供行卡骨架：标题 / 副标题 / 标签 / 动作 / 行内展开区。"""
    row = ItemRow("Ubuntu", "v2 · 2.0 GB")
    try:
        # 共享外观契约：所有应用的行卡同为 ItemRow + itemRow 属性 + ItemRowName 标题。
        assert row.objectName() == "ItemRow"
        # 行内文字不拦鼠标：可选中文本会让点击落在标签上、列表选不中这一行。
        for label in (row.title_label, row.sub_label):
            assert label.textInteractionFlags() == Qt.TextInteractionFlag.NoTextInteraction
        assert row.property("itemRow") == "true"
        assert row.title_label.objectName() == "ItemRowName"
        assert row.title_label.text() == "Ubuntu"
        assert row.sub_label.text() == "v2 · 2.0 GB"
        assert row.property("selected") == "false"
        assert row.is_expanded() is False
        assert row.detail_host.isHidden()

        tag = StatusTag("运行中", tone="success")
        row.add_tag(tag)
        # 胶囊（StatusTag 也是 QLabel）同样不能抢走点击
        assert tag.textInteractionFlags() == Qt.TextInteractionFlag.NoTextInteraction
        note_widget = QPushButton("小字")
        row.add_note_widget(note_widget)
        action = QPushButton("启动")
        row.add_action(action)
        # 插槽顺序：副标题 → 小字 → 拉伸 → 胶囊；动作始终在展开箭头之前。
        note_indexes = [row.note_row.indexOf(widget) for widget in (row.sub_label, note_widget, tag)]
        assert note_indexes == sorted(note_indexes)
        assert row.header_row.indexOf(action) + 1 == row.header_row.indexOf(row.expand_button)

        row.set_title("Debian")
        row.set_subtitle("v1 · 未知")
        assert row.title_label.text() == "Debian"
        assert row.title_label.toolTip() == "Debian"
        assert row.sub_label.text() == "v1 · 未知"

        row.set_expanded(True)
        assert row.is_expanded() is True
        assert not row.detail_host.isHidden()
        assert row.expand_button.toolTip() == "收起详情"
        row.set_expanded(False)
        assert row.is_expanded() is False
        assert row.detail_host.isHidden()
        assert row.expand_button.toolTip() == "展开详情"

        row.set_metrics("2.0 GB")
        assert row.metrics_label is not None and row.metrics_label.text() == "2.0 GB"
        assert row.metrics_label.objectName() == "RowMetrics"
        row.set_metrics("3.0 GB")
        assert row.metrics_label.text() == "3.0 GB"
    finally:
        row.deleteLater()


def test_item_row_groups_tags_and_actions() -> None:
    """行卡胶囊共享 rowChip 契约；动作区与文字区之间有细分隔线且不破坏插槽顺序。"""
    row = ItemRow("Qwen/demo", "Qwen3 · 对话")
    try:
        tag = StatusTag("已安装", tone="success")
        row.add_tag(tag)
        assert tag.property("rowChip") == "true"

        # 没有动作时不显示分隔线，避免悬空竖线。
        assert row.action_divider.isHidden()

        action = QPushButton("启动服务")
        row.add_action(action)
        assert not row.action_divider.isHidden()
        assert row.header_row.indexOf(row.action_divider) + 1 == row.header_row.indexOf(action)
        assert row.header_row.indexOf(action) + 1 == row.header_row.indexOf(row.expand_button)

        # 再加按钮：分隔线仍只一条，动作保持紧挨展开箭头。
        second = QPushButton("下载")
        row.add_action(second)
        assert row.header_row.indexOf(action) + 1 == row.header_row.indexOf(second)
        assert row.header_row.indexOf(second) + 1 == row.header_row.indexOf(row.expand_button)
        assert row.header_row.count() == 5  # 文字区 + 分隔线 + 两个动作 + 展开

        # 选中行底色更深：分隔线同步 selected 属性，主题换成更亮的强调色。
        assert row.action_divider.property("selected") == "false"
        row.set_selected(True)
        assert row.action_divider.property("selected") == "true"
        row.set_selected(False)
        assert row.action_divider.property("selected") == "false"
    finally:
        row.deleteLater()


def test_item_row_metrics_lane_has_fixed_width() -> None:
    """数值 / 状态列固定宽度：长短文案不改变分隔线与动作按钮的横向位置。"""
    short = ItemRow("Ubuntu", "v2")
    long = ItemRow("Qwen/demo", "Qwen3 · 对话")
    try:
        short.set_metrics("2.0 KiB")
        long.set_metrics("≈123.0 GiB")
        assert short.metrics_label is not None
        assert long.metrics_label is not None
        assert short.metrics_label.width() == long.metrics_label.width()
        assert short.action_divider.width() == 1
        assert not short.action_divider.isHidden()
    finally:
        short.deleteLater()
        long.deleteLater()


def test_loading_bar_tracks_elapsed_and_stops() -> None:
    """加载条显示说明文字与已用时，stop 后收起且停止计时。"""
    bar = LoadingBar("正在加载模型列表…")
    try:
        assert not bar.isVisible()
        bar.start("正在加载第 1 页…")
        assert bar.isVisible()
        assert bar.is_running()
        assert bar.text_label.text() == "正在加载第 1 页…"
        bar.set_text("正在扫描本地模型…")
        assert bar.text_label.text() == "正在扫描本地模型…"
        bar._tick()
        assert bar.elapsed_text().endswith("s")
        bar.stop()
        assert not bar.isVisible()
        assert not bar.is_running()
    finally:
        bar.deleteLater()


def test_loading_bar_determinate_progress() -> None:
    """能预估总量时切确定进度：按字节填充；stop 后回到忙碌态，不残留百分比。"""
    bar = LoadingBar("下载中…")
    try:
        bar.start("下载 Ubuntu…")
        bar.set_progress(256, 1024)
        assert bar.bar.minimum() == 0
        assert bar.bar.maximum() == 1000
        assert bar.bar.value() == 250
        bar.set_progress(9999, 1024)  # 超额（服务端总量不准）时夹在满格
        assert bar.bar.value() == 1000
        # 几十 GB 的镜像也不会撑爆 QProgressBar 的 int 范围（按比例折算）
        bar.set_progress(30 * 1024**3, 60 * 1024**3)
        assert bar.bar.maximum() == 1000
        assert bar.bar.value() == 500
        bar.set_progress(512, None)  # 总量未知：回到忙碌动画
        assert bar.bar.maximum() == 0
        bar.set_progress(128, 0)
        assert bar.bar.maximum() == 0
        bar.set_progress(128, 256)
        bar.stop()
        assert bar.bar.maximum() == 0
    finally:
        bar.deleteLater()


def test_log_view_roundtrip() -> None:
    view = LogView(height=80)
    view.append("第一行")
    view.append("第二行")
    assert "第一行" in view.text()
    view.clear()
    assert view.text() == ""


def test_button_attaches_shared_icon_automatically() -> None:
    """公共按钮按文案自动挂图标；显式传空串可保持纯文字。"""
    refresh = button("刷新", kind="ghost")
    assert refresh.icon().isNull() is False
    plain = button("取消")
    assert plain.icon().isNull() is True
    forced = button("取消", icon="trash")
    assert forced.icon().isNull() is False
    disabled_icon = button("刷新", icon="")
    assert disabled_icon.icon().isNull() is True


def test_set_button_kind_switches_variant() -> None:
    """动态动作按钮可在主按钮 / 危险按钮间切换；变体未变时幂等。"""
    widget = button("停止", kind="primary")
    try:
        assert widget.objectName() == "Primary"
        set_button_kind(widget, "danger")
        assert widget.objectName() == "Danger"
        set_button_kind(widget, "danger")
        assert widget.objectName() == "Danger"
        set_button_text(widget, "停止", tone="danger")
        assert widget.property("fcmdIconTone") == "danger"
        set_button_kind(widget, "")
        assert widget.objectName() == ""
    finally:
        widget.deleteLater()


def test_set_button_text_updates_icon_and_tone() -> None:
    """动态改文案时同步换图标与色调：启动 → 停止要换成 stop + danger。"""
    widget = button("启动服务", kind="primary")
    try:
        assert widget.property("fcmdIconName") == "run"
        assert widget.property("fcmdIconTone") == "on_accent"
        set_button_text(widget, "停止服务", tone="danger")
        assert widget.text() == "停止服务"
        assert widget.property("fcmdIconName") == "stop"
        assert widget.property("fcmdIconTone") == "danger"
        set_button_text(widget, "已启动", tone="text")
        assert widget.property("fcmdIconName") == "check"
    finally:
        widget.deleteLater()


def test_fit_dialog_to_content_removes_bottom_blank() -> None:
    """对话框按内容高度贴合：不写死大尺寸，也不留下底部空白。"""
    from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

    from fcmd.gui.qt.widgets import fit_dialog_to_content

    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel("内容"))
    dialog.resize(600, 600)
    fit_dialog_to_content(dialog, min_width=520)
    assert dialog.width() >= 520
    assert dialog.height() < 200
    assert abs(dialog.height() - dialog.sizeHint().height()) <= 4
    dialog.deleteLater()


def test_card_title_with_action_stays_one_line() -> None:
    """卡片标题带动作（如「刷新」）时标题不换行：长标题也在同一行。"""
    from PySide6.QtWidgets import QApplication

    from fcmd.gui.qt.widgets import Card, button

    app = QApplication.instance() or QApplication([])
    card = Card("推理引擎（llama.cpp）")
    card.add_title_action(button("刷新", kind="ghost"))
    card.resize(520, 160)
    card.show()
    app.processEvents()
    metrics = card.title_label.fontMetrics()
    assert card.title_label.wordWrap() is False
    assert card.title_label.height() < metrics.height() * 2
    card.close()
    card.deleteLater()


# ---------------------------------------------------------------------- #
# 选中态同步：只重抛光旧 / 新两行（切换选中不卡顿）
# ---------------------------------------------------------------------- #
class _Row(SelectionRow, QFrame):
    """测试用行卡：只关心选中态，不搞真实内容。"""

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("selected", "false")
        self.polish_count = 0


class _CountingStyle:
    """记录 polish / unpolish 次数的 style 替身。"""

    def __init__(self, owner: _Row) -> None:
        self.owner = owner

    def unpolish(self, _widget: object) -> None:
        self.owner.polish_count += 1

    def polish(self, _widget: object) -> None:
        self.owner.polish_count += 1


def _list_with(count: int) -> tuple[QListWidget, list[_Row]]:
    from fcmd.gui.qt.widgets import RowListWidget

    view = RowListWidget()
    view.setVisible(False)
    rows: list[_Row] = []
    for _index in range(count):
        item = QListWidgetItem(view)
        row = _Row()
        rows.append(row)
        view.setItemWidget(item, row)
    for row in rows:
        row.style = lambda row=row: _CountingStyle(row)  # type: ignore[method-assign]
    return view, rows


def test_set_selected_is_idempotent() -> None:
    """状态没变时不重复抛光：重复调用只付一次 QSS 重解析。"""
    row = _Row()
    row.style = lambda: _CountingStyle(row)  # type: ignore[method-assign]
    row.set_selected(False)
    assert row.polish_count == 0

    row.set_selected(True)
    assert row.polish_count == 2  # unpolish + polish

    row.set_selected(True)
    assert row.polish_count == 2  # 幂等：不再抛光

    row.set_selected(False)
    assert row.polish_count == 4
    row.deleteLater()


def test_sync_selection_only_touches_old_and_new_rows() -> None:
    """切换选中只处理「上一行 / 当前行」，不是 O(N) 全列表重抛光。"""
    view, rows = _list_with(40)
    try:
        view.sync_selection(3)
        assert [row.polish_count for row in rows].count(2) == 1  # 只有 3 号抛光
        assert rows[3].is_selected() is True

        for row in rows:
            row.polish_count = 0
        view.sync_selection(7)
        polished = [index for index, row in enumerate(rows) if row.polish_count]
        assert polished == [3, 7]  # 旧行取消 + 新行选中
        assert rows[3].is_selected() is False
        assert rows[7].is_selected() is True

        # 切到同一行是空操作
        for row in rows:
            row.polish_count = 0
        view.sync_selection(7)
        assert [row.polish_count for row in rows] == [0] * 40

        # 选中行被裁掉（列表重建后行数变少）时不越界
        view.sync_selection(99)
        assert rows[7].is_selected() is False
    finally:
        view.deleteLater()


def test_selected_row_repolishes_child_widgets() -> None:
    """选中态变化要重抛光子控件：否则依赖祖先 [selected] 的 Ghost 边框不会更新。"""
    row = _Row()
    child = QPushButton("配置", row)
    counter = {"unpolish": 0, "polish": 0}

    class _ChildStyle:
        def unpolish(self, _widget: object) -> None:
            counter["unpolish"] += 1

        def polish(self, _widget: object) -> None:
            counter["polish"] += 1

    child.style = lambda: _ChildStyle()  # type: ignore[method-assign]
    try:
        row.set_selected(True)
        assert counter == {"unpolish": 1, "polish": 1}
        row.set_selected(False)
        assert counter == {"unpolish": 2, "polish": 2}
    finally:
        row.deleteLater()
