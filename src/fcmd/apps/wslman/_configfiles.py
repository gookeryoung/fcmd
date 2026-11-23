"""wslmanw「迁移配置文件」的纯逻辑。

原卡片只迁 ``~/.ssh``；现在扩成按**项目**迁移实例外已经配好的个人配置：

- ``ssh``：``~/.ssh`` 下的私钥 / 公钥 / ``known_hosts`` / ``config``；
- ``git``：``~/.gitconfig``、``~/.gitignore_global`` 与 ``~/.config/git/`` 下的配置；
- ``shell``：``~/.bashrc`` / ``~/.profile`` / ``~/.zshrc``。

来源可以是 Windows 宿主的家目录，也可以是任一其它已安装 WSL 实例的 ``$HOME``；
目标固定为当前实例的 ``$HOME``。每个项目都像「初始化环境」那样独立展示状态：
来源里有几个文件、实例里是否已经一致，来源为空的项目标「无文件」而不是「未迁移」。

只负责扫描、分类、生成探测与写入脚本、解析结果与持久化快照；文件内容由界面线程
读入后通过 stdin 送进实例，避免私钥 / 令牌出现在命令行。SSH 目录仍按 OpenSSH
口径写权限（目录 700、私钥 600、其余 644），其它配置文件 644；覆盖前先备份。
"""

from __future__ import annotations

import json
import posixpath
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from fcmd.apps.wslman import _configadapt, _sshfiles
from fcmd.apps.wslman._errors import not_found, runtime_error

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "BACKUP_PREFIX",
    "CONFIG_BACKUP_PREFIX",
    "DONE_LABEL",
    "GROUPS",
    "GROUP_GIT",
    "GROUP_SHELL",
    "GROUP_SSH",
    "SOURCE_LABELS",
    "SOURCE_WINDOWS",
    "SOURCE_WSL",
    "WRITE_PREFIX",
    "ConfigFile",
    "ConfigGroup",
    "ConfigSource",
    "ConfigTarget",
    "GroupProgress",
    "GroupState",
    "MigrationSnapshot",
    "MigrationState",
    "RemoteFile",
    "RestoreEntry",
    "RestoreRecord",
    "backup_root",
    "build_snapshot",
    "classify",
    "conflicts",
    "default_source_dir",
    "group_by_key",
    "group_progress",
    "group_stats",
    "groups_with_files",
    "is_migratable_name",
    "loose_modes",
    "migration_state",
    "parse_backup_path",
    "parse_probe",
    "parse_write_meta",
    "parse_write_result",
    "probe_script",
    "read_source",
    "remote_read_argv",
    "restore_script",
    "scan_host",
    "scan_target",
    "snapshot_from_mapping",
    "snapshot_summary",
    "snapshot_to_mapping",
    "source_file_path",
    "source_label",
    "target_summary",
    "write_script",
]

SOURCE_WINDOWS = _sshfiles.SOURCE_WINDOWS
SOURCE_WSL = _sshfiles.SOURCE_WSL
SOURCE_LABELS = _sshfiles.SOURCE_LABELS

GROUP_SSH = "ssh"
GROUP_GIT = "git"
GROUP_SHELL = "shell"

BACKUP_PREFIX = _sshfiles.BACKUP_PREFIX
"""SSH 覆盖前备份目录名前缀（与原卡片保持同一位置与命名）。"""

CONFIG_BACKUP_PREFIX = "fcmd-config-backup-"
"""其它配置文件覆盖前的备份目录名前缀。"""

WRITE_PREFIX = "WSLCFG_WRITTEN="
"""写入脚本最后一行：``<权限>|<属主>``，供界面校验落盘结果。"""

_PROBE_PREFIX = "WSLCFG_"
_HEAD_BYTES = 4096
_SSH_DIR_MODE = 0o700
_DEFAULT_DIR_MODE = 0o755
_DEFAULT_FILE_MODE = 0o644

DONE_LABEL = "已完成迁移"


