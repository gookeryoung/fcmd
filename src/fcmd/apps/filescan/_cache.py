"""filescan 扫描结果缓存：SQLite 持久化、并发安全与损坏自愈。

设计口径：

- **存储**：单文件 SQLite（默认 ``${FCMD_HOME:-~/.fcmd}/cache/filescan-cache.db``），
  表 ``entries(cache_key, path, size, mtime_ns, encoding, matches)``，主键
  ``(cache_key, path)``；``cache_key`` 是规则与命中相关选项的指纹，不同配置共存。
- **复用条件**：同一路径的 ``size`` 与 ``mtime_ns`` 都与缓存一致，且 ``cache_key``
  一致才复用；文件内容变化若同时改变大小或修改时间即失效（缓存不做全量内容
  哈希，避免把复用变成又一次读盘）。
- **并发安全**：只有扫描的主进程打开连接并读写，进程池 / 线程池的 worker 只
  返回结果、不接触数据库；WAL + ``busy_timeout`` 允许多个扫描进程同时存在，
  写冲突超时即降级为「本次不写缓存」，不影响扫描。
- **损坏降级**：打开或读写遇到 ``sqlite3.DatabaseError`` 时关闭连接、删除
  数据库（含 ``-wal`` / ``-shm``）并重建；重建仍失败则本次禁用缓存，
  由调用方打印提示。任何缓存异常都不会中断扫描。

本模块只依赖标准库与同包的 ``_engine`` / ``_rules``，不导入 Tk。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fcmd.apps.filescan._engine import FileHit, RuleMatch
from fcmd.apps.filescan._rules import ruleset_to_mapping

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fcmd.apps.filescan._engine import ScanOptions
    from fcmd.apps.filescan._rules import RuleSet

__all__ = [
    "CACHE_SCHEMA_VERSION",
    "ScanCache",
    "cache_fingerprint",
    "default_cache_path",
]

CACHE_SCHEMA_VERSION = 1
"""缓存表结构版本；与文件内记录不一致时重建。"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    cache_key TEXT NOT NULL,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    encoding TEXT NOT NULL,
    has_hit INTEGER NOT NULL,
    matches TEXT NOT NULL,
    PRIMARY KEY (cache_key, path)
);
"""

_BUSY_TIMEOUT_MS = 5000
"""等待其它扫描进程释放写锁的毫秒数，超时即降级为本次不写缓存。"""

_LOOKUP_BATCH = 400
"""单条查询的最大路径数，避开 SQLite 的变量上限。"""


def default_cache_path() -> Path:
    """返回默认缓存文件路径：``${FCMD_HOME:-~/.fcmd}/cache/filescan-cache.db``。"""
    home = os.environ.get("FCMD_HOME")
    base = Path(home) if home else Path.home() / ".fcmd"
    return base / "cache" / "filescan-cache.db"


def _fingerprint_payload(ruleset: RuleSet, options: ScanOptions, mask: bool) -> dict[str, Any]:
    """取出会影响单文件命中结果的字段；只影响展示 / 调度的选项不参与。"""
    return {
        "ruleset": ruleset_to_mapping(ruleset),
        "options": {
            "skip_binary": options.skip_binary,
            "scan_documents": options.scan_documents,
            "max_file_bytes": options.max_file_bytes,
            "encodings": list(options.encodings),
            "context_chars": options.context_chars,
            "max_hits_per_file": options.max_hits_per_file,
        },
        "mask": mask,
    }


def cache_fingerprint(ruleset: RuleSet, options: ScanOptions, *, mask: bool = True) -> str:
    """计算缓存指纹。

    参与计算的字段：完整规则集（含表达式）、``skip_binary`` / ``scan_documents`` /
    ``max_file_bytes`` / ``encodings`` / ``context_chars`` / ``max_hits_per_file``
    与 ``mask``。递归深度、后缀与大小/时间过滤、并发数、报告格式等只影响
    「扫哪些文件」或「怎么输出」，不改变单文件命中，故不参与指纹。
    """
    payload = json.dumps(_fingerprint_payload(ruleset, options, mask), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _path_key(path: Path) -> str:
    """把路径规范化为缓存键：绝对路径 + 平台大小写口径。"""
    return os.path.normcase(str(path.absolute()))


def _matches_to_json(matches: Sequence[RuleMatch]) -> str:
    """把命中明细序列化为 JSON（字段与 :class:`RuleMatch` 一一对应）。"""
    return json.dumps([asdict(match) for match in matches], ensure_ascii=False)


def _matches_from_json(raw: str) -> list[RuleMatch]:
    """从 JSON 还原命中明细；结构异常时抛 ``ValueError``。"""
    items = json.loads(raw)
    if not isinstance(items, list):
        raise ValueError("matches 不是数组")
    return [RuleMatch(**item) for item in items if isinstance(item, dict)]


class ScanCache:
    """扫描结果缓存；读写失败自动降级，不向调用方抛异常。

    ``error`` 保存最近一次降级原因（中文），供 CLI / GUI 提示。
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_cache_path()
        self.error: str | None = None
        self._conn: sqlite3.Connection | None = None
        self._open()

    @property
    def enabled(self) -> bool:
        """当前连接是否可用。"""
        return self._conn is not None

    def close(self) -> None:
        """关闭连接（重复调用安全）。"""
        self._close_quietly()

    # -- 打开与自愈 ----------------------------------------------------- #
    def _open(self) -> None:
        """连接并建表；数据库损坏时删除重建，仍失败则禁用缓存。"""
        for attempt in range(2):
            conn: sqlite3.Connection | None = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(self.path), timeout=_BUSY_TIMEOUT_MS / 1000)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
                self._ensure_schema(conn)
                self._conn = conn
                return
            except (sqlite3.Error, OSError) as exc:
                if conn is not None:
                    # 先释放句柄，Windows 上占用中的文件无法删除。
                    with contextlib.suppress(sqlite3.Error):
                        conn.close()
                self._close_quietly()
                if attempt == 0 and self._remove_files():
                    self.error = f"缓存文件不可用，已重建（{exc}）"
                    continue
                self._disable(f"缓存不可用（{exc}），本次不使用缓存")

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """建表并校验结构版本；版本不符时清空重建。"""
        conn.executescript(_SCHEMA)
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
        if row is not None and row[0] != str(CACHE_SCHEMA_VERSION):
            conn.execute("DELETE FROM entries")
            conn.execute("DELETE FROM meta")
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema', ?)",
            (str(CACHE_SCHEMA_VERSION),),
        )
        conn.commit()

    def _remove_files(self) -> bool:
        """删除数据库与 WAL 附属文件；返回是否至少删掉了一个文件。"""
        removed = False
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(f"{self.path}{suffix}").unlink()
                removed = True
            except FileNotFoundError:
                continue
            except OSError:
                return False
        return removed or not self.path.exists()

    def _close_quietly(self) -> None:
        """关闭连接并丢弃引用。"""
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(sqlite3.Error):
                conn.close()

    def _disable(self, message: str) -> None:
        """标记本次禁用缓存，保留提示信息。"""
        self._close_quietly()
        self.error = message

    def _run(self, action: Any) -> Any:
        """执行一次数据库操作；遇到损坏则重建后重试一次，仍失败则禁用。"""
        conn = self._conn
        if conn is None:
            return None
        try:
            return action(conn)
        except sqlite3.Error as exc:
            self._close_quietly()
            if self._remove_files():
                self.error = f"缓存读写失败，已重建（{exc}）"
                self._open()
                conn = self._conn
                if conn is not None:
                    try:
                        return action(conn)
                    except sqlite3.Error as retry_exc:
                        self._disable(f"缓存读写失败，本次不使用（{retry_exc}）")
                        return None
            self._disable(f"缓存读写失败，本次不使用（{exc}）")
            return None

    # -- 读写 ----------------------------------------------------------- #
    def lookup_many(self, cache_key: str, items: Sequence[tuple[Path, int, int]]) -> dict[Path, FileHit | None]:
        """批量查询缓存；只返回 ``size`` 与 ``mtime_ns`` 都一致的文件。

        ``items`` 为 ``(路径, 大小, mtime_ns)``；返回 ``{路径: FileHit | None}``，
        ``None`` 表示上次实扫过但没有命中（同样可以跳过重扫）。
        """
        if not self._conn or not cache_key or not items:
            return {}

        def action(conn: sqlite3.Connection) -> dict[Path, FileHit | None]:
            found: dict[Path, FileHit | None] = {}
            for start in range(0, len(items), _LOOKUP_BATCH):
                batch = items[start : start + _LOOKUP_BATCH]
                keys = [_path_key(path) for path, _size, _mtime in batch]
                placeholders = ",".join("?" * len(keys))
                rows = conn.execute(
                    "SELECT path, size, mtime_ns, encoding, has_hit, matches FROM entries "
                    f"WHERE cache_key = ? AND path IN ({placeholders})",
                    [cache_key, *keys],
                ).fetchall()
                stored = {row[0]: row for row in rows}
                for path, size, mtime_ns in batch:
                    row = stored.get(_path_key(path))
                    if row is None or row[1] != size or row[2] != mtime_ns:
                        continue
                    if not row[4]:
                        found[path] = None
                        continue
                    try:
                        matches = _matches_from_json(row[5])
                    except (ValueError, TypeError):
                        continue
                    found[path] = FileHit(path=path, size=size, encoding=row[3], matches=matches)
            return found

        result = self._run(action)
        return result if isinstance(result, dict) else {}

    def store_many(self, cache_key: str, items: Sequence[tuple[Path, int, int, FileHit | None]]) -> None:
        """批量写入扫描结果；``hit=None`` 表示「实扫过且无命中」，同样缓存。"""
        if not self._conn or not cache_key or not items:
            return
        rows = [
            (
                cache_key,
                _path_key(path),
                size,
                mtime_ns,
                "" if hit is None else hit.encoding,
                int(hit is not None),
                "[]" if hit is None else _matches_to_json(hit.matches),
            )
            for path, size, mtime_ns, hit in items
        ]

        def action(conn: sqlite3.Connection) -> None:
            conn.executemany(
                "INSERT OR REPLACE INTO entries(cache_key, path, size, mtime_ns, encoding, has_hit, matches) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()

        self._run(action)

    def clear(self) -> int:
        """清空全部缓存条目，返回删除条数；失败时返回已删除的部分或 0。"""
        if not self._conn:
            return 0

        def action(conn: sqlite3.Connection) -> int:
            count = int(conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0])
            conn.execute("DELETE FROM entries")
            conn.commit()
            return count

        result = self._run(action)
        return int(result) if isinstance(result, int) else 0
