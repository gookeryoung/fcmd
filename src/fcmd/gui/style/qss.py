"""QSS 模板：样式文本与运行时函数分离，``stylesheet()`` 负责代入 token。"""

from __future__ import annotations

from string import Template

from .qss_help import HELP_QSS

_QSS = Template(
    """
QWidget { color: $text; font-size: ${font_body}pt; }
QMainWindow, QDialog, QScrollArea, QStackedWidget { background: $background; }
QWidget#PageBody { background: $background; }
QLabel { background: transparent; }
QMenuBar { background: $surface; color: $text; border-bottom: 1px solid $border; }
QMenuBar::item { padding: 4px 10px; background: transparent; }
QMenuBar::item:selected { background: $surface_active; border-radius: 4px; }
QMenu { background: $surface; color: $text; border: 1px solid $border; padding: 4px; }
QMenu::item { padding: 5px 22px 5px 18px; border-radius: 4px; }
QMenu::item:selected { background: $surface_active; }
QMenu::separator { height: 1px; background: $border; margin: 4px 6px; }
/* 行内「更多动作」菜单：与行内 Ghost 按钮同一套圆角 / 底色 / hover，不使用
   默认菜单的宽左侧沟槽与系统高亮色。 */
QMenu#RowActionMenu {
    background: $surface;
    border: 1px solid $border_strong;
    border-radius: 6px;
    padding: 4px;
}
QMenu#RowActionMenu::item {
    padding: 6px 12px 6px 6px;
    border-radius: 4px;
    color: $text;
}
QMenu#RowActionMenu::item:selected { background: $surface_hover; color: $text; }
QMenu#RowActionMenu::item:disabled { color: $disabled_fg; }
QMenu#RowActionMenu::icon { padding-left: 4px; }
/* 行内菜单按钮沿用 Ghost 外形：去掉 Qt 默认菜单小三角，图标已经表达「更多」。 */
QPushButton[rowMenuButton="true"]::menu-indicator {
    image: none;
    width: 0;
}
QStatusBar { background: $surface; color: $muted; border-top: 1px solid $border; }
QStatusBar QLabel { color: $muted; }

QFrame#Card {
    background: $surface;
    border: 1px solid $border;
    border-radius: ${radius}px;
}
QLabel#CardTitle {
    color: $muted;
    font-weight: 600;
    font-size: ${font_body}pt;
    padding: 0 0 2px 0;
}

QLabel#Title { font-size: ${font_title}pt; font-weight: 600; }
QLabel#SectionTitle { font-size: ${font_heading}pt; font-weight: 600; }
QLabel#Hint { color: $muted; font-size: ${font_small}pt; }
QLabel#Value { font-weight: 600; }
QLabel#LoadValue { font-size: ${font_heading}pt; font-weight: 600; }
QLabel#LoadValue[band="success"] { color: $success; }
QLabel#LoadValue[band="warning"] { color: $warning; }
QLabel#LoadValue[band="danger"] { color: $danger; }
QLabel#RateValue { color: $accent; font-size: ${font_title}pt; font-weight: 700; }
QLabel#RateUnit { color: $muted; font-size: ${font_body}pt; }
QLabel#MeterValue { font-size: ${font_small}pt; font-weight: 600; }
QLabel#MeterValue[band="success"] { color: $success; }
QLabel#MeterValue[band="warning"] { color: $warning; }
QLabel#MeterValue[band="danger"] { color: $danger; }
QFrame#LoadBlock {
    background: $background;
    border: 1px solid $border;
    border-radius: 6px;
}
/* 紧凑行：行内按钮 / 图标按钮降一档高度，配合 ItemRow(dense=True) 的内边距，
   让同屏能多看几行且不挤压文字列。 */
QFrame[itemRow="true"][dense="true"] QPushButton#Primary,
QFrame[itemRow="true"][dense="true"] QPushButton#Ghost,
QFrame[itemRow="true"][dense="true"] QPushButton#Danger {
    min-height: 26px;
    max-height: 26px;
}
QFrame[itemRow="true"][dense="true"] QToolButton#IconButton {
    min-width: 26px;
    max-width: 26px;
    min-height: 26px;
    max-height: 26px;
}
/* 行卡胶囊：共享高度 / 圆角 / 内边距 / 字号（见 gui/widgets/rows.py 的 rowChip 属性），
   对象名只负责颜色语义；边框用同色系浅描边，避免一排彩色粗框抢走标题的注意力。 */
QFrame[itemRow="true"] QLabel[rowChip="true"] {
    min-height: 18px;
    max-height: 18px;
    padding: 0 7px;
    border-radius: 9px;
    font-size: ${font_small}pt;
    font-weight: 600;
}
QLabel#OwnerTag {
    color: $muted;
    background: $surface_active;
    border: 1px solid $border;
}
/* 定时提醒：大号时钟面板与放大的操作控件。颜色全部走主题 token，切主题后自动跟随。 */
QFrame#AlarmClockHero {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 $surface, stop:1 $surface_hover);
    border: 1px solid $border_strong; border-radius: 14px;
}
QLabel#AlarmClockTime { color: $text; background: transparent; font-size: 50pt; font-weight: 700; }
QLabel#AlarmClockDate { color: $muted; background: transparent; font-size: ${font_body}pt; font-weight: 600; }
QLabel#AlarmClockStatus { color: $muted; background: transparent; font-size: ${font_heading}pt; font-weight: 700; }
QLabel#AlarmClockStatus[tone="accent"] { color: $accent; }
QLabel#AlarmClockStatus[tone="success"] { color: $success; }
QLabel#AlarmClockStatus[tone="warning"] { color: $warning; }
QLabel#AlarmClockStatus[tone="danger"] { color: $danger; }
QLabel#AlarmFormLabel { color: $muted; font-size: ${font_body}pt; font-weight: 600; }
QLineEdit#AlarmTimeInput {
    color: $text; font-size: ${font_heading}pt; font-weight: 700;
    min-height: 42px; max-height: 42px; padding: 0 10px;
}
QPushButton[alarmAction="true"] {
    min-height: 42px; max-height: 42px; padding: 0 18px;
    font-size: ${font_body}pt; font-weight: 600;
}
QPushButton[alarmDelay="true"] {
    min-height: 32px; max-height: 32px; padding: 0 4px;
    font-size: ${font_small}pt; font-weight: 600;
}
QCheckBox#AlarmRepeat { font-size: ${font_body}pt; font-weight: 600; }
QDialog#AlarmReminderDialog { background: $background; }
QFrame#AlarmReminderHero {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 $surface, stop:1 $surface_hover);
    border: 3px solid $danger; border-radius: 16px;
}
QLabel#AlarmReminderTitle { color: $danger; background: transparent; font-size: 40pt; font-weight: 700; }
QLabel#AlarmReminderTitle[tone="warning"] { color: $warning; }
QLabel#AlarmReminderHint { color: $muted; background: transparent; font-size: ${font_body}pt; font-weight: 600; }
QLabel#StatusTag {
    min-height: 18px;
    max-height: 18px;
    padding: 0 7px;
    border-radius: 9px;
    font-size: ${font_small}pt;
    font-weight: 600;
    color: $muted;
    background: $surface_hover;
    border: 1px solid $border;
}
QLabel#StatusTag[tone="success"] { color: $success; background: $success_soft; border-color: $success_soft_strong; }
QLabel#StatusTag[tone="warning"] { color: $warning; background: $warning_soft; border-color: $warning_soft_strong; }
QLabel#StatusTag[tone="danger"] { color: $danger; background: $danger_soft; border-color: $danger_soft_strong; }
QLabel#StatusTag[tone="accent"] { color: $accent; background: $accent_soft; border-color: $accent_soft_strong; }
QLabel#Danger { color: $danger; }
QLabel#Warning { color: $warning; }
QLabel#Success { color: $success; }
QLabel#Mono { font-family: "$mono_family"; font-size: ${font_small}pt; }

QPushButton {
    background: $surface_hover;
    color: $text;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 0 ${control_pad_x};
    min-height: $control_height;
    max-height: $control_height;
}
QPushButton:hover { background: $surface_active; border-color: $border_strong; }
QPushButton:pressed { background: $border; }
/* 禁用态：底色比可用态更平（贴近窗口底）、文字退一档、边框几乎消失，
   与 hover / pressed 的层次完全分开；只靠 muted + surface 在浅色主题里
   反而比可用态更亮。具体色值由各主题的 disabled_* token 决定。 */
QPushButton:disabled { color: $disabled_fg; background: $disabled_bg; border-color: $disabled_border; }
QPushButton#Primary {
    background: $accent;
    color: $on_accent;
    border-color: $accent;
    font-weight: 600;
}
QPushButton#Primary:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#Primary:pressed { background: $accent_pressed; }
QPushButton#Primary:disabled { background: $disabled_bg; color: $disabled_fg; border-color: $disabled_border; }
QPushButton#Danger { color: $danger; border-color: $danger; background: transparent; }
QPushButton#Danger:hover { background: $danger; color: $on_accent; }
QPushButton#Danger:disabled { color: $disabled_fg; border-color: $disabled_border; background: transparent; }
QComboBox#ModelCombo[running="true"] { color: $success; border-color: $success; font-weight: 600; }
QComboBox#ModelCombo {
    min-height: $run_control_height;
    max-height: $run_control_height;
    padding: 0 10px;
}
QPushButton#RunPrimary, QPushButton#RunAction, QPushButton#RunDanger {
    min-height: $run_control_height;
    max-height: $run_control_height;
    padding: 0 16px;
    border-radius: 7px;
    font-weight: 600;
}
QPushButton#RunPrimary { background: $accent; color: $on_accent; border-color: $accent; }
QPushButton#RunPrimary:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#RunPrimary:pressed { background: $accent_pressed; }
QPushButton#RunPrimary:disabled { background: $disabled_bg; color: $disabled_fg; border-color: $disabled_border; }
QPushButton#RunAction { background: $surface_hover; color: $text; border-color: $border; }
QPushButton#RunAction:hover { background: $surface_active; border-color: $border_strong; }
QPushButton#RunAction:pressed { background: $border; }
/* 禁用态必须覆盖 #RunAction 的常态配色：只靠全局 QPushButton:disabled 会被 ID 选择器盖掉。 */
QPushButton#RunAction:disabled { background: $disabled_bg; color: $disabled_fg; border-color: $disabled_border; }
QPushButton#RunDanger { color: $danger; border-color: $danger; background: transparent; }
QPushButton#RunDanger:hover { background: $danger; color: $on_accent; }
QPushButton#RunDanger:disabled { color: $disabled_fg; border-color: $disabled_border; background: transparent; }
/* Hero 主行动按钮：页面里只有一个「开始 / 停止」时给足点击面积（扫描页起步）。 */
QPushButton#HeroPrimary, QPushButton#HeroDanger {
    min-height: $hero_control_height;
    max-height: $hero_control_height;
    padding: 0 26px;
    border-radius: 8px;
    font-size: ${font_heading}pt;
    font-weight: 700;
}
QPushButton#HeroPrimary { background: $accent; color: $on_accent; border-color: $accent; }
QPushButton#HeroPrimary:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#HeroPrimary:pressed { background: $accent_pressed; border-color: $accent_pressed; }
QPushButton#HeroPrimary:disabled { background: $disabled_bg; color: $disabled_fg; border-color: $disabled_border; }
QPushButton#HeroDanger { color: $danger; border-color: $danger; background: transparent; }
QPushButton#HeroDanger:hover { background: $danger; color: $on_accent; }
QPushButton#HeroDanger:disabled { color: $disabled_fg; border-color: $disabled_border; background: transparent; }
QFrame#ChatSidebar {
    background: transparent;
    border: none;
    border-radius: 0;
}
QSplitter#QuietSplitter::handle { background: transparent; }
QSplitter#QuietSplitter::handle:hover { background: $accent_soft; }
QSplitter#QuietSplitter::handle:pressed { background: $accent_soft_strong; }
/* 通用侧边栏列表：条目由 SidebarList 的 delegate 自绘，这里只保留透明底。 */
QListWidget#SidebarList { background: transparent; border: none; outline: 0; padding: 0; }
/* 侧边栏主操作：与条目同宽同圆角，但底色更实、边框更深，和纯文字条目区分。 */
QPushButton#SidebarAction {
    background: $surface_active;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 8px;
    padding: 0 10px;
    min-height: 34px;
    max-height: 34px;
    text-align: left;
    font-weight: 600;
}
QPushButton#SidebarAction:hover { background: $surface_hover; border-color: $accent; }
QPushButton#SidebarAction:pressed { background: $accent_soft_strong; border-color: $accent; }
QPushButton#SidebarAction:disabled { background: $disabled_bg; color: $disabled_fg; border-color: $disabled_border; }
QTextEdit#ChatTranscript {
    background: transparent;
    border: none;
    padding: 4px 6px;
}
QFrame#ChatComposer {
    background: $surface;
    border: 1px solid $border;
    border-radius: 16px;
}
QLabel#PaintPreview {
    background: $surface;
    border: 1px solid $border;
    border-radius: ${radius}px;
}
QPlainTextEdit#ChatInput {
    background: transparent;
    border: none;
    padding: 4px 2px;
}
QToolButton#ChatIconButton, QToolButton#ChatStop, QToolButton#ChatSend {
    border: none;
    border-radius: 9px;
    padding: 0;
}
QToolButton#ChatIconButton { background: transparent; }
QToolButton#ChatIconButton:hover { background: $surface_hover; }
/* 对话列表底部操作条：不再包描边容器，只留三个透明图标键，避免与正文抢视线。 */
QFrame#ChatSidebarActions {
    background: transparent;
    border: none;
}
QFrame#ChatSidebarActions QToolButton#ChatIconButton { border-radius: 7px; }
QToolButton#ChatStop { background: transparent; }
QToolButton#ChatStop:hover { background: $danger_soft; }
QToolButton#ChatStop:disabled { background: transparent; }
QToolButton#ChatStop:disabled, QToolButton#ChatSend:disabled, QToolButton#ChatIconButton:disabled {
    color: $disabled_fg;
}
/* 发送按钮与设置齿轮同为无边框透明底：只靠图标色与 hover 底色区分主次。 */
QToolButton#ChatSend { background: transparent; }
QToolButton#ChatSend:hover { background: $accent_soft; }
QToolButton#ChatSend:pressed { background: $accent_soft_strong; }
QToolButton#ChatSend:disabled { background: transparent; }
/* 对话 / 画图模式胶囊：元宝式全圆角开关，选中回到强调色系。
   左右内边距与文字 / 图标排布由 ToggleChip 自绘，避免 QToolButton 自带 label
   布局在选中态偏左；这里只负责底色、圆角与选中 / 悬停配色。 */
QToolButton#ToggleChip {
    background: $surface_hover;
    border: none;
    border-radius: 15px;
    padding: 0;
    color: $muted;
    /* QSS 的 min / max-height 算内容高：必须同时把通用 QToolButton 的
       上下 padding 归零，否则胶囊会被撑到 30 + 8 = 38px，圆角不再成胶囊。 */
    min-height: 30px;
    max-height: 30px;
    font-weight: 500;
}
QToolButton#ToggleChip[tall="true"] {
    min-height: 34px;
    max-height: 34px;
    border-radius: 17px;
}
QToolButton#ToggleChip:hover { background: $surface_active; color: $text; }
QToolButton#ToggleChip:checked {
    background: $accent_soft;
    color: $accent;
}
QToolButton#ToggleChip:checked:hover { background: $accent_soft_strong; }
/* 禁用态放在 checked 之后：同特异性下后者生效，避免选中胶囊仍然亮着强调色。 */
QToolButton#ToggleChip:disabled { background: $disabled_bg; color: $disabled_fg; }
QToolButton#ToggleChip:checked:disabled { background: $disabled_bg; color: $disabled_fg; }
QPushButton#ChoiceCard {
    background: $surface;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 0;
    text-align: left;
    min-height: 72px;
    max-height: 72px;
}
QPushButton#ChoiceCard:hover { background: $surface_hover; border-color: $border_strong; }
QPushButton#ChoiceCard:checked { background: $accent_soft_strong; border-color: $accent; }
/* 存储卡片（llmw 设置页，参考 Steam 的磁盘库）：
   并排的磁盘卡 + 末尾虚线添加卡；卡片只描述存储位置，不抢标题层级。 */
QFrame#DiskCard {
    background: $background;
    border: 1px solid $border;
    border-radius: ${radius}px;
}
QFrame#DiskCard:hover { border-color: $border_strong; }
QLabel#DiskDrive { color: $text; font-size: ${font_heading}pt; font-weight: 700; }
QLabel#DiskMeta { color: $muted; font-size: ${font_small}pt; }
QLabel#DiskPath { color: $text; font-size: ${font_small}pt; }
QProgressBar#DiskBar { background: $surface_hover; border: none; border-radius: 3px; }
QProgressBar#DiskBar::chunk { background: $accent; border-radius: 3px; }
QPushButton#DiskAdd {
    background: transparent;
    border: 1px dashed $border_strong;
    border-radius: ${radius}px;
    color: $muted;
    padding: 8px;
}
QPushButton#DiskAdd:hover { background: $surface_hover; border-color: $accent; color: $text; }
QPushButton#ScanTaskAdd {
    background: transparent;
    border: 1px dashed $border_strong;
    border-radius: ${radius}px;
    color: $muted;
    padding: 0 14px;
    min-height: 54px;
    max-height: 54px;
    font-weight: 600;
    text-align: left;
}
QPushButton#ScanTaskAdd:hover { background: $surface_hover; border-color: $accent; color: $accent; }

QLabel#ChoiceTitle {
    background: transparent;
    color: $text;
    font-size: ${font_body}pt;
    font-weight: 600;
}
QLabel#ChoiceHint {
    background: transparent;
    color: $muted;
    font-size: ${font_small}pt;
}
QPushButton#ChoiceCard[tone="danger"] { border-color: $danger; background: $danger_soft; }
QPushButton#ChoiceCard[tone="accent"] { border-color: $accent; background: $accent_soft; }
QPushButton#ChoiceCard[tone="success"] { border-color: $success; background: $success_soft; }
QPushButton#ChoiceCard[tone="warning"] { border-color: $warning; background: $warning_soft; }
QPushButton#ChoiceCard[tone="muted"] { border-color: $border_strong; background: $surface_active; }
QPushButton#ChoiceCard[tone="danger"]:checked,
QPushButton#ChoiceCard[tone="accent"]:checked,
QPushButton#ChoiceCard[tone="success"]:checked,
QPushButton#ChoiceCard[tone="warning"]:checked,
QPushButton#ChoiceCard[tone="muted"]:checked { border-width: 2px; }
QPushButton#ChoiceCard[tone="danger"]:checked { background: $danger_soft_strong; }
QPushButton#ChoiceCard[tone="accent"]:checked { background: $accent_soft_strong; }
QPushButton#ChoiceCard[tone="success"]:checked { background: $success_soft_strong; }
QPushButton#ChoiceCard[tone="warning"]:checked { background: $warning_soft_strong; }
QPushButton#ChoiceCard[tone="muted"]:checked { background: $border; }
QPushButton#ChoiceCard[tone="danger"] QLabel#ChoiceTitle { color: $danger; }
QPushButton#ChoiceCard[tone="accent"] QLabel#ChoiceTitle { color: $accent; }
QPushButton#ChoiceCard[tone="success"] QLabel#ChoiceTitle { color: $success; }
QPushButton#ChoiceCard[tone="warning"] QLabel#ChoiceTitle { color: $warning; }
QPushButton#ChoiceCard[tone="muted"] QLabel#ChoiceTitle { color: $text; }
QPushButton#Ghost {
    background: transparent;
    border: 1px solid $border;
    color: $muted;
    padding: 0 ${control_pad_x};
}
QPushButton#Ghost:hover {
    background: $surface_hover;
    border-color: $border_strong;
    color: $text;
}
QPushButton#Ghost:pressed { background: $surface_active; }
QPushButton#Ghost:disabled { color: $disabled_fg; border-color: $disabled_border; background: transparent; }
/* 选中行卡底色更深，Ghost 默认的 border 与底色接近；换成强调色边框，保证配置等
   次要动作在选中行里仍能看见边框。这是行卡 + Ghost 的公共契约，各应用无需重复写。 */
QFrame#RecommendationRow[selected="true"] QPushButton#Ghost,
QFrame#EngineRow[selected="true"] QPushButton#Ghost,
QFrame[itemRow="true"][selected="true"] QPushButton#Ghost {
    border-color: $accent;
    color: $text;
}
QFrame#RecommendationRow[selected="true"] QPushButton#Ghost:hover,
QFrame#EngineRow[selected="true"] QPushButton#Ghost:hover,
QFrame[itemRow="true"][selected="true"] QPushButton#Ghost:hover {
    border-color: $accent_hover;
    background: $surface_hover;
}
QFrame#RecommendationRow[selected="true"] QPushButton#Ghost:disabled,
QFrame#EngineRow[selected="true"] QPushButton#Ghost:disabled,
QFrame[itemRow="true"][selected="true"] QPushButton#Ghost:disabled {
    border-color: $disabled_border;
    color: $disabled_fg;
}
QPushButton#FilterChip {
    background: $surface;
    border: 1px solid $border;
    border-radius: 12px;
    color: $muted;
    font-weight: 600;
}
QPushButton#FilterChip:hover { border-color: $border_strong; color: $text; }
QPushButton#FilterChip:checked {
    background: $accent_soft_strong;
    border-color: $accent;
    color: $text;
}
QPushButton#Link {
    background: transparent;
    border: none;
    color: $accent;
    padding: 0 2px;
    min-height: 0;
    max-height: none;
    font-size: ${font_small}pt;
    text-align: left;
}
QPushButton#Link:hover { color: $accent_hover; }
QPushButton#Link:pressed { color: $accent_pressed; }
QPushButton#Link:disabled { color: $disabled_fg; }
QPushButton#FilterChip:disabled {
    background: $disabled_bg;
    border-color: $disabled_border;
    color: $disabled_fg;
}

QToolButton { border: 1px solid $border; border-radius: 6px; background: $surface_hover; padding: 4px 8px; }
QToolButton:hover { background: $surface_active; }
QToolButton:disabled { background: $disabled_bg; border-color: $disabled_border; color: $disabled_fg; }
QToolButton#Collapsible {
    border: none;
    background: transparent;
    color: $muted;
    font-weight: 600;
    text-align: left;
    padding: 4px 0;
}
QToolButton#Collapsible:hover { color: $text; }
QToolButton#IconButton {
    background: transparent;
    border: 1px solid $border;
    border-radius: 6px;
    color: $muted;
    padding: 0;
    min-width: $control_height;
    max-width: $control_height;
    min-height: $control_height;
    max-height: $control_height;
    font-size: ${font_heading}pt;
}
QToolButton#IconButton:hover { background: $surface_hover; color: $text; }
QToolButton#IconButton:checked { background: $accent_soft_strong; color: $accent; border-color: $accent; }
QToolButton#IconButton:disabled { background: transparent; border-color: $disabled_border; color: $disabled_fg; }
QToolButton#IconButton:checked:disabled { background: $disabled_bg; color: $disabled_fg; border-color: $disabled_border; }
QPushButton#NavButton {
    background: transparent;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 0;
    color: $muted;
    text-align: left;
    padding: 0 12px;
    min-height: 36px;
    max-height: 36px;
    font-size: ${font_heading}pt;
}
QPushButton#NavButton[collapsed="true"] { padding: 0; text-align: center; }
QPushButton#NavButton:hover { background: $surface_hover; color: $text; }
QPushButton#NavButton:checked {
    background: $surface_active;
    color: $accent;
    border-left: 3px solid $accent;
    font-weight: 600;
}
QPushButton#NavButton:disabled { background: transparent; color: $disabled_fg; border-left-color: transparent; }
QToolButton#SidebarToggle {
    background: transparent;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 0;
    color: $muted;
    text-align: left;
    padding: 0 12px;
    min-height: 36px;
    max-height: 36px;
}
QToolButton#SidebarToggle:hover { background: $surface_hover; color: $text; }
QToolButton#SidebarToggle:disabled { background: transparent; color: $disabled_fg; }
QToolButton#Collapsible:disabled { color: $disabled_fg; }

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 0 ${control_pad_x_input};
    min-height: $control_height;
    max-height: $control_height;
    selection-background-color: $select_bg;
    selection-color: $select_fg;
}
QPlainTextEdit, QTextEdit {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: $select_bg;
    selection-color: $select_fg;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus { border-color: $accent; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    color: $disabled_fg;
    background: $disabled_bg;
    border-color: $disabled_border;
}
QLineEdit[readOnly="true"] { color: $muted; background: $surface_hover; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: $surface;
    border: 1px solid $border;
    selection-background-color: $select_bg;
    selection-color: $select_fg;
    outline: none;
}
QPlainTextEdit#Log, QTextEdit#Log {
    background: $log_bg;
    color: $log_fg;
    font-family: "$mono_family";
    font-size: ${font_small}pt;
    border: 1px solid $border;
    border-radius: 6px;
}

QTableView, QTreeView, QListView, QTreeWidget, QTableWidget {
    background: $surface;
    alternate-background-color: $stripe;
    color: $text;
    gridline-color: $border;
    border: 1px solid $border;
    border-radius: 6px;
    selection-background-color: $select_bg;
    selection-color: $select_fg;
    outline: none;
}
QTableView::item, QTreeView::item, QTreeWidget::item, QTableWidget::item { padding: 3px 4px; }
QTableView::item:selected, QTreeView::item:selected, QTreeWidget::item:selected,
QTableWidget::item:selected { background: $select_bg; color: $select_fg; }
QHeaderView::section {
    background: $surface_hover;
    color: $muted;
    padding: 5px 6px;
    border: none;
    border-right: 1px solid $border;
    border-bottom: 1px solid $border;
}
QTableCornerButton::section { background: $surface_hover; border: none; }
QFrame#Card QTableView, QFrame#Card QTreeView, QFrame#Card QListView,
QFrame#Card QTreeWidget, QFrame#Card QTableWidget {
    background: transparent;
    border: none;
    border-radius: 0;
}
/* 行卡展开区里的表格（如模型文件列表）：与行卡 / 详情区同底色，不另起一个盒子。 */
QTableWidget#ModelFiles {
    background: transparent;
    alternate-background-color: transparent;
    border: none;
    border-radius: 0;
}

QTabWidget::pane { border: none; background: transparent; }
QTabBar::tab {
    background: $surface;
    color: $muted;
    border: 1px solid $border;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 6px 16px;
    margin-right: 2px;
}
QTabBar::tab:selected { background: $surface_active; color: $text; font-weight: 600; }
QTabBar::tab:hover { color: $text; }
/* 药丸页签（运行页「对话 / 编程 / 画图」、模型行展开的「详细信息 / 文件列表」）：
   无整条基线，与内容同底色衔接。 */
QTabWidget#PillTabs::pane { border: none; background: transparent; top: 4px; }
/* 行卡展开区里的页签：不另起底色，跟着行卡走（选中时也一起变成选中底）。
   QTabWidget 内部的 QStackedWidget 默认按窗口底色填充，必须显式置透明，
   否则详情区会露出一块与行卡不同的底色。 */
QTabWidget#RowTabs { background: transparent; }
QTabWidget#RowTabs::pane { border: none; background: transparent; top: 4px; }
QTabWidget#RowTabs QStackedWidget { background: transparent; }
QTabBar#PillTabBar { background: transparent; border: none; }
QTabBar#PillTabBar::tab {
    background: transparent;
    color: $muted;
    border: 1px solid transparent;
    border-radius: 13px;
    padding: 3px 14px;
    margin: 0 6px 4px 0;
    min-height: 22px;
}
QTabBar#PillTabBar::tab:selected {
    background: $surface_active;
    color: $text;
    border-color: $border;
    font-weight: 600;
}
QTabBar#PillTabBar::tab:hover:!selected { background: $surface_hover; color: $text; }

QProgressBar {
    background: $surface_hover;
    border: 1px solid $border;
    border-radius: 5px;
    color: $text;
    text-align: center;
    height: 10px;
}
QProgressBar::chunk { background: $accent; border-radius: 4px; }
QWidget#LoadingBar {
    background: $surface_hover;
    border: 1px solid $border;
    border-radius: 6px;
}
QLabel#LoadingText { color: $muted; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: $border_strong; border-radius: 5px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: $accent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: $border_strong; border-radius: 5px; min-width: 28px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QCheckBox, QRadioButton { color: $text; spacing: 6px; }
QCheckBox::indicator, QRadioButton::indicator { width: 14px; height: 14px; }
QCheckBox::indicator:unchecked { border: 1px solid $border_strong; border-radius: 3px; background: $surface; }
QCheckBox::indicator:checked { border: 1px solid $accent; border-radius: 3px; background: $accent; }
QToolTip { background: $surface_active; color: $text; border: 1px solid $border; padding: 4px 6px; }
QFrame#ActionBar {
    background: $background;
    border-top: 1px solid $border;
}
/* 通用分栏手柄：常态透明，hover / 拖动才浮出强调色；需要常驻线的页面由两侧
   卡片自身边框承担，不再画一条贯穿整窗的粗线。 */
QSplitter::handle { background: transparent; }
QSplitter::handle:hover { background: $accent_soft; }
QSplitter::handle:pressed { background: $accent_soft_strong; }
QFrame#Sidebar { background: $surface; border-right: 1px solid $border; }
QListWidget[rowList="true"] { background: transparent; border: none; outline: 0; }
QListWidget[rowList="true"]::item {
    background: transparent;
    border: none;
    padding: 0;
}
/* 行卡外观：可展开行卡（共享 `ItemRow`）用 itemRow 属性选，不按应用分别开对象名；
   行列表容器（共享 `RowListWidget`）同理用 rowList 属性选。
   概览推荐 / 运行中模型 / 引擎行是另一类紧凑行卡，沿用各自的对象名。 */
QFrame#RecommendationRow, QFrame#RunningModelRow, QFrame#EngineRow,
QFrame[itemRow="true"] {
    background: $surface;
    border: 1px solid $border;
    border-radius: 8px;
}
/* 行卡动作区与文字区之间的细分隔线：只在有动作时出现。
   常态用较强边框色；选中行底色更深，换成强调色才看得清。 */
QFrame#RowActionDivider {
    background: $border_strong;
    border: none;
    margin: 0 2px;
}
QFrame#RowActionDivider[selected="true"] { background: $accent; }
/* 行卡右侧数值摘要：比副标题更小、加粗，与动作按钮拉开层级。 */
QLabel#RowMetrics {
    color: $muted;
    background: transparent;
    font-size: ${font_small}pt;
    font-weight: 600;
}
/* 行内动作按钮收紧左右内边距：一行可能要放 2–3 个按钮 + 数值 + 箭头。 */
QFrame[itemRow="true"] QPushButton { padding: 0 10px; }
QFrame[itemRow="true"] QPushButton#Ghost { padding: 0 8px; }
QFrame#RecommendationRow[band="excellent"] { background: $success_soft; border-color: $success; }
QFrame#RecommendationRow[band="good"] { background: $accent_soft; border-color: $accent; }
QFrame#RecommendationRow[band="fair"] { background: $warning_soft; border-color: $warning; }
QFrame#RecommendationRow[band="limited"] { background: $danger_soft; border-color: $danger; }
QFrame#RecommendationRow:hover, QFrame#EngineRow:hover,
QFrame[itemRow="true"]:hover { border-width: 2px; }
QFrame#RecommendationRow[selected="true"], QFrame#EngineRow[selected="true"],
QFrame[itemRow="true"][selected="true"] {
    border-width: 2px;
}
QFrame#RecommendationRow[band="excellent"][selected="true"] { background: $success_soft_strong; }
QFrame#RecommendationRow[band="good"][selected="true"] { background: $accent_soft_strong; }
QFrame#RecommendationRow[band="fair"][selected="true"] { background: $warning_soft_strong; }
QFrame#RecommendationRow[band="limited"][selected="true"] { background: $danger_soft_strong; }
QFrame#EngineRow[selected="true"], QFrame[itemRow="true"][selected="true"] {
    background: $accent_soft_strong;
    border-color: $select_bg;
}
QLabel#RecommendationName, QLabel#ItemRowName, QLabel#EngineItemName {
    font-size: ${font_heading}pt;
    font-weight: 600;
}
/* 侧边栏页按钮上的数量徽标：半透明强调色底 + 强调色数字的圆点。 */
QLabel#NavBadge {
    background: $accent_soft_strong;
    color: $accent;
    border-radius: 9px;
    font-size: ${font_small}pt;
    font-weight: 700;
}
QPushButton#InstalledButton, QPushButton#ConvertedButton {
    color: $success;
    background: $success_soft;
    border: 1px solid $success;
    font-weight: 600;
}
/* 「已安装 / 已转换」按钮的禁用态是完成标记，不是不可用：保留成功色，
   只用更淡的边框弱化可点击感。 */
QPushButton#InstalledButton:disabled, QPushButton#ConvertedButton:disabled {
    color: $success;
    background: $success_soft;
    border: 1px solid $success_soft_strong;
}
QLabel#DownloadProgress {
    color: $warning;
    background: $warning_soft;
    border: 1px solid $warning_soft_strong;
}
QLabel#QuantTag {
    color: $accent;
    background: $accent_soft;
    border: 1px solid $accent_soft_strong;
}
QLabel#ScoreBadge {
    border: 2px solid $border;
    border-radius: 22px;
    font-size: ${font_heading}pt;
    font-weight: 700;
}
QLabel#ScoreBadge[band="excellent"] { color: $on_accent; background: $success; border-color: $success; }
QLabel#ScoreBadge[band="good"] { color: $on_accent; background: $accent; border-color: $accent; }
QLabel#ScoreBadge[band="fair"] { color: $on_accent; background: $warning; border-color: $warning; }
QLabel#ScoreBadge[band="limited"] { color: $on_accent; background: $danger; border-color: $danger; }
"""
)

_QSS = Template(_QSS.template + HELP_QSS)