@dataclass(frozen=True, slots=True)
class ConfigGroup:
    """一个可勾选的迁移项目（与「初始化环境」的任务项同形）。"""

    key: str
    label: str
    short: str
    detail: str
    paths: tuple[str, ...]
    """相对 ``$HOME`` 的 POSIX 路径；目录项会展开成其下的普通文件。"""
    directories: tuple[str, ...] = ()
    """``paths`` 中属于目录的项。"""
    windows: bool = True
    """Windows 宿主家目录里是否有对应位置（Shell 配置只在实例侧存在）。"""
    ssh: bool = False
    """是否按 SSH 权限口径（目录 700、私钥 600）写入。"""

    @property
    def dir_mode(self) -> int:
        return _SSH_DIR_MODE if self.ssh else _DEFAULT_DIR_MODE


GROUPS: tuple[ConfigGroup, ...] = (
    ConfigGroup(
        GROUP_SSH,
        "SSH 文件",
        "SSH",
        "迁移 ~/.ssh 下的私钥、公钥、known_hosts 与 config；目录权限 700、私钥 600，与 OpenSSH 口径一致。",
        (".ssh",),
        directories=(".ssh",),
        ssh=True,
    ),
    ConfigGroup(
        GROUP_GIT,
        "Git 配置",
        "Git",
        "迁移 ~/.gitconfig、~/.gitignore_global 与 ~/.config/git/ 下的配置文件；"
        "迁移后实例里的 git 用户名 / 邮箱 / 别名 / 忽略规则与来源一致。",
        (".gitconfig", ".gitignore_global", ".config/git/config", ".config/git/ignore"),
    ),
    ConfigGroup(
        GROUP_SHELL,
        "Shell 配置",
        "Shell",
        "迁移 ~/.bashrc、~/.profile 与 ~/.zshrc；会整份覆盖实例内的同名文件。"
        "若已用「初始化环境」写入过 fcmd 的 rc 块，请先确认是否仍要覆盖。",
        (".bashrc", ".profile", ".zshrc"),
        windows=False,
    ),
)

_GROUP_MAP = {group.key: group for group in GROUPS}
_MANAGED_PATHS = tuple(path for group in GROUPS for path in group.paths)
_DIR_PATHS = frozenset(path for group in GROUPS for path in group.directories)


@dataclass(frozen=True, slots=True)
class ConfigFile:
    """来源里的一个可迁移文件；路径统一相对 ``$HOME``。"""

    path: str
    size: int
    group: str
    kind: str
    warning: str = ""

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def group_label(self) -> str:
        group = group_by_key(self.group)
        return group.label if group is not None else self.group

    @property
    def mode(self) -> int:
        """写入实例后的权限：SSH 私钥 600，其余 644。"""
        if self.group == GROUP_SSH:
            return _sshfiles.SshFile(self.name, self.size, self.kind).mode
        return _DEFAULT_FILE_MODE

    @property
    def mode_text(self) -> str:
        return f"{self.mode:03o}"

    @property
    def kind_label(self) -> str:
        return _sshfiles.kind_label(self.kind)

    @property
    def size_text(self) -> str:
        return _sshfiles.SshFile(self.name, self.size, self.kind).size_text

    @property
    def checked_by_default(self) -> bool:
        """未知类型 / 备份临时文件默认不勾选；仍然可见、可手动勾选。"""
        return self.kind != "other" and is_migratable_name(self.path)

    @property
    def display(self) -> str:
        """勾选框文案：``.ssh/id_ed25519  ·  私钥  ·  1.2 KB``。"""
        return f"{self.path}  ·  {self.kind_label}  ·  {self.size_text}"


@dataclass(frozen=True, slots=True)
class RemoteFile:
    """实例 ``$HOME`` 下已有的受管文件。"""

    path: str
    mode: str
    size: int = 0


@dataclass(frozen=True, slots=True)
class ConfigTarget:
    """一次探测得到的实例 ``$HOME`` 配置快照。"""

    user: str = ""
    home: str = ""
    files: tuple[RemoteFile, ...] = ()
    dirs: Mapping[str, str] = field(default_factory=dict)
    """受管目录 → 权限（如 ``.ssh`` → 700）。"""

    @property
    def exists(self) -> bool:
        return bool(self.files) or bool(self.dirs)

    def paths(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.files)

    def find(self, path: str) -> RemoteFile | None:
        return next((item for item in self.files if item.path == path), None)

    def dir_mode(self, path: str) -> str:
        return str(self.dirs.get(path, ""))


