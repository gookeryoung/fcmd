"""cmder 对话框包：按功能拆分，统一从这里 import。"""

from fcmd.apps.cmder.qt.attrs import (
    AttributeDialog,
    BookmarkDialog,
)
from fcmd.apps.cmder.qt.commands import (
    ColorRulesDialog,
    UserCommandsDialog,
)
from fcmd.apps.cmder.qt.diff import (
    CompareDialog,
    TextDiffDialog,
)
from fcmd.apps.cmder.qt.openers import (
    ArchiveOptionsDialog,
    DuplicateDialog,
    FilterDialog,
    OpenWithDialog,
    SplitDialog,
)
from fcmd.apps.cmder.qt.queue import (
    TransferQueueDialog,
)
from fcmd.apps.cmder.qt.rename import (
    MultiRenameDialog,
)
from fcmd.apps.cmder.qt.search import (
    SearchDialog,
)
from fcmd.apps.cmder.qt.sync import (
    SyncDialog,
)

__all__ = [
    "ArchiveOptionsDialog",
    "AttributeDialog",
    "BookmarkDialog",
    "ColorRulesDialog",
    "CompareDialog",
    "DuplicateDialog",
    "FilterDialog",
    "MultiRenameDialog",
    "OpenWithDialog",
    "SearchDialog",
    "SplitDialog",
    "SyncDialog",
    "TextDiffDialog",
    "TransferQueueDialog",
    "UserCommandsDialog",
]
