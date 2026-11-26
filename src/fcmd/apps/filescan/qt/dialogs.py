"""filescan 模态对话框：白名单类型、规则与组合式表达式编辑。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.filescan import filescanw as fw
from fcmd.apps.filescan._metadata import METADATA_KEYS, METADATA_LABELS
from fcmd.apps.filescan._rules import (
    DEFAULT_SEVERITY,
    MATCH_DICTIONARY,
    MATCH_ENTROPY,
    MATCH_FILETYPE,
    MATCH_HASH,
    MATCH_KEYWORD,
    MATCH_METADATA,
    MATCH_REGEX,
    MATCH_SIZE,
    MATCH_TIME,
    SEVERITY_LABELS,
    SEVERITY_LEVELS,
    SIZE_BETWEEN,
    SIZE_LT,
    SIZE_OP_LABELS,
    SIZE_OPS,
    TARGET_ARCHIVE_PATH,
    TARGET_CONTENT,
    TARGET_FILENAME,
    TARGET_PATH,
    TIME_BEFORE,
    TIME_BETWEEN,
    TIME_MODIFIED,
    TIME_OP_LABELS,
    TIME_OPS,
    TIME_TARGET_LABELS,
    TIME_TARGETS,
    VALIDATOR_LABELS,
    VALIDATORS,
    Expression,
    Matcher,
    Rule,
    RuleSet,
    validate_expression,
)
from fcmd.apps.filescan.qt._common import _collapse_multiline, _multiline_editor
from fcmd.gui.widgets import (
    button,
    hint_label,
    messages,
)

_EXT_HINT = "后缀以逗号分隔，如 exe,dll；留空表示按扩展名匹配"

__all__ = ["ExpressionDialog", "RuleDialog", "WhitelistDialog"]


class WhitelistDialog(QDialog):  # pragma: no cover - 模态对话框
    """新增 / 编辑白名单类型：类型名 + 后缀串，提交时在窗口内联校验。"""

    def __init__(
        self,
        parent: QWidget,
        *,
        label: str = "",
        exts: tuple[str, ...] = (),
        taken: tuple[str, ...] = (),
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑白名单类型" if label else "新增白名单类型")
        self.setMinimumWidth(680)
        self._taken = set(taken)
        self._original = label
        self._result: tuple[str, tuple[str, ...]] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("类型"), 0, 0, Qt.AlignmentFlag.AlignRight)
        self._label = QLineEdit(label, self)
        grid.addWidget(self._label, 0, 1)
        grid.addWidget(QLabel("后缀"), 1, 0, Qt.AlignmentFlag.AlignRight)
        self._exts = QLineEdit(",".join(exts), self)
        self._exts.setPlaceholderText(_EXT_HINT)
        grid.addWidget(self._exts, 1, 1)
        self._hint = hint_label(_EXT_HINT)
        grid.addWidget(self._hint, 2, 0, 1, 2)
        outer.addLayout(grid)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        confirm = button("确定", kind="primary")
        confirm.clicked.connect(self._commit)
        actions.addWidget(confirm)
        outer.addLayout(actions)
        self._label.selectAll()
        self._label.setFocus()

    def _fail(self, message: str) -> None:
        self._hint.setText(message)

    def _commit(self) -> None:
        label = self._label.text().strip()
        if not label:
            self._fail("请填写类型名称")
            return
        if label != self._original and label in self._taken:
            self._fail(f"类型「{label}」已存在")
            return
        valid, invalid = fw.parse_and_validate_ext_line(self._exts.text())
        if invalid:
            self._fail(f"无效条目：{'、'.join(invalid)}")
            return
        if not valid:
            self._fail("请至少填写一个有效后缀")
            return
        self._result = (label, valid)
        self.accept()

    def value(self) -> tuple[str, tuple[str, ...]] | None:
        """提交结果；取消时为 ``None``。"""
        return self._result


_KIND_LABELS: dict[str, str] = {
    MATCH_KEYWORD: "关键词",
    MATCH_REGEX: "正则",
    MATCH_TIME: "时间",
    MATCH_SIZE: "文件大小",
    MATCH_FILETYPE: "文件类型",
    MATCH_METADATA: "文档元数据",
    MATCH_DICTIONARY: "自定义词典",
    MATCH_HASH: "文件哈希",
    MATCH_ENTROPY: "高熵串",
}
"""规则类型下拉的中文名称（内部仍用稳定 id）。"""


class RuleDialog(QDialog):  # pragma: no cover - 模态对话框
    """新增 / 编辑单条规则；校验（id 唯一、匹配器合法）由窗口侧统一执行。"""

    def __init__(self, parent: QWidget, rule: Rule | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑规则" if rule is not None else "新增规则")
        self.setMinimumWidth(660)
        self._result: Rule | None = None

        current_kind = rule.matcher.kind if rule is not None else MATCH_KEYWORD
        # 关键词 / 正则各自记住大小写开关：新增时用类型默认值（关键词不区分、正则区分）。
        self._case_by_kind = {MATCH_KEYWORD: False, MATCH_REGEX: True}
        if rule is not None and current_kind in self._case_by_kind:
            self._case_by_kind[current_kind] = rule.matcher.is_case_sensitive()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        self._id = QLineEdit(rule.id if rule else "", self)
        self._name = QLineEdit(rule.name if rule else "", self)
        self._group = QLineEdit(rule.group if rule else "自定义", self)
        self._kind = QComboBox(self)
        for kind, label in _KIND_LABELS.items():
            self._kind.addItem(label, kind)
        self._kind.setCurrentIndex(max(self._kind.findData(current_kind), 0))
        self._text = _multiline_editor(placeholder="关键词原文，如 password")
        self._text.setPlainText(rule.matcher.text if rule else "")
        self._pattern = _multiline_editor(placeholder="正则表达式，如 \\d{11}；可换行，保存时合并为单行")
        self._pattern.setPlainText(rule.matcher.pattern if rule else "")
        self._severity = QComboBox(self)
        self._severity.addItems([SEVERITY_LABELS[level] for level in SEVERITY_LEVELS])
        self._severity.setCurrentIndex(SEVERITY_LEVELS.index(rule.severity if rule else DEFAULT_SEVERITY))
        self._target = QComboBox(self)
        self._target.addItem("文件内容", TARGET_CONTENT)
        self._target.addItem("文件名", TARGET_FILENAME)
        self._target.addItem("完整路径", TARGET_PATH)
        self._target.addItem("归档成员路径", TARGET_ARCHIVE_PATH)
        current_target = (
            rule.matcher.target if rule and rule.matcher.target in (TARGET_CONTENT, TARGET_FILENAME) else TARGET_CONTENT
        )
        self._target.setCurrentIndex(max(self._target.findData(current_target), 0))
        self._time_field = QComboBox(self)
        for target in TIME_TARGETS:
            self._time_field.addItem(TIME_TARGET_LABELS[target], target)
        current_time_field = rule.matcher.target if rule and rule.matcher.target in TIME_TARGETS else TIME_MODIFIED
        self._time_field.setCurrentIndex(max(self._time_field.findData(current_time_field), 0))
        self._time_op = QComboBox(self)
        for op in TIME_OPS:
            self._time_op.addItem(TIME_OP_LABELS[op], op)
        current_op = rule.matcher.compare if rule and rule.matcher.kind == MATCH_TIME else TIME_BEFORE
        self._time_op.setCurrentIndex(max(self._time_op.findData(current_op), 0))
        self._time_value = QLineEdit(rule.matcher.value if rule and rule.matcher.kind == MATCH_TIME else "", self)
        self._time_value.setPlaceholderText("2026-01-01T00:00:00 或 epoch 秒")
        self._time_end = QLineEdit(rule.matcher.value2 if rule and rule.matcher.kind == MATCH_TIME else "", self)
        self._time_end.setPlaceholderText("结束时间（介于时必填）")
        self._size_op = QComboBox(self)
        for op in SIZE_OPS:
            self._size_op.addItem(SIZE_OP_LABELS[op], op)
        current_size_op = rule.matcher.compare if rule and rule.matcher.kind == MATCH_SIZE else SIZE_LT
        self._size_op.setCurrentIndex(max(self._size_op.findData(current_size_op), 0))
        self._size_value = QLineEdit(rule.matcher.value if rule and rule.matcher.kind == MATCH_SIZE else "", self)
        self._size_value.setPlaceholderText("如 10MB / 1048576")
        self._size_end = QLineEdit(rule.matcher.value2 if rule and rule.matcher.kind == MATCH_SIZE else "", self)
        self._size_end.setPlaceholderText("结束大小（介于时必填）")
        self._filetype_value = QLineEdit(
            rule.matcher.value if rule and rule.matcher.kind == MATCH_FILETYPE else "", self
        )
        self._filetype_value.setPlaceholderText(" pdf,zip,exe（逗号分隔）")
        self._metadata_key = QComboBox(self)
        for key in METADATA_KEYS:
            self._metadata_key.addItem(METADATA_LABELS[key], key)
        current_key = rule.matcher.value if rule and rule.matcher.kind == MATCH_METADATA else METADATA_KEYS[0]
        self._metadata_key.setCurrentIndex(max(self._metadata_key.findData(current_key), 0))
        self._metadata_text = QLineEdit(rule.matcher.text if rule and rule.matcher.kind == MATCH_METADATA else "", self)
        self._metadata_text.setPlaceholderText("元数据中包含的文本（忽略大小写）")
        self._extra = _multiline_editor(placeholder="每行一个词条 / SHA-256")
        if rule and rule.matcher.kind in (MATCH_DICTIONARY, MATCH_HASH):
            self._extra.setPlainText(rule.matcher.value)
        self._entropy_value = QLineEdit(
            rule.matcher.value if rule and rule.matcher.kind == MATCH_ENTROPY else "4.5", self
        )
        self._entropy_value.setPlaceholderText("熵阈值 (0, 8]，默认 4.5")
        self._entropy_min = QLineEdit(
            rule.matcher.value2 if rule and rule.matcher.kind == MATCH_ENTROPY else "24", self
        )
        self._entropy_min.setPlaceholderText("最小长度，默认 24")
        self._validator = QComboBox(self)
        for validator in VALIDATORS:
            self._validator.addItem(VALIDATOR_LABELS[validator], validator)
        current_validator = rule.matcher.validator if rule else ""
        self._validator.setCurrentIndex(max(self._validator.findData(current_validator), 0))

        def _label(text: str) -> QLabel:
            label = QLabel(text, self)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return label

        for row, (text, widget) in enumerate(
            (
                ("规则 id", self._id),
                ("名称", self._name),
                ("分组", self._group),
                ("类型", self._kind),
            )
        ):
            grid.addWidget(_label(text), row, 0)
            grid.addWidget(widget, row, 1)
        # 关键词 / 正则共用第 4 行：切换类型时只显示对应标签与输入框。
        self._text_label = _label("关键词")
        self._pattern_label = _label("正则")
        grid.addWidget(self._text_label, 4, 0)
        grid.addWidget(self._text, 4, 1)
        grid.addWidget(self._pattern_label, 4, 0)
        grid.addWidget(self._pattern, 4, 1)
        self._kind_hint = hint_label("", wrap=True)
        grid.addWidget(self._kind_hint, 5, 1)
        grid.addWidget(_label("严重级"), 6, 0)
        grid.addWidget(self._severity, 6, 1)
        self._target_label = _label("匹配对象")
        grid.addWidget(self._target_label, 7, 0)
        grid.addWidget(self._target, 7, 1)
        self._time_labels: dict[str, QLabel] = {}
        for row, (text, widget) in enumerate(
            (
                ("时间字段", self._time_field),
                ("时间条件", self._time_op),
                ("起始时间", self._time_value),
                ("结束时间", self._time_end),
            ),
            start=8,
        ):
            label = _label(text)
            self._time_labels[text] = label
            grid.addWidget(label, row, 0)
            grid.addWidget(widget, row, 1)
        self._size_labels: dict[str, QLabel] = {}
        for row, (text, widget) in enumerate(
            (
                ("大小条件", self._size_op),
                ("起始大小", self._size_value),
                ("结束大小", self._size_end),
            ),
            start=12,
        ):
            label = _label(text)
            self._size_labels[text] = label
            grid.addWidget(label, row, 0)
            grid.addWidget(widget, row, 1)
        self._validator_label = _label("校验器")
        grid.addWidget(self._validator_label, 15, 0)
        grid.addWidget(self._validator, 15, 1)
        self._filetype_label = _label("文件类型")
        grid.addWidget(self._filetype_label, 16, 0)
        grid.addWidget(self._filetype_value, 16, 1)
        self._metadata_key_label = _label("元数据字段")
        grid.addWidget(self._metadata_key_label, 17, 0)
        grid.addWidget(self._metadata_key, 17, 1)
        self._metadata_text_label = _label("元数据文本")
        grid.addWidget(self._metadata_text_label, 18, 0)
        grid.addWidget(self._metadata_text, 18, 1)
        self._extra_label = _label("词条 / 哈希")
        grid.addWidget(self._extra_label, 19, 0)
        grid.addWidget(self._extra, 19, 1)
        self._entropy_value_label = _label("熵阈值")
        grid.addWidget(self._entropy_value_label, 20, 0)
        grid.addWidget(self._entropy_value, 20, 1)
        self._entropy_min_label = _label("最小长度")
        grid.addWidget(self._entropy_min_label, 21, 0)
        grid.addWidget(self._entropy_min, 21, 1)
        outer.addLayout(grid)

        flags = QHBoxLayout()
        flags.setContentsMargins(0, 0, 0, 0)
        flags.setSpacing(12)
        self._case = QCheckBox("区分大小写", self)
        self._case.setChecked(self._case_by_kind.get(current_kind, False))
        self._case.toggled.connect(self._remember_case)
        self._whole = QCheckBox("整词匹配", self)
        self._whole.setChecked(rule.matcher.whole_word if rule else False)
        self._whole.setToolTip("仅关键词类型可用；按词边界匹配，避免中文关键词被误判")
        self._enabled = QCheckBox("启用", self)
        self._enabled.setChecked(rule.enabled if rule else True)
        for widget in (self._case, self._whole, self._enabled):
            flags.addWidget(widget)
        flags.addSpacing(12)
        flags.addWidget(QLabel("最少命中", self))
        self._min_hits = QSpinBox(self)
        self._min_hits.setRange(1, 9999)
        self._min_hits.setValue(rule.min_hits if rule else 1)
        self._min_hits.setToolTip("同一文件内至少命中多少次才算该规则命中（默认 1）")
        flags.addWidget(self._min_hits)
        flags.addWidget(QLabel("最多命中", self))
        self._max_hits = QSpinBox(self)
        self._max_hits.setRange(0, 9999)
        self._max_hits.setSpecialValueText("不限")
        self._max_hits.setValue(rule.max_hits if rule else 0)
        self._max_hits.setToolTip("同一文件最多命中多少次；0 表示不限。超过上限视为规则未命中")
        flags.addWidget(self._max_hits)
        flags.addStretch(1)
        outer.addLayout(flags)

        self._kind.currentIndexChanged.connect(lambda _index: self._sync_kind_fields(self._current_kind))
        self._time_op.currentIndexChanged.connect(self._sync_time_end)
        self._size_op.currentIndexChanged.connect(self._sync_size_end)
        self._sync_kind_fields(current_kind)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        confirm = button("保存", kind="primary")
        confirm.clicked.connect(self._commit)
        actions.addWidget(confirm)
        outer.addLayout(actions)

    @property
    def _current_kind(self) -> str:
        data = self._kind.currentData()
        return data if isinstance(data, str) and data else MATCH_KEYWORD

    def _remember_case(self, checked: bool) -> None:
        kind = self._current_kind
        if kind in self._case_by_kind:
            self._case_by_kind[kind] = checked

    def _sync_time_end(self, *_args: object) -> None:
        """「介于」才显示结束时间；其它条件隐藏，避免误以为需要填。"""
        between = self._current_kind == MATCH_TIME and self._time_op.currentData() == TIME_BETWEEN
        self._time_end.setVisible(between)
        self._time_labels["结束时间"].setVisible(between)

    def _sync_size_end(self, *_args: object) -> None:
        """大小「介于」才显示结束大小。"""
        between = self._current_kind == MATCH_SIZE and self._size_op.currentData() == SIZE_BETWEEN
        self._size_end.setVisible(between)
        self._size_labels["结束大小"].setVisible(between)
        self._relayout()

    def _relayout(self) -> None:
        layout = self.layout()
        if layout is not None:
            layout.activate()
        if self.isVisible():
            self.adjustSize()

    def _sync_kind_fields(self, kind: str) -> None:  # noqa: PLR0912 - 逐类型显隐
        """按类型切换可见字段：关键词 / 正则 / 时间 / 大小各显示自己的输入项。"""
        keyword = kind == MATCH_KEYWORD
        regex = kind == MATCH_REGEX
        time_rule = kind == MATCH_TIME
        size_rule = kind == MATCH_SIZE
        filetype_rule = kind == MATCH_FILETYPE
        metadata_rule = kind == MATCH_METADATA
        dictionary_rule = kind == MATCH_DICTIONARY
        hash_rule = kind == MATCH_HASH
        entropy_rule = kind == MATCH_ENTROPY
        pattern_rule = keyword or regex
        self._text.setVisible(keyword)
        self._text_label.setVisible(keyword)
        self._pattern.setVisible(regex)
        self._pattern_label.setVisible(regex)
        self._whole.setVisible(keyword)
        self._case.setVisible(pattern_rule)
        self._target.setVisible(pattern_rule)
        self._target_label.setVisible(pattern_rule)
        self._validator.setVisible(regex)
        self._validator_label.setVisible(regex)
        self._filetype_value.setVisible(filetype_rule)
        self._filetype_label.setVisible(filetype_rule)
        for widget in (self._metadata_key, self._metadata_key_label, self._metadata_text, self._metadata_text_label):
            widget.setVisible(metadata_rule)
        for widget in (self._extra, self._extra_label):
            widget.setVisible(dictionary_rule or hash_rule)
        if dictionary_rule:
            self._extra.setPlaceholderText("每行一个词条（忽略大小写）")
        elif hash_rule:
            self._extra.setPlaceholderText("每行一个 64 位 SHA-256")
        for widget in (
            self._entropy_value,
            self._entropy_value_label,
            self._entropy_min,
            self._entropy_min_label,
        ):
            widget.setVisible(entropy_rule)
        for text in ("时间字段", "时间条件", "起始时间"):
            self._time_labels[text].setVisible(time_rule)
        self._time_field.setVisible(time_rule)
        self._time_op.setVisible(time_rule)
        self._time_value.setVisible(time_rule)
        self._sync_time_end()
        for text in ("大小条件", "起始大小"):
            self._size_labels[text].setVisible(size_rule)
        self._size_op.setVisible(size_rule)
        self._size_value.setVisible(size_rule)
        self._sync_size_end()
        self._case.blockSignals(True)
        self._case.setChecked(self._case_by_kind.get(kind, False))
        self._case.blockSignals(False)
        if time_rule:
            hint = "时间：按文件时间戳比较；创建时间在 Linux 上退化为 ctime（更接近元数据变更时间）。"
        elif size_rule:
            hint = "大小：按文件字节数比较，支持 10KB / 1.5MB / 2GB 或裸字节数；区间为半开 [起始, 结束)。"
        elif filetype_rule:
            hint = "文件类型：按魔数识别真实类型（逗号分隔，如 pdf,zip,exe），可发现改名文件。"
        elif metadata_rule:
            hint = "文档元数据：读取 docx/xlsx/pptx 的作者 / 公司 / 标题等字段并做包含匹配。"
        elif dictionary_rule:
            hint = "自定义词典：每行一个词条，命中任意词条即算该规则命中（忽略大小写）。"
        elif hash_rule:
            hint = "文件哈希：每行一个 SHA-256，按文件内容摘要精确匹配。"
        elif entropy_rule:
            hint = "高熵串：长度 ≥ 最小长度且 Shannon 熵 ≥ 阈值的随机 / 加密风格字符串。"
        elif keyword:
            hint = "关键词：按原文包含匹配，可勾选整词；默认不区分大小写。可换行，保存时合并为单行。"
        else:
            hint = "正则：按行逐条匹配（Python re 语法）；可选校验器过滤误报。可换行，保存时合并为单行。"
        self._kind_hint.setText(hint)
        # 显隐切换后让对话框高度跟随内容重新计算（隐藏字段不占空间）。
        self._relayout()

    def _commit(self) -> None:
        kind = self._current_kind
        if kind == MATCH_TIME:
            compare = self._time_op.currentData() or TIME_BEFORE
            matcher = Matcher(
                kind=kind,
                target=self._time_field.currentData() or TIME_MODIFIED,
                compare=compare,
                value=self._time_value.text().strip(),
                value2=self._time_end.text().strip() if compare == TIME_BETWEEN else "",
            )
        elif kind == MATCH_FILETYPE:
            matcher = Matcher(kind=kind, value=self._filetype_value.text().strip())
        elif kind == MATCH_METADATA:
            matcher = Matcher(
                kind=kind,
                value=self._metadata_key.currentData() or METADATA_KEYS[0],
                text=self._metadata_text.text().strip(),
            )
        elif kind in (MATCH_DICTIONARY, MATCH_HASH):
            matcher = Matcher(kind=kind, value=self._extra.toPlainText().strip())
        elif kind == MATCH_ENTROPY:
            matcher = Matcher(
                kind=kind,
                value=self._entropy_value.text().strip(),
                value2=self._entropy_min.text().strip() or "24",
            )
        elif kind == MATCH_SIZE:
            compare = self._size_op.currentData() or SIZE_LT
            matcher = Matcher(
                kind=kind,
                compare=compare,
                value=self._size_value.text().strip(),
                value2=self._size_end.text().strip() if compare == SIZE_BETWEEN else "",
            )
        else:
            matcher = Matcher(
                kind=kind,
                text=_collapse_multiline(self._text.toPlainText()) if kind == MATCH_KEYWORD else "",
                pattern=_collapse_multiline(self._pattern.toPlainText()) if kind == MATCH_REGEX else "",
                case_sensitive=self._case.isChecked(),
                whole_word=self._whole.isChecked() if kind == MATCH_KEYWORD else False,
                target=self._target.currentData() or TARGET_CONTENT,
                validator=self._validator.currentData() if kind == MATCH_REGEX else "",
            )
        self._result = Rule(
            id=self._id.text().strip(),
            name=self._name.text().strip(),
            matcher=matcher,
            group=self._group.text().strip() or "自定义",
            enabled=self._enabled.isChecked(),
            severity=SEVERITY_LEVELS[self._severity.currentIndex()],
            min_hits=self._min_hits.value(),
            max_hits=self._max_hits.value(),
        )
        self.accept()

    def value(self) -> Rule | None:
        """提交的规则对象；取消时为 ``None``。"""
        return self._result


_CONTEXT_ROLE = Qt.ItemDataRole.UserRole + 11
_CONTEXT_PADDING = 4
_PATH_ROLE = Qt.ItemDataRole.UserRole + 12
_MATCH_ROLE = Qt.ItemDataRole.UserRole + 13
_MATCHES_ROLE = Qt.ItemDataRole.UserRole + 14
_CHILDREN_ROLE = Qt.ItemDataRole.UserRole + 15
"""结果树的数据角色：上下文列内边距（像素）、文件路径、单条命中、文件全部命中、子行已构建。"""

_RESULT_PAGE_SIZE = 1000
"""结果树首批渲染的文件行数；超出部分用「加载更多结果」按需追加。"""


class ExpressionDialog(QDialog):  # pragma: no cover - 模态对话框
    """新增 / 编辑组合式：id、名称、表达式文本与启用状态，可插入规则 / 组合式 id。"""

    def __init__(self, parent: QWidget, ruleset: RuleSet, expression: Expression | None = None) -> None:
        super().__init__(parent)
        self._ruleset = ruleset
        self._result: Expression | None = None
        self.setWindowTitle("编辑组合式" if expression is not None else "新增组合式")
        self.setMinimumWidth(760)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        self._id = QLineEdit(expression.id if expression is not None else fw.new_expression_id(ruleset), self)
        grid.addWidget(QLabel("ID", self), 0, 0)
        grid.addWidget(self._id, 0, 1)
        self._name = QLineEdit(expression.name if expression is not None else "", self)
        grid.addWidget(QLabel("名称", self), 1, 0)
        grid.addWidget(self._name, 1, 1)
        self._expr = QLineEdit(expression.expression if expression is not None else "", self)
        self._expr.setPlaceholderText("例如：(pii or phone) and not allow_list")
        grid.addWidget(QLabel("表达式", self), 2, 0)
        grid.addWidget(self._expr, 2, 1)

        insert_row = QHBoxLayout()
        insert_row.setContentsMargins(0, 0, 0, 0)
        insert_row.setSpacing(6)
        self._insert_source = QComboBox(self)
        for rule in ruleset.rules:
            self._insert_source.addItem(f"规则：{rule.id}（{rule.name}）", rule.id)
        for item in ruleset.expressions:
            if expression is not None and item.id == expression.id:
                continue
            self._insert_source.addItem(f"组合式：{item.id}（{item.name}）", item.id)
        insert_row.addWidget(hint_label("插入 ID", wrap=False))
        insert_row.addWidget(self._insert_source, 1)
        insert_button = button("插入到表达式")
        insert_button.clicked.connect(self._insert_selected_id)
        insert_row.addWidget(insert_button)
        grid.addLayout(insert_row, 3, 1)
        outer.addLayout(grid)

        flags = QHBoxLayout()
        self._enabled = QCheckBox("启用（默认参与生效表达式）", self)
        self._enabled.setChecked(expression.enabled if expression is not None else True)
        flags.addWidget(self._enabled)
        flags.addStretch(1)
        validate_button = button("校验")
        validate_button.clicked.connect(self._validate)
        flags.addWidget(validate_button)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        flags.addWidget(cancel)
        confirm_button = button("保存", kind="primary")
        confirm_button.clicked.connect(self._commit)
        flags.addWidget(confirm_button)
        outer.addLayout(flags)

    def _candidate(self) -> Expression:
        return Expression(
            id=self._id.text().strip(),
            name=self._name.text().strip(),
            expression=self._expr.text().strip(),
            enabled=self._enabled.isChecked(),
        )

    def _checked(self) -> Expression:
        candidate = self._candidate()
        validate_expression(self._ruleset, candidate)
        return candidate

    def _insert_selected_id(self) -> None:
        target = self._insert_source.currentData()
        if not isinstance(target, str) or not target:
            return
        text = self._expr.text()
        cursor = max(0, min(self._expr.cursorPosition(), len(text)))
        before = "" if cursor == 0 or text[cursor - 1].isspace() or text[cursor - 1] == "(" else " "
        after = "" if cursor >= len(text) or text[cursor].isspace() or text[cursor] == ")" else " "
        inserted = f"{before}{target}{after}"
        self._expr.setText(f"{text[:cursor]}{inserted}{text[cursor:]}")
        self._expr.setCursorPosition(cursor + len(inserted))
        self._expr.setFocus()

    def _validate(self) -> None:
        try:
            self._checked()
        except ValueError as exc:
            messages.error(self, "组合式无效", str(exc))
            return
        messages.info(self, "组合式有效", "表达式解析通过")

    def _commit(self) -> None:
        try:
            self._result = self._checked()
        except ValueError as exc:
            messages.error(self, "组合式无效", str(exc))
            return
        self.accept()

    def value(self) -> Expression | None:
        """提交的组合式；取消时为 ``None``。"""
        return self._result