@dataclass(frozen=True, slots=True)
class ConfigSource:
    """一次迁移的来源：Windows 宿主家目录或某个 WSL 实例的 ``$HOME``。"""

    kind: str = SOURCE_WINDOWS
    distro: str = ""
    directory: str = ""

    @property
    def is_wsl(self) -> bool:
        return self.kind == SOURCE_WSL

    @property
    def label(self) -> str:
        return source_label(self.kind, self.distro)

    def display_path(self) -> str:
        """实际读取的位置：Windows 侧是本地目录，WSL 侧是 ``<实例>:<家目录>``。"""
        if self.is_wsl:
            return f"{self.distro}:{self.directory or '~'}"
        return self.directory or str(default_source_dir())


@dataclass(frozen=True, slots=True)
class RestoreEntry:
    """一次迁移对单个目标文件做过的操作，供「还原上次迁移」使用。"""

    path: str
    backup: str = ""
    """覆盖前的备份路径（相对 ``$HOME``）；空串表示迁移前目标不存在。"""
    size: int = 0
    """迁移写入后的字节数，用于删除新增文件前确认它没被改过。"""

    @property
    def existed(self) -> bool:
        return bool(self.backup)


@dataclass(frozen=True, slots=True)
class RestoreRecord:
    """最近一次迁移的还原记录（按实例持久化）。"""

    stamp: str
    source: str = ""
    entries: tuple[RestoreEntry, ...] = ()

    @property
    def created(self) -> int:
        return sum(1 for item in self.entries if not item.existed)

    @property
    def overwritten(self) -> int:
        return sum(1 for item in self.entries if item.existed)

    @property
    def summary(self) -> str:
        return f"{self.stamp} 迁移的 {len(self.entries)} 个文件（新增 {self.created} · 覆盖 {self.overwritten}）"

    def to_mapping(self) -> dict[str, str]:
        rows = [[item.path, item.backup, str(item.size)] for item in self.entries]
        return {
            "stamp": self.stamp,
            "source": self.source,
            "entries": json.dumps(rows, ensure_ascii=False),
        }

    @classmethod
    def from_mapping(cls, raw: object) -> RestoreRecord | None:
        if not isinstance(raw, Mapping):
            return None
        stamp = str(raw.get("stamp") or "").strip()
        if not stamp:
            return None
        try:
            rows = json.loads(str(raw.get("entries") or "[]"))
        except (TypeError, ValueError):
            return None
        if not isinstance(rows, list):
            return None
        entries: list[RestoreEntry] = []
        for row in rows:
            if not isinstance(row, list) or not row:
                continue
            path = str(row[0] or "").strip()
            backup = str(row[1] or "").strip() if len(row) > 1 else ""
            try:
                size = int(str(row[2])) if len(row) > 2 else 0
            except (TypeError, ValueError):
                size = 0
            if path and group_for_path(path) is not None:
                entries.append(RestoreEntry(path, backup, max(size, 0)))
        if not entries:
            return None
        return cls(stamp, str(raw.get("source") or "").strip(), tuple(entries))


@dataclass(frozen=True, slots=True)
class GroupProgress:
    """单个迁移项目的进度：来源有效文件数 / 已在实例内一致的文件数。"""

    key: str
    matched: int = 0
    total: int = 0

    @property
    def tone(self) -> str:
        if not self.total:
            return "muted"
        if self.matched >= self.total:
            return "success"
        return "warning" if self.matched else "danger"

    @property
    def label(self) -> str:
        if not self.total:
            return "无文件"
        return f"{self.matched}/{self.total}"


@dataclass(frozen=True, slots=True)
class GroupState:
    """项目标签文案：``SSH 2/3``。"""

    key: str
    short: str
    label: str
    tone: str
    detail: str


@dataclass(frozen=True, slots=True)
class MigrationState:
    """由一次扫描结果推导的整体迁移进度（标签文案 / 色调 / 说明）。"""

    label: str
    tone: str
    detail: str
    matched: int = 0
    total: int = 0

    @property
    def done(self) -> bool:
        return self.label == DONE_LABEL


