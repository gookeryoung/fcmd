"""wslman 共享控件 / 段落（ADR 0046 P2/P3 迁移）。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from fcmd.apps.wslman._images import ImageEntry


@dataclass
class _DownloadTask:
    """镜像下载队列里的一项：独立取消标记、进度与状态，互不影响。

    ``state`` 取 ``queued`` / ``running`` / ``ok`` / ``failed`` / ``cancelled``：
    排队中也能单独取消；单项失败只影响自己，调度器会继续跑后面的项。
    """

    entry: ImageEntry
    cancel: threading.Event = field(default_factory=threading.Event)
    state: str = "queued"
    done: int = 0
    total: int | None = None
    message: str = ""

    @property
    def finished(self) -> bool:
        return self.state in ("ok", "failed", "cancelled")
