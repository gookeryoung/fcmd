"""pptmake 编辑历史：基于不可变模板快照的撤销 / 重做栈。

模板（:class:`~fcmd.apps.pptmake._core.TemplateSpec`）是冻结 dataclass，每次编辑
都用 ``dataclasses.replace`` 生成新对象，因此撤销只需保存旧快照，不需要命令模式
或可变补丁。文本输入、滑杆这类连续动作通过 ``coalesce_key`` 合并：同一字段在
``coalesce_window`` 秒内的多次提交只占一条历史，撤销时一次退回到编辑前。

纯逻辑模块，不导入 Qt，CI 无显示环境可直接单测。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from fcmd.apps.pptmake._core import TemplateSpec

__all__ = ["DEFAULT_COALESCE_WINDOW", "DEFAULT_LIMIT", "TemplateHistory"]

DEFAULT_LIMIT: Final[int] = 100
"""历史栈上限；快照是冻结 dataclass，100 条的内存开销可忽略。"""

DEFAULT_COALESCE_WINDOW: Final[float] = 0.8
"""连续输入合并窗口（秒）：同一 ``coalesce_key`` 在此窗口内只记一条历史。"""


@dataclass(frozen=True, slots=True)
class _Snapshot:
    """一条历史快照：模板 + 触发这次变更的动作名。"""

    template: TemplateSpec
    label: str


class TemplateHistory:
    """模板快照历史：``record`` 记录变更，``undo`` / ``redo`` 返回目标快照。"""

    def __init__(self, template: TemplateSpec, *, limit: int = DEFAULT_LIMIT) -> None:
        if limit < 1:
            raise ValueError("历史栈上限必须为正整数")
        self._limit = limit
        self._current = template
        self._undo: list[_Snapshot] = []
        self._redo: list[_Snapshot] = []
        self._coalesce_key = ""
        self._coalesce_at = 0.0

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    @property
    def current(self) -> TemplateSpec:
        """当前模板快照（与窗口里的模板保持一致）。"""
        return self._current

    def can_undo(self) -> bool:
        """是否还有可撤销的历史。"""
        return bool(self._undo)

    def can_redo(self) -> bool:
        """是否还有可重做的历史。"""
        return bool(self._redo)

    def undo_label(self) -> str:
        """下一次撤销对应的动作名（无历史时为空串）。"""
        return self._undo[-1].label if self._undo else ""

    def redo_label(self) -> str:
        """下一次重做对应的动作名（无历史时为空串）。"""
        return self._redo[-1].label if self._redo else ""

    # ------------------------------------------------------------------ #
    # 记录 / 撤销 / 重做
    # ------------------------------------------------------------------ #
    def reset(self, template: TemplateSpec) -> None:
        """载入新模板：清空撤销 / 重做栈。"""
        self._current = template
        self._undo.clear()
        self._redo.clear()
        self._coalesce_key = ""
        self._coalesce_at = 0.0

    def record(
        self,
        template: TemplateSpec,
        label: str,
        *,
        coalesce_key: str = "",
        now: float | None = None,
        window: float = DEFAULT_COALESCE_WINDOW,
    ) -> bool:
        """记录一次变更；``template`` 与当前快照相同时返回 ``False``。

        同一 ``coalesce_key`` 且在 ``window`` 秒内的连续变更只保留第一条历史，
        后续变更直接更新当前快照，撤销时一步回到该字段编辑前。
        """
        if template == self._current:
            return False
        moment = time.monotonic() if now is None else now
        merge = (
            bool(coalesce_key) and coalesce_key == self._coalesce_key and 0.0 <= moment - self._coalesce_at <= window
        )
        if not merge:
            self._push_undo(_Snapshot(self._current, label))
        self._current = template
        self._redo.clear()
        self._coalesce_key = coalesce_key
        self._coalesce_at = moment
        return True

    def undo(self) -> TemplateSpec | None:
        """撤销一步并返回目标模板；无历史时返回 ``None``。"""
        if not self._undo:
            return None
        entry = self._undo.pop()
        self._redo.append(_Snapshot(self._current, entry.label))
        self._current = entry.template
        self._coalesce_key = ""
        return self._current

    def redo(self) -> TemplateSpec | None:
        """重做一步并返回目标模板；无历史时返回 ``None``。"""
        if not self._redo:
            return None
        entry = self._redo.pop()
        self._undo.append(_Snapshot(self._current, entry.label))
        self._current = entry.template
        self._coalesce_key = ""
        return self._current

    def _push_undo(self, snapshot: _Snapshot) -> None:
        """把旧快照压入撤销栈，超出上限时丢弃最旧的一条。"""
        self._undo.append(snapshot)
        if len(self._undo) > self._limit:
            del self._undo[0]