@dataclass(frozen=True, slots=True)
class MigrationSnapshot:
    """一次扫描的可持久化结果：只留状态文案与计数，不留文件内容。"""

    state: MigrationState
    source_label: str = ""
    source_path: str = ""
    source_count: int = 0
    target_count: int = 0
    target_summary: str = ""
    scanned_at: str = ""
    group_counts: str = ""
    """各项目计数：``ssh:2/2;git:1/0;shell:0/0``（``匹配/来源``）。"""

    @property
    def summary(self) -> str:
        return snapshot_summary(self)

    @property
    def collapsed_hint(self) -> str:
        return f"{self.source_label or '来源'} {self.source_count} 个文件"

    @property
    def counts(self) -> dict[str, GroupProgress]:
        return group_stats(self.group_counts)


_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "label",
    "tone",
    "detail",
    "matched",
    "total",
    "source_label",
    "source_path",
    "source_count",
    "target_count",
    "target_summary",
    "scanned_at",
    "group_counts",
)


def group_by_key(key: str) -> ConfigGroup | None:
    return _GROUP_MAP.get(key)


def group_for_path(path: str) -> ConfigGroup | None:
    """按相对路径找归属项目：目录项看前缀，文件项看全等。"""
    for group in GROUPS:
        for item in group.paths:
            if path == item:
                return group
            if item in group.directories and path.startswith(f"{item}/"):
                return group
    return None


def default_source_dir() -> Path:
    """默认来源：当前 Windows 用户的家目录（``.ssh`` / ``.gitconfig`` 都在它下面）。"""
    return Path.home()


def source_label(kind: str, distro: str = "") -> str:
    label = SOURCE_LABELS.get(kind, kind)
    return f"{label} · {distro}" if kind == SOURCE_WSL and distro else label


def is_migratable_name(path: str) -> bool:
    """备份 / 临时 / 系统杂项不算有效配置文件。"""
    return _sshfiles.is_migratable_name(posixpath.basename(path))


def classify(group: ConfigGroup, path: str, head: bytes = b"") -> str:
    """按项目判断文件类型：SSH 沿用私钥 / 公钥 / known_hosts 分类，其余都是配置。"""
    name = posixpath.basename(path)
    if not is_migratable_name(path):
        return "other"
    if group.ssh:
        return _sshfiles.classify(name, head)
    return "config"


def _warning(group: ConfigGroup, path: str, kind: str, content: bytes) -> str:
    if not is_migratable_name(path):
        return "备份 / 临时文件，默认不迁移"
    notes = _configadapt.adapt_notes(group.key, path, content) if content else ()
    if notes:
        # 命中 Windows 专用值 / CRLF 时，明确告诉用户迁移会自动适配，而不是照搬后炸掉。
        return "迁移时会自动适配：" + "、".join(notes)
    if group.ssh:
        return _sshfiles.file_warning(posixpath.basename(path), kind, content[:_HEAD_BYTES])
    if group.key == GROUP_SHELL:
        return "Shell 配置会整份覆盖实例内的同名文件，迁移前确认"
    return ""


def _sort_key(item: ConfigFile) -> tuple[int, int, str]:
    order = {group.key: index for index, group in enumerate(GROUPS)}
    kind_order = {"private": 0, "public": 1, "known_hosts": 2, "config": 3, "other": 4}
    return (order.get(item.group, len(order)), kind_order.get(item.kind, 9), item.path.casefold())


def scan_host(home: Path | str, groups: Sequence[ConfigGroup] = GROUPS) -> tuple[ConfigFile, ...]:
    """列出宿主家目录里各项目的文件；单个文件读不到只跳过，不影响整次扫描。"""
    root = Path(home)
    try:
        if not root.is_dir():
            raise not_found(
                f"未找到来源目录：{root}",
                hint="选择 Windows 用户的家目录（%USERPROFILE%）；.ssh / .gitconfig 都在它下面",
            )
    except OSError as exc:
        raise runtime_error(f"无法读取来源目录：{root}", hint=str(exc)) from exc

    files: list[ConfigFile] = []
    for group in groups:
        for rel in group.paths:
            target = root / Path(*rel.split("/"))
            if rel in group.directories:
                if not target.is_dir():
                    continue
                try:
                    children = list(target.iterdir())
                except OSError as exc:
                    raise runtime_error(f"无法读取来源目录：{target}", hint=str(exc)) from exc
                for child in children:
                    entry = _host_file(group, f"{rel}/{child.name}", child)
                    if entry is not None:
                        files.append(entry)
            elif target.is_file():
                entry = _host_file(group, rel, target)
                if entry is not None:
                    files.append(entry)
    files.sort(key=_sort_key)
    return tuple(files)


