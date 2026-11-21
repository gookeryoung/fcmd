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

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QLineEdit,
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
    LoadingBar,
    LogView,
    MetricGrid,
    PathRow,
    ProgressRow,
    SegmentBar,
    SelectionRow,
    StatusTag,
    ToggleChip,
    button,
    icon_button,
    section_header,
    segment_band,
    segment_fill,
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