def _host_file(group: ConfigGroup, rel: str, path: Path) -> ConfigFile | None:
    name = posixpath.basename(rel).casefold()
    # SSH 密钥只读文件头做分类；SSH config / Git / Shell 需要完整内容做 Windows 适配提示。
    text_config = (group.ssh and name == "config") or group.key in {GROUP_GIT, GROUP_SHELL}
    try:
        if not path.is_file():
            return None
        size = path.stat().st_size
        with path.open("rb") as handle:
            content = handle.read() if text_config else handle.read(_HEAD_BYTES)
    except OSError:
        return None
    kind = classify(group, rel, content[:_HEAD_BYTES])
    return ConfigFile(rel, size, group.key, kind, _warning(group, rel, kind, content))


def scan_target(target: ConfigTarget) -> tuple[ConfigFile, ...]:
    """把实例快照里属于受管项目的文件转成可勾选条目（只按文件名分类）。"""
    files: list[ConfigFile] = []
    for item in target.files:
        group = group_for_path(item.path)
        if group is None:
            continue
        kind = classify(group, item.path, b"")
        files.append(ConfigFile(item.path, item.size, group.key, kind, _warning(group, item.path, kind, b"")))
    files.sort(key=_sort_key)
    return tuple(files)


def read_source(path: Path) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise runtime_error(f"无法读取源文件：{path}", hint=str(exc)) from exc


def source_file_path(source: ConfigSource, path: str) -> str:
    """来源实例里的绝对路径（``$HOME`` + 相对路径）。"""
    if not source.directory:
        raise runtime_error(
            f"实例 {source.distro} 没有可读取的家目录",
            hint="重新扫描来源实例后再迁移",
        )
    return posixpath.join(source.directory, path)


def conflicts(paths: Sequence[str], target: ConfigTarget) -> tuple[str, ...]:
    present = set(target.paths())
    return tuple(path for path in paths if path in present)


def looks_private(path: str) -> bool:
    return group_for_path(path) is not None and _sshfiles.looks_private(posixpath.basename(path))


def loose_modes(target: ConfigTarget) -> tuple[RemoteFile, ...]:
    """实例内权限过宽的 SSH 私钥；迁移同名文件后会被修正为 600。"""
    found: list[RemoteFile] = []
    for item in target.files:
        if not looks_private(item.path):
            continue
        try:
            mode = int(item.mode, 8)
        except ValueError:
            continue
        if mode & 0o077:
            found.append(item)
    return tuple(found)


def target_summary(target: ConfigTarget) -> str:
    """实例侧现状的一行说明：目录 / 文件 / 覆盖与权限提示。"""
    if not target.exists:
        return "实例内尚未找到受管配置（~/.ssh、~/.gitconfig、Shell 配置）；迁移时自动创建"
    parts: list[str] = []
    for rel, mode in target.dirs.items():
        if rel == ".ssh":
            parts.append(f"~/.ssh 目录权限 {mode or '未知'}")
    if target.files:
        names = "、".join(item.path for item in target.files)
        parts.append(f"已有 {len(target.files)} 个文件：{names}；同名文件会先备份再覆盖")
    else:
        parts.append("受管目录已存在但还没有配置文件")
    loose = loose_modes(target)
    if loose:
        detail = "、".join(f"{item.path}（{item.mode}）" for item in loose)
        parts.append(f"注意：{detail}权限过宽，迁移同名文件后会改为 600")
    return "；".join(parts)


def backup_root(group: str, stamp: float) -> str:
    """备份目录（相对 ``$HOME``）：SSH 仍落在 ``~/.ssh/fcmd-ssh-backup-…``。"""
    if group == GROUP_SSH:
        return f".ssh/{_sshfiles.backup_name(stamp)}"
    return CONFIG_BACKUP_PREFIX + time.strftime("%Y%m%d-%H%M%S", time.localtime(stamp))


def _content_matches(item: ConfigFile, target: ConfigTarget) -> bool:
    """判断来源文件是否已到实例。

    SSH 私钥 / 公钥 / known_hosts 按大小比对；配置文件在写入前会被
    :mod:`_configadapt` 适配（去掉 Windows 专用值、CRLF 转 LF），大小本来就该变化，
    所以只看是否已存在 + 权限，否则迁移完成后会永远显示「部分迁移」。
    """
    remote = target.find(item.path)
    if remote is None:
        return False
    if item.group == GROUP_SSH and item.kind in {"private", "public", "known_hosts"}:
        return remote.size == item.size
    return True


def _mode_matches(item: ConfigFile, target: ConfigTarget) -> bool:
    remote = target.find(item.path)
    if remote is None or not remote.mode:
        return True
    try:
        return int(remote.mode, 8) == item.mode
    except ValueError:
        return True


def group_progress(files: Sequence[ConfigFile], target: ConfigTarget) -> dict[str, GroupProgress]:
    """按项目统计「已一致 / 来源有效文件」；来源为空的项目给 ``0/0``。"""
    result: dict[str, GroupProgress] = {group.key: GroupProgress(group.key) for group in GROUPS}
    for item in files:
        if not item.checked_by_default:
            continue
        progress = result.setdefault(item.group, GroupProgress(item.group))
        matched = progress.matched + (1 if _content_matches(item, target) else 0)
        result[item.group] = GroupProgress(item.group, matched, progress.total + 1)
    return result


def group_states(files: Sequence[ConfigFile], target: ConfigTarget) -> tuple[GroupState, ...]:
    """每个项目的标签：来源为空给「无文件」，部分 / 全部 / 未迁移按计数着色。"""
    progress = group_progress(files, target)
    states: list[GroupState] = []
    for group in GROUPS:
        item = progress.get(group.key) or GroupProgress(group.key)
        if not item.total:
            detail = "来源里没有该项目的配置文件"
            if not group.windows and group.key == GROUP_SHELL:
                detail = "Windows 宿主没有该项目；请选择 WSL 实例作为来源"
            states.append(GroupState(group.key, group.short, "无文件", "muted", detail))
            continue
        states.append(
            GroupState(
                group.key,
                group.short,
                item.label,
                item.tone,
                f"{group.label}：来源 {item.total} 个文件，实例内已一致 {item.matched} 个",
            )
        )
    return tuple(states)


def groups_with_files(files: Sequence[ConfigFile]) -> dict[str, tuple[ConfigFile, ...]]:
    grouped: dict[str, list[ConfigFile]] = {}
    for item in files:
        grouped.setdefault(item.group, []).append(item)
    return {key: tuple(values) for key, values in grouped.items()}


def group_counts_text(progress: Mapping[str, GroupProgress]) -> str:
    return ";".join(f"{group.key}:{progress[group.key].matched}/{progress[group.key].total}" for group in GROUPS)


def group_stats(raw: str) -> dict[str, GroupProgress]:
    """解析 ``ssh:2/2;git:1/0``；坏行当成 ``0/0``。"""
    result: dict[str, GroupProgress] = {}
    for chunk in str(raw or "").split(";"):
        key, _, rest = chunk.partition(":")
        if not key:
            continue
        matched, _, total = rest.partition("/")
        try:
            values = GroupProgress(key, max(int(matched), 0), max(int(total), 0))
        except ValueError:
            values = GroupProgress(key)
        result[key] = values
    return result


def migration_state(files: Sequence[ConfigFile], target: ConfigTarget) -> MigrationState:
    """按扫描结果判断整体迁移完成度，供卡片标签直接展示。"""
    valid = [item for item in files if item.checked_by_default]
    skipped = len(files) - len(valid)
    total = len(valid)
    if not total:
        if files:
            return MigrationState(
                "无有效文件",
                "muted",
                f"来源有 {len(files)} 个文件，但均为备份 / 临时 / 未知类型；默认不迁移，也不参与完成度判断",
            )
        return MigrationState("无文件", "muted", "来源里没有受管项目的配置文件")
    missing = [item for item in valid if not _content_matches(item, target)]
    if not missing:
        wrong = [item for item in valid if not _mode_matches(item, target)]
        if wrong:
            names = "、".join(item.path for item in wrong)
            return MigrationState(
                "权限待修正",
                "warning",
                f"文件已全部存在，但 {names} 的权限与口径不一致；迁移同名文件会按项目重设权限",
                total,
                total,
            )
        detail = f"{total} 个有效文件均已存在于实例，且权限正确"
        if skipped:
            detail += f"；已忽略 {skipped} 个备份 / 临时 / 未知文件"
        return MigrationState(DONE_LABEL, "success", detail, total, total)
    if len(missing) == total:
        return MigrationState("未迁移", "muted", "实例内未找到与来源匹配的文件", 0, total)
    matched = total - len(missing)
    return MigrationState(
        "部分迁移",
        "warning",
        f"已匹配 {matched}/{total} 个文件；其余文件缺失或大小与来源不一致",
        matched,
        total,
    )


def build_snapshot(
    files: Sequence[ConfigFile],
    target: ConfigTarget,
    source: ConfigSource,
    *,
    stamp: float | None = None,
) -> MigrationSnapshot:
    """把一次扫描结果压成可持久化的状态（不含文件内容）。"""
    now = time.time() if stamp is None else stamp
    progress = group_progress(files, target)
    return MigrationSnapshot(
        state=migration_state(files, target),
        source_label=source.label,
        source_path=source.display_path(),
        source_count=len(files),
        target_count=len(target.files),
        target_summary=target_summary(target),
        scanned_at=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        group_counts=group_counts_text(progress),
    )


def snapshot_summary(snapshot: MigrationSnapshot) -> str:
    """摘要行文案：``Windows 宿主 5 个文件 · 实例内 3 个文件``。"""
    label = snapshot.source_label or "来源"
    return f"{label} {snapshot.source_count} 个文件 · 实例内 {snapshot.target_count} 个文件"


def snapshot_to_mapping(snapshot: MigrationSnapshot) -> dict[str, str]:
    state = snapshot.state
    values = {
        "label": state.label,
        "tone": state.tone,
        "detail": state.detail,
        "matched": str(state.matched),
        "total": str(state.total),
        "source_label": snapshot.source_label,
        "source_path": snapshot.source_path,
        "source_count": str(snapshot.source_count),
        "target_count": str(snapshot.target_count),
        "target_summary": snapshot.target_summary,
        "scanned_at": snapshot.scanned_at,
        "group_counts": snapshot.group_counts,
    }
    return {name: values.get(name, "") for name in _SNAPSHOT_FIELDS}


def _count(raw: object) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def snapshot_from_mapping(raw: object) -> MigrationSnapshot | None:
    """从设置文件恢复上次扫描结果；没有标签（坏行 / 旧版本）时返回 ``None``。"""
    if not isinstance(raw, Mapping):
        return None
    label = str(raw.get("label") or "").strip()
    if not label:
        return None
    state = MigrationState(
        label,
        str(raw.get("tone") or "muted").strip() or "muted",
        str(raw.get("detail") or "").strip(),
        _count(raw.get("matched")),
        _count(raw.get("total")),
    )
    return MigrationSnapshot(
        state=state,
        source_label=str(raw.get("source_label") or "").strip(),
        source_path=str(raw.get("source_path") or "").strip(),
        source_count=_count(raw.get("source_count")),
        target_count=_count(raw.get("target_count")),
        target_summary=str(raw.get("target_summary") or "").strip(),
        scanned_at=str(raw.get("scanned_at") or "").strip(),
        group_counts=str(raw.get("group_counts") or "").strip(),
    )


def target_dir_mode(group: ConfigGroup) -> int:
    """写入时直接父目录应有的权限：SSH 目录 700，其余 755。"""
    return group.dir_mode if group.ssh else _DEFAULT_DIR_MODE


# 探测与写入脚本独立在 _configio，避免单个模块超过行数预算；这里保留同一套导出名。
from fcmd.apps.wslman._configio import (  # noqa: E402
    parse_backup_path,
    parse_probe,
    parse_write_meta,
    parse_write_result,
    probe_script,
    remote_read_argv,
    restore_script,
    write_script,
)
