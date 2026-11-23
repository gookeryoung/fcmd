# 0023 wslman：Windows 上的 WSL 发行版管理

- 状态：P5 已落地（CLI：只读 `list` / `status` / `info` / `config` / `doctor` / `available`；变更 `open` / `terminate` / `set-default`；传输 `export` / `import` / `copy` / `compress` / `backups` / `backup-verify` / `backup-remove`；下载/销毁 `download` / `unregister` / `shutdown`；GUI：`wslmanw`）
- 日期：2026-11-13
- 相关文档：[ADR 0001](0001-layering.md)、[ADR 0003](0003-package-layout.md)、[ADR 0013](0013-gui-toolkit-feasibility.md)

## 背景

Windows 上管理 WSL 发行版目前只能拼 `wsl.exe` 子命令，痛点集中在四处：

1. **输出不可直接消费**：`wsl --list --verbose` 被重定向时输出 UTF-16LE，
   带 BOM 与 NUL 字节，首列还有 `*` 默认标记；`--status` 是本地化自由文本。
2. **事实分散**：发行版名、`BasePath`、版本、默认用户只在注册表 `Lxss` 下；
   运行状态只在 `wsl -l -v`；全局内存 / 交换配置只在 `%UserProfile%\.wslconfig`。
3. **重活没有反馈**：`--export` / `--import` 可能几十 GB，`wsl.exe` 不给进度，
   中断后容易留下半截包或半注册的发行版。
4. **破坏性操作没有护栏**：`--unregister` 直接删虚拟磁盘、`--shutdown` 会连带
   停掉 Docker Desktop 与其他发行版，误触代价高。

`wslman`（CLI）与 `wslmanw`（Qt GUI）只做一件事：把上述能力收敛成一套可脚本化、
可机读、默认安全的外壳；**不替代** WSL 本身，不管理发行版内部的包与配置。

非目标（v1 明确不做）：

- 不写 `/etc/wsl.conf`、不写 `.wslconfig`（只解析与校验，写入需 `--shutdown` 生效，风险边界不同）；
- 不挂载 / 卸载磁盘（`--mount`），不更新 WSL（`--update`），不安装 WSL 功能本身；
- 不做 vhdx 空间回收（`Optimize-VHD` 需 Hyper-V 模块与管理员权限，另立议题）；
- 不拆 Store 的 `.appx` 包（格式与签名策略变动频繁，见 D8 降级路径）。

## 决策

### D1 入口、包布局与注册边界

新增包 `src/fcmd/apps/wslman/`，两层平铺，遵守 ADR 0003 与 `tests/test_layout.py`：

| 文件 | 职责 |
|---|---|
| `__init__.py` | 包门面，不注册逻辑 |
| `_errors.py` | `WslmanError`、错误码常量、场景 → 错误码映射 |
| `_runner.py` | 执行器协议、`SubprocessRunner`、能力探测 `probe()` |
| `_decode.py` | 控制台字节解码、`-l -v` / `-l -o` / `--status` 解析（纯函数） |
| `_registry.py` | `Lxss` 只读读取（协议 + Windows 实现 + 假实现） |
| `_wslconfig.py` | `.wslconfig` INI 解析、已知键白名单校验 |
| `_distro.py` | 注册表 / `-l -v` / `--status` 合并成 `Distro` 模型与冲突标记 |
| `_disk.py` | 卷剩余空间、vhdx / rootfs 占用估算、FAT32 与网络盘判定 |
| `_transfer.py` | 导出 / 导入 / 复制 / 压缩的状态机：预检、进度、中断、清理 |
| `_source.py` | 下载源选择（CLI / manifest / 本地文件）、sha256 校验、断点续传 |
| `_terminal.py` | `wt.exe` / `conhost` 回退的 argv 构造与启动 |
| `_config.py` | `~/.fcmd/wslman.json` 读写与字段级容错 |
| `wslman.py` | CLI 子命令注册与双读输出 |
| `wslmanw.py` + `_wslmanw_qt.py` | Qt 窗口，复用 `fcmd.gui` 主题与设置持久化 |

协作约束（本轮生效）：**不修改** `pyproject.toml`、`cli/_manifest_data.py`、
`uv.lock`、`core/exit_codes.py`，也不碰其他应用包。入口注册与清单重生成由统一
会话处理，需要写入的内容见文末「交接」。

### D2 外部命令必须可注入

所有对 Windows 的接触面都收敛成协议，业务代码只依赖协议：

```python
@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    duration_ms: int


class Runner(Protocol):
    def run(self, argv: Sequence[str], *, timeout: float | None = None) -> CommandResult: ...
    def spawn(self, argv: Sequence[str], *, stdout_path: Path | None = None) -> StreamingRun: ...


class StreamingRun(Protocol):
    """长任务：可轮询进度、可中断，输出不进内存。"""

    def wait(self, timeout: float | None = None) -> int: ...
    def terminate(self) -> None: ...
```

- `SubprocessRunner` 只用 `subprocess.Popen`，`shell=False`，argv 传列表；
  Windows 下加 `CREATE_NO_WINDOW`，子进程环境注入 `WSL_UTF8=1`（新版 WSL 尊重
  该变量，老版本忽略，故仍需 D3 的解码兜底）。
- 注册表、磁盘空间、终端启动、HTTP 下载同样做成可注入协议：
  `RegistryReader` / `DiskProbe` / `Launcher` / `Fetcher`。
- 测试一律注入假实现：`FakeRunner` 按 argv 前缀返回预置的 UTF-16LE 字节、
  `FakeRegistry` 返回构造的键值、`FakeDisk` 返回指定剩余空间、`FakeLauncher`
  记录 argv 不真的开窗口。**单元测试不执行 `wsl.exe`**；真实环境验证单独标记
  `@pytest.mark.slow`，默认门禁不跑。
- 非 Windows 平台在 `preflight()` 直接返回 `NOT_FOUND` + 中文提示；纯解析器
  仍可在任意平台跑单测。

### D3 控制台解码与 `wsl -l -v` 解析

解码顺序（不靠猜，逐级可判定）：

1. 以 `FF FE` 开头 → `utf-16`；
2. 偶数位 NUL 占比 > 30% → `utf-16-le`（去掉开头 BOM）；
3. `utf-8` 严格解码成功 → 采用；
4. 回退 `locale.getpreferredencoding(False)`（简中环境常见 `cp936`）。

最后统一剥离 `\x00`、BOM、ANSI 转义与首尾空白。

`-l -v` 解析规则：

- 跳过首行表头，**不匹配表头文字**（本地化会变）；
- 每行可选 `*` 前缀标记默认发行版；
- 从右往左取：最后一个 token ∈ `{1, 2}` 为版本号；其前一列（可能两个词）为状态；
  其余为名称（名称允许空格）；
- 状态词表 `Running / Stopped / Installing / Uninstalling / Converting / ...` 之外的
  取值保留原文到 `state_raw`，`state` 记为 `unknown`，**不抛异常**；
- 空列表（退出码 0 或可识别的「没有已安装的分发版」）是合法结果，不是错误。

`--list --online` 以「两个及以上空格或制表符」分隔 `NAME` 与 `FRIENDLY NAME`；
`--status` 是本地化自由文本，**只原样展示，不作为默认发行版 / 版本的判定依据**。

### D4 事实来源与优先级

| 事实 | 权威来源 | 备注 |
|---|---|---|
| 名称 / `BasePath` / `Version` / `DefaultUid` / `PackageFamilyName` | `HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss\{GUID}` | 只读；`winreg.KEY_READ`；读不到就降级 |
| 默认发行版 | `-l -v` 的 `*`；注册表根 `DefaultDistribution` 仅作交叉校验 | 不一致时 `doctor` 报 `warn`，不自动修 |
| 默认 WSL 版本 | 注册表根 `DefaultVersion` | `--set-default-version` 之后的校验依据 |
| 运行 / 停止 | `-l -v` 的 STATE | 注册表 `State` 不作为运行状态判据 |
| 磁盘占用 | WSL2 取 `BasePath/ext4.vhdx` 的 `st_size`；WSL1 递归累加 `BasePath/rootfs` | 稀疏 vhdx 会高估，偏安全侧，输出标 `estimated` |
| 全局内存 / 交换等 | `%UserProfile%\.wslconfig` | 只解析，不写 |

`Flags` 等未知位**不解读**：原文以十六进制透出，避免把某一 WSL 版本的位语义
写死。注册表读取失败只让 `info` / `doctor` 降级（`registry: unavailable` + 原因），
不影响 `list`。

### D5 命令面：只读 / 变更 / 破坏性三级

子命令按风险分三档，**只有破坏性档要求 `--yes`**；`fcmd wslman --help` 的子命令表
以 `[危险]` 前缀标注，`--json` 结果带 `"destructive": true`，两条路径同样可辨。

| 子命令 | 档位 | 依赖的 Windows / WSL | 确认标志 | 主要风险 |
|---|---|---|---|---|
| `list` | 只读 | `wsl -l -v`、注册表 | — | 无（不启动任何发行版） |
| `status` | 只读 | `wsl --status`、`wsl --version`、`.wslconfig` | — | 无 |
| `info <d>` | 只读 | 注册表 | — | 无；`--include-config` 会启动发行版，需显式 |
| `available` | 只读 | `wsl -l -o` | — | 联网；失败给降级提示 |
| `doctor` | 只读 | 以上全部 | — | 无；有问题以退出码 5 表达 |
| `config` | 只读 | 文件系统 | — | 无 |
| `backups <dir>` | 只读 | 文件系统 | — | 无 |
| `backup-verify <f>` | 只读 | 文件系统 | — | 无 |
| `open <d>` | 变更 | `wt.exe` 或 `wsl.exe` | — | 会启动发行版；新窗口 |
| `terminate <d>` | 变更 | `wsl -t` | — | 杀掉发行版内进程，未保存工作丢失 |
| `set-default <d>` | 变更 | `wsl --set-default` | — | 后续裸 `wsl` 落到新默认 |
| `export <d> <f>` | 变更 | `wsl --export`（必要时先 `-t`） | 文件已存在需 `--overwrite`；运行中必须显式 `--terminate-first` 或 `--allow-running` | 需先停机或显式接受一致性风险；几十 GB |
| `import <n> <f>` | 变更 | `wsl --import` | 名称冲突需 `--replace --yes` | 目标目录必须本地 NTFS 且为空 |
| `copy <d> <n>` | 变更 | `export` + `import` | 同名同上 | 临时包需要额外磁盘 |
| `compress <f>` | 变更 | 纯 Python `gzip` | `--remove-source` 需 `--yes` | 删除源包不可逆 |
| `download <n>` | 变更 | `wsl --install` 或 HTTPS | 覆盖已有文件需 `--overwrite` | 默认走 `wsl --install` 会触发系统级安装、可能弹 UAC，且不能指定自定义镜像 |
| `set-version <d> <v>` | 破坏性 | `wsl --set-version` | `--yes` | 重写 vhdx，耗时长，可能中途失败 |
| `unregister <d>` | 破坏性 | `wsl --unregister` | `--yes` | 删除虚拟磁盘，无回收站 |
| `shutdown` | 破坏性 | `wsl --shutdown` | `--yes` | 停掉全部发行版与 WSL 虚拟机 |
| `backup-remove <f>` | 破坏性 | 文件系统 | `--yes` | 删除导出包不可逆 |

约定：

- 变更与破坏性档都提供 `--preview`：打印**将要执行的完整 argv、目标路径、
  预计占用、预检结论**，不产生副作用（不建目录、不启动/停止发行版）。
  不使用 `--dry-run`——它是框架级全局选项，工具函数收不到（见 `core/toolkit/args.py`）。
- 缺 `--yes` 时：退出码 2（`USAGE`），提示行直接给出补上 `--yes` 的完整命令，
  便于人和小模型照着改。
- 只读子命令不得调用任何会启动发行版的命令；`info` 默认只读注册表。

### D6 各能力落到的具体命令与风险

**发行版管理**

- `list`：`wsl.exe --list --verbose` 为主，注册表补 `BasePath` / `DefaultUid` /
  `PackageFamilyName`；`--json` 给 `distros[]`（`name/state/version/default/base_path/
  default_uid/disk_bytes/estimated`）。
- `status`：`wsl --status` 原文 + `wsl --version` 解析出的 WSL 版本 + `.wslconfig`
  摘要；不把本地化文本转成结构化判定。
- `terminate`：`wsl.exe --terminate <Name>`。不要求 `--yes`（可重启恢复），
  但提示会杀掉发行版内进程；GUI 用 `askyesno` 二次确认。
- `set-default`：`wsl.exe --set-default <Name>`，执行后用 `-l -v` 复核 `*`。
- `set-version`：`wsl.exe --set-version <Name> <1|2>`。风险：重写 vhdx、耗时长、
  WSL2 需要虚拟化支持、2→1 在新版本上逐步弃用。归**破坏性**档，必须 `--yes`
  （缺失 → 退出码 2，并打印补上 `--yes` 的完整命令）。
- `unregister`：`wsl.exe --unregister <Name>`。护栏四件套：
  1. 运行中且未给 `--terminate-first` → 拒绝；
  2. 未给 `--yes` → 拒绝（退出码 2）；
  3. `--backup <file>` 给定时先导出，导出失败则**绝不继续注销**；
  4. `--preview` 展示 `BasePath`、占用大小、将删除的虚拟磁盘路径。
  注销后 BasePath 目录是否残留随 WSL 版本而异：只报告，不自动清理。
- `shutdown`：`wsl.exe --shutdown`。必须 `--yes`，提示中明确列出连带影响
  （Docker Desktop、IDE Remote、其他发行版）。

**导出**

`export <d> <f>` 不强制先停机，但**不允许静默**：检测到发行版运行中时，必须显式
二选一，没有默认值：

- `--terminate-first`：先 `wsl.exe --terminate <d>` 再导出；
- `--allow-running`：直接导出，接受数据一致性风险。

两者都缺 → 退出码 3（`INVALID_ARGUMENT`），并同时打印补上两种标志的完整命令。

**下载**

`download <name>` 不绑定单一 URL，见 D8。默认 `--source auto` 允许直接调用
`wsl.exe --install -d <Name> --no-launch`；该路径是**系统级安装**：可能触发 UAC，
且无法指定自定义镜像 / 特定构建。要特定版本必须改用清单（`--source manifest`）
或本地文件（`--source file`），帮助文本与文档必须把这一点写在 `download` 的
命令说明里，而不是只藏在 ADR。

`--source manifest|file` 只落盘并给出 sha256，安装交给 `import`，避免「下载即安装」
的隐式副作用。

**复制**

`copy <src> <dst>` = 导出到临时包 + 以新名导入：

1. 源发行版运行中 → 需 `--terminate-first`；
2. 目标名已存在 → 拒绝，除非 `--replace --yes`（走 `import` 同一条护栏）；
3. 临时包优先放在目标卷，避免跨卷搬几十 GB；`--temp <dir>` 可覆盖；
4. 保留 `Version`；不复制「默认」标记；不复制运行状态；
5. 任一步失败：删除本次临时包（`--keep-temp` 可保留），已导入的半成品按 D7 处理。

**压缩**

选型：**以 `wsl --export` 产出的未压缩 tar 为规范产物，压缩是可选外层（gzip）**。

- 理由一：`--import` 直接吃 tar，规范产物与恢复路径一一对应，不多一个中间格式；
- 理由二：`--export` 无进度且长，tar 阶段可校验字节数、可 `--keep-partial`，
  压缩失败不会连累归档本体；
- 理由三：不依赖版本相关的导出扩展（部分较新版本可能提供 `--format` / `--vhd`
  之类的扩展，检测到也只作为优化，不作为契约）；
- 不引入 zstd：项目当前 Python ≥ 3.13，v1 只做 `gzip`（`--level 1–9`，默认 6），
  不检测外部 `zstd.exe`，也不做静默降级；需要更强压缩时由用户先用外部工具处理
  再 `import`。
- `compress <tar>` 独立成命令，也支持 `export --compress gzip` 一步完成；
  `--remove-source` 必须同时给 `--yes`。

**打开终端**

| 场景 | argv | 说明 |
|---|---|---|
| 首选 | `wt.exe new-tab --title wsl:<Name> -- wsl.exe -d <Name> [--cd <Dir>] [-u <User>]` | `shutil.which("wt.exe")` 找不到时再查 `%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe` |
| 回退 | `wsl.exe -d <Name> [--cd <Dir>] [-u <User>]` + `CREATE_NEW_CONSOLE` | **不直接调 `conhost.exe`**：新控制台窗口由系统创建 conhost 承载，直接拉起 conhost 不是受支持用法 |

`--workdir` 必须是 Windows 路径（WSL 内部路径用户自行传 `--cd` 语义），含空格时依赖
argv 列表而非 shell 引号。`wt.exe` 启动失败（返回非 0 或找不到）自动回退 conhost
路径，并在结果里标明实际使用了哪条（`terminal: "wt" | "conhost"`）。

`.wslconfig` 只做解析与校验：

- 用 `configparser.ConfigParser(interpolation=None, strict=False)`；
- 已知小节 `[wsl2]` / `[experimental]` 的已知键做类型形状提示（如 `memory` 是带单位
  字符串，不转数字）；未知键保留并给 `warn`，不拒绝整份文件；
- 重复键、无小节键、编码错误分别给独立 `warn`，不中断 `status`。

### D7 传输：磁盘预检、进度、中断与残留

**预检（导出 / 导入 / 复制 / 压缩共用）**

- 目标卷剩余空间：`shutil.disk_usage(target).free`；
- 需要空间 = 估算大小 × `safety_margin`（默认 1.10），且写完后仍须留
  `min_free_after_mb`（默认 512 MiB）；
- 导出估算：WSL2 取 `ext4.vhdx` 的 `st_size`（稀疏会高估，偏安全）；
  WSL1 递归累加 `rootfs/`，带 5 秒时间预算，超时则 `known=false`，此时要求
  剩余空间 ≥ 2 GiB 并在运行时持续监控；
- 导入额外检查：目标目录必须不存在或为空、必须本地 NTFS（`GetDriveTypeW` 拒绝
  网络盘，卷类型拒绝 FAT32/exFAT 上的 BasePath）；单文件 > 4 GiB 时 FAT32 目标
  直接拒绝并给提示；
- `--preview` 输出全部预检结论；任一硬性检查失败 → 退出码 1（`RUNTIME_ERROR`）
  或 3（参数不合法），不进入执行阶段。

**进度**

- 导出：每秒轮询目标文件大小 / 估算值，输出 `≈` 百分比与已写字节；结束时才
  允许 100%，避免提前报满。估算值取 `distro_export_bytes`：稀疏 vhdx 用实际占用
  （`GetCompressedFileSizeW` / `st_blocks`）而不是逻辑 `st_size`，否则百分比会长期
  停在偏低值；预检仍用逻辑大小（偏安全）。GUI 把同一进度写到状态栏，右侧进度条
  与「百分比 · 已写 / 估算 · 速度 · 剩余」同步；
- 导入：轮询 `BasePath/ext4.vhdx` 增长，最终大小未知，只报已写字节与耗时，
  **不伪造百分比**；
- 下载：`Content-Length` + 已收字节；
- 进度一律写 stderr：人读为一行文本，`--json` 时 stdout 仍只有一个
  `fcmd-result/1` 信封，不被进度污染；`--quiet` 关闭进度。

**中断与残留**

- 导出先写 `<file>.part`，成功后 `os.replace` 原子改名：中断不会破坏已有备份；
- 默认删除本次产生的 `.part`（`--keep-partial` 保留），退出码 130（`INTERRUPTED`）；
- 导入被中断或失败：**不自动 `--unregister`**，只在结果里给出残留发行版名、
  `BasePath` 与可直接复制的清理命令；自动注销属于破坏性操作，不能藏在清理逻辑里；
- `--replace`（覆盖已有发行版）额外护栏：默认要求 `--backup <file>`，无备份时
  拒绝；`--backup` 失败则不注销旧发行版；
- 并发：`~/.fcmd/locks/wslman-<distro>.lock`，同发行版同时只允许一个传输；
  陈旧锁按 PID + 创建时间判定并给出明确提示。

### D8 下载源、校验与降级

来源分层，**没有任何单一 URL 被当作唯一来源**：

| 顺序 | 来源 | 触发 | 失败后 |
|---|---|---|---|
| 1 | `wsl.exe --install -d <Name> --no-launch` | `--source auto`（默认）且能力探测到 `--no-launch` | 落到 2 |
| 2 | 发行版清单 URL（可配置） | `--source manifest` 或 1 不可用 | 落到 3 |
| 3 | 用户本地文件 | `--source file --file <path>` | 报错并给指引 |

- 清单 URL 默认值只作为**可覆盖指针**放在 `~/.fcmd/wslman.json` 的
  `download.manifest_url`，可用 `--manifest-url` 临时覆盖；解析时只认
  「按当前架构取到可导入归档」的条目，字段名兼容 `sha256` / `checksum` 等常见写法；
- 若清单条目只有 `.appx` / `.AppxBundle`：**不拆包**，明确报「格式不可直接导入，
  请改用 `--source cli`」，因为 Store 包的签名与展开方式随版本变动；
- 校验：始终计算并打印 sha256 与文件大小；只有调用方给了期望值
  （`--sha256` 或配置里的 `download.sha256.<name>`）且匹配时才置
  `verified: true`；不匹配 → 删除本次下载并退出 5（`CHECK_FAILED`）；
  清单未提供摘要时**不谎称已校验**；
- 下载写 `<file>.part`，服务器支持 `Range` 且 `ETag` / `Last-Modified` 未变时续传，
  否则从头下载；仅 HTTPS、限制重定向次数、超时可配；
- 全部来源不可用时的降级：给出「手工下载 + `wslman import`」的两条命令，并把
  失败原因（无网络 / 清单不可达 / 架构无条目）写进 `hint` 与 JSON。

### D9 机读输出与退出码（复用现有契约）

本阶段**不新增错误码**，只用 `core/exit_codes.py` 已登记的码，避免改共享文件：

| 场景 | code | kind | 退出码 |
|---|---|---|---|
| 找不到 `wsl.exe` | `NOT_FOUND` | not_found | 4 |
| 指定发行版不存在 | `NOT_FOUND` | not_found | 4 |
| WSL 未启用 / 服务不可用 | `RUNTIME_ERROR` | runtime | 1 |
| 发行版正在运行但缺少必要开关 | `INVALID_ARGUMENT` | input | 3 |
| 目标目录非空 / 名称非法 / 文件已存在 | `INVALID_ARGUMENT` | input | 3 |
| 缺少 `--yes` | `USAGE` | usage | 2 |
| 磁盘空间不足 | `RUNTIME_ERROR` | runtime | 1 |
| sha256 不匹配 | `CHECK_FAILED` | check | 5 |
| `doctor` 存在 fail 项 | `CHECK_FAILED` | check | 5 |
| 用户 Ctrl+C | `INTERRUPTED` | interrupted | 130 |

- 每个子命令都有 `--json`（`json_output: bool = False`，参数名沿用仓库别名约定）；
- 成功走 `emit_result("wslman", "<子命令>", data)`，`data` 内含
  `schema: "wslman/<子命令>/1"`、`commands`（实际执行的 argv）、`artifacts`、
  `duration_ms`；破坏性命令另带 `destructive` / `confirmed`；
- 失败走 `report_error(...)`，人读与机读共用同一个退出码（`core/result.py`）；
- `doctor` 的 `checks[]` 每项 `{name, status: ok|warn|fail, detail, hint}`，
  有问题时 `emit_result(..., ok=False)` + 退出 5，不写 stderr 错误体。

### D10 GUI（`wslmanw`）

- 复用 `fcmd.gui.toolwindow` / `workers` / `widgets.LogView` 与
  `fcmd.gui.theme`；设置持久化 `~/.fcmd/ui/wslmanw.json`（`fcmd.gui.config`）。
- 布局：复用共享 `QtMainWindow` 的侧边栏分页（发行版 / 镜像 / 设置），
  每页只做一件事：发行版页管发行版，镜像页管清单与下载动作，配置项（下载目录 /
  下载源 / 校验）集中在设置页，镜像页不再放表单。任务日志不占分页：从顶部
  「帮助 → 任务日志…」（`Ctrl+Shift+L`）以非模态窗口打开（共享 `LogDialog`），
  打开时回放最近 1000 行缓冲、之后按 `logLine` 信号实时追加，清空同时清缓冲。
- 发行版行在名称右侧注明对应的在线镜像：按「名称全等 → 友好名全等 → 名称是发行版名的
  词首前缀（多个候选取最长）」匹配镜像清单，命中显示「镜像 Ubuntu 24.04 LTS」（tooltip 给
  可用架构 / 来源），匹配不到显示「本地导入」，清单尚未加载时留空不误报。启动时先同步回显
  `wslman-available.json` 缓存，镜像清单更新后只就地刷新该说明，不重建行卡。
- 术语：`wslmanw` 展示层统一叫**实例**（即 WSL 发行版），页面 / 标题 / 对话框 / 状态文案
  都用「实例」；CLI 输出与代码模型仍是「发行版」/ `distro`，两者指同一对象。
- 启动实例用后台看护进程（`wsl.exe -d <名称> --exec /bin/sh -c
  "while :; do sleep 3600; done"`，`CREATE_NO_WINDOW` + `STARTF_USESHOWWINDOW/SW_HIDE`
  + 断开标准流，与 `_runner` 隐藏窗口同口径）：一次性命令
  （如 `/bin/true`）跑完 WSL 就把实例收回 Stopped，退出 wslmanw 后更会失去唯一进程。
  只有用户点「停止」（`wsl --terminate`）、「关闭全部」或 `wsl --shutdown` 才结束实例；
  关闭窗口自身不终止任何实例。不要用 `DETACHED_PROCESS`：它和 `CREATE_NO_WINDOW`
  互斥，两个一起给时 Windows 反过来会给这个控制台程序建一个**可见的空白控制台**
  （表现为启动后弹出一个一直黑屏的窗口）。启动是异步的（冷启动实测 0.7–2.6 秒才从 Stopped 迁移到
  Running），状态判定走 `wslman.wait_until_running`（20 秒上限、0.15 秒轮询），
  spawn 完立刻断言会误报「启动失败」。
- 实例页沿用 llmw 模型列表的行卡结构与布局，骨架直接用共享 `ItemRow`（见 ADR 0038）：
  行内左侧是「名称 + 副标题行（版本 + 候选在线镜像说明）+ 状态胶囊（运行中 / 已停止 /
  未知）+ 默认胶囊」——镜像说明与版本同排、吃掉剩余宽度（超长右侧省略），不与行尾胶囊
  挤在一起；行内启停按钮按状态换变体（运行中＝红色危险按钮「停止」，否则主按钮
  「启动」），禁用态图标色调退回 muted；
  右侧是「磁盘占用（估算值带 `≈`）+ 行内启动 / 停止按钮 + 配置 + 展开箭头」，详情
  （BasePath、虚拟磁盘路径、占用、默认用户、来源）通过行内「展开」就地显示（双击行
  等价，同一时间只展开一行），不再占用下方分栏；启停右侧的「配置」打开实例设置：
  「设为默认发行版」与「终端默认目录」（默认 `~`，可填 `/home/me/project` 这类 Linux
  路径，存储到 `wslmanw.json` 的 `distro_terminal_dirs`）；设为默认成功后补跑收尾
  刷新，默认胶囊立即换行，不等 3 秒轮询。「打开终端」按该目录传
  `--cd`（GUI 不走 CLI `--workdir` 的 Windows 路径校验，避免落到 Windows 当前目录的
  `/mnt/...` 映射）；工具栏保留打开终端 / 启动 / 停止 / 导出 /
  安装镜像（含行内「安装」已下载的归档）与 CLI 同一口径：导入前先按
  `mkdir(parents=True, exist_ok=True)` 建好目标目录（默认装到 `install/<名称>`，
  父目录可能尚未创建；`wsl --import` 只建最后一级，父目录缺失会报「找不到路径」）。
  下载 / 导出进度写在状态栏：右侧是确定进度条 + 短文案（百分比 / 速度），左侧状态
  文字给完整口径（已下载或已写 / 总量 / 速度 / 剩余时间）；其它后台任务在任务期间
  显示加载条，结束后收起。状态轮询只就地刷新行内状态 / 占用 / 按钮，
  名单不变时不重建列表，选中行与滚动位置不受影响；任务忙闲切换时行内启停按钮同步
  禁用 / 恢复（任务结束立刻恢复主按钮态，不等下一次轮询）。
  视觉上与 llmw 模型行完全一致，用的是共享行卡契约（`QFrame[itemRow="true"]`，见
  ADR 0038）：1px 边框 + 8px 圆角的卡片，hover / 选中加粗边框，选中底用半透明的
  `accent_soft_strong`；列表自身透明无边框（`QListWidget#DistroResultList`，与
  `ModelResultList` 同口径）。
  镜像页沿用同一套口径：自定义行卡（名称 + 友好名 / 架构 / 来源
  副标题 + 已安装 / 已备份 / sha256 胶囊 + 右侧下载或安装按钮）铺满整页宽度，
  详情与下载地址通过行内「展开」（`chevron-down` / `chevron-up` 图标）就地显示
  （双击行等价，同一时间只展开一行），
  「已安装 / 已下载」的镜像在任何排序下都置顶（档位：已安装 → 已下载 → 其它），
  排序口径只在同档位内生效，避免本地已有的条目被上百条在线镜像埋掉；
  多个条目共用同一个归档（如 `Ubuntu` 与 `Ubuntu-26.04` 的下载地址都指向
  `ubuntu-26.04.1-wsl-amd64.wsl`）时视为同一份镜像：全部标为已下载，且已安装 /
  已备份状态在这组条目间同步，不按清单顺序只标第一个；
  不再保留右侧详情面板；筛选胶囊（全部 / 可直链下载 / 仅安装 /
  已安装 / 已备份）、名称过滤（150ms 防抖）、排序（名称 / 友好名 / 已备份优先）、
  来源筛选（全部 / 官方清单 / CLI / Store）与「更新列表」（`collect_images(force=True)`
  跳过 30 分钟缓存，更新期间按钮禁用并显示「更新中…」）；筛选 / 排序 / 来源写入
  `wslmanw.json` 的 `image_filter` / `image_sort` / `image_source`，下次启动恢复。
- 实例配置对话框内嵌「初始化环境」卡片（纯逻辑在 `_initenv.py`）：
  勾选国内系统源 / SSH 密钥 / Python / Node.js / Rust，探测与安装都走
  `wsl.exe -d <实例> --exec bash -lc`（必须 `--exec`，否则 `$HOME` / `$(...)` 会被提前展开），
  `root=True` 的步骤加 `-u root`。打开即后台探测实例环境并按探测结果回填：
  **已就绪的项目保持勾选但禁用，并在勾选框右侧标注绿色「已初始化」胶囊**（`StatusTag`
  `tone="success"`）；未就绪的默认勾选、可自由增删，全选 / 全不选与「开始初始化」都只
  作用于未就绪项目，避免重复跑脚本。卡片默认收起，收起行只显示已初始化项目标签与
  行尾的 chevron 展开按钮（`chevron-down` / `chevron-up`，与列表行卡同口径）；
  「检测环境」「复制 SSH 公钥」「开始初始化」三个动作固定在卡片内，
  不随详情收起，收起态下也能直接操作；全选 / 全不选也放在这条常显动作行。
  展开后复用标题行显示一行摘要（收起行与摘要行互斥，不额外占高），正文是
  左右两栏（左：5 个勾选项；右：状态值两对一行，7 项压到 4 行）+ 日志
  （经 `EventBridge` 增量回填），状态值单行省略、完整内容进 tooltip，说明文字
  收在卡片 tooltip 里，展开高度只随内容增长。每个实例的勾选写入 `wslmanw.json`
  的 `distro_init_tasks`，下次打开沿用；展开 / 收起后下个事件循环再 `adjustSize`，
  窗口高度跟随内容回弹而不是单向变大。
- 同一对话框内另有「迁移 SSH 文件」卡片（纯逻辑在 `_sshfiles.py`）：来源可以是 Windows 侧
  `%USERPROFILE%\.ssh`（可在卡片里改选本地目录），也可以是**任一其它已安装 WSL 实例**的
  `~/.ssh`（下拉选择；当前实例不出现在来源里）。来源行切换类型 / 实例后清空旧结果并立即
  重扫，状态标签与判定口径完全跟随当前来源。跨实例扫描通过 `wsl.exe -d <实例> --exec
  bash -lc <探测脚本>` 读取文件清单，读取文件内容用 `wsl.exe -d <实例> --exec cat -- <路径>`
  走 stdout 文件流，不依赖实例内安装 rsync；来源实例未运行 / 不存在时明确报错，提示先在
  实例页启动（`wsl.exe` 通常也会自动拉起已停止的实例），不当成「没有文件」。目标侧固定为
  当前实例：要把当前实例的 SSH 推到别的实例，打开目标实例的配置、把来源选成当前实例即可，
  避免在卡片里再叠一层目标选择器。逐文件列出类型 / 大小 / 提示（config 里的 Windows 路径、
  `.ppk` 不支持等），未知类型默认不勾选。写入口径固定为目录 700、私钥 600、其余 644，
  写后回读 `stat` 校验权限，不符直接报错。实例内已有同名文件不静默覆盖：确认框逐条列出，
  确认后先备份到 `~/.ssh/fcmd-ssh-backup-<时间>/` 再写入；未确认时脚本以退出码 3 失败
  （扫描后新出现的同名文件同样拒绝）。文件内容经 stdin 送进目标 `wsl.exe`，命令行与日志
  只出现文件名与权限。卡片标题行的状态标签由每次扫描的实际结果推导（不记「点过按钮」）：
  来源为空给「无文件」，一个都没命中「未迁移」，部分命中「部分迁移」，全部存在且权限正确
  「已完成迁移」，全部存在但权限不符「权限待修正」；扫描失败时隐藏标签，迁移结束自动重扫
  刷新。
- 镜像页布局固定为「单行」结构：筛选胶囊 + 安装镜像 / 管理镜像 / 更新列表 → 搜索 + 排序
  + 来源 → 单行状态（`ElidedLabel`，垂直策略 Fixed）→ 行卡列表（吃满剩余空间、
  自身滚动）→ 下载动作行。状态行与动作行都是单行控件，窗口压到最小高度时不会被
  列表压扁 / 与列表重叠（换行标签 + 拉伸列表的组合在矮窗口下会被 Qt 压成一行并
  叠到下一行控件上）。
- 「管理镜像」对话框直接管理下载目录里的文件：列出完整镜像包与 ``.part`` 未完成
  下载（名称 / 大小 / 识别为 / 状态），支持导出（复制到选定目录）、删除、清理缓存
  （镜像清单缓存 + 全部 ``.part``）和打开目录；操作在对话框内即时刷新，关闭后重扫
  行内「已下载」状态。
- 设置页（`PageScroll` + 两张卡片）：**下载**（下载目录 / 备份目录 `PathRow` +
  更改 / 恢复默认、下载源、仅下一次下载生效的校验 sha256）与**配置文件**（界面设置
  `wslmanw.json` 与命令行配置路径）。下载 / 备份目录与下载源改动即时写回
  `wslmanw.json`（`download_dir` / `backup_dir` / `mirror`）；镜像页的下载动作与
  导出 / 备份的默认路径仍读设置页控件，两页共享同一份状态。窗口几何（`geometry`）
  在关闭时写回并在下次启动还原；实例配置、管理镜像、安装本地镜像三个对话框的
  位置 / 尺寸写入 `dialog_geometry`，再次打开在原处还原。
- 列表持久化与「陈旧回显」：进镜像页先同步读取
  `${FCMD_HOME}/cache/wslman-available.json`（`_images.load_cache()`）并立即渲染，
  状态行标注 `本地缓存 · 更新于 HH:MM`；随后仍后台刷新一次（`_images_from_cache`
  标记保证不会被「已有列表」短路），刷新成功后缓存文件由 `collect_images` 写入，
  重启即恢复上次清单。手动更新失败时保留旧列表并在状态行说明。
- 长任务在后台线程执行，`root.after` 轮询；「取消」置 `Event` 并终止子进程；
  关闭窗口时若有传输在跑，先确认，再终止子进程并按 CLI 同规则清理 `.part`。
- 破坏性操作：`unregister` 要求**手动输入发行版名**匹配才启用按钮，并展示
  BasePath 与占用；`shutdown` 需勾选「我明白会停止全部发行版」；两者用错误色按钮。
- 弹窗位置：Toplevel 首次打开时用 Qt 对话框按父窗口居中；固定尺寸弹窗显式传
  `width` / `height`，自动布局弹窗省略后按请求尺寸定位，不再交给窗口管理器摆放。
  可长期使用的对话框（实例配置 / 管理镜像 / 安装本地镜像）改用记忆几何：
  `dialog_geometry` 记上次的大小与位置，下次打开优先还原（越界时按屏幕夹紧）。
- GUI 与 CLI 调同一套 `_transfer` / `_source` / `_distro` 函数，不复制业务判断。

### D10.1 默认工作目录

GUI 与 CLI 的默认落盘位置统一到**一个工作目录** `${FCMD_HOME:-~/.fcmd}/wsl`，按用途细分：

| 子目录 | 用途 | 覆盖方式 |
|---|---|---|
| `download/` | 镜像归档（`.wsl` / `.tar`）默认下载目录 | 设置页「下载目录」→ `wslmanw.json` → `wslman.json` 的 `download.dir` |
| `install/` | 安装镜像的默认目录根，每个发行版一个子目录（`install/<名称>`） | 安装对话框里手改 |
| `backup/` | 导出 / 备份的 `.tar` 默认保存位置 | 设置页「备份目录」→ `wslmanw.json` → `wslman.json` 的 `backups.dir`（CLI 仍要求显式 `--dir`） |

- 解析函数集中在 `_config`（`work_root` / `default_download_dir` / `default_install_root` /
  `default_backups_dir`），都接受可选 `home` 便于单测；`_disk.recommended_backup_dir()`
  直接委托 `default_backups_dir()`，CLI 的 `doctor` 推荐与 GUI 默认因此一致。
- 设置页新增「工作目录」卡片：根目录（带「打开」）+ 下载 / 安装 / 备份三个子目录的只读
  展示，配合上方「下载」卡片的可覆盖下载目录，解释「默认写到哪里」。

### D10.2 用 IDE / AI Agent 打开当前目录

- 文件页两个入口「用 IDE 打开」「用 AI Agent 打开」作用于当前浏览目录；纯逻辑在
  `_open.py`，设置页「打开方式」卡片管理条目（下拉 + 运行位置 `StatusTag` +
  添加 / 编辑 / 删除），条目与选中项持久化到 `wslmanw.json` 的 `openers`。
- 条目 = 名称 + 运行位置（`windows` 宿主机 / `wsl` 实例内）+ 命令模板；占位符
  `{dir}`（实例内 POSIX 路径）、`{unc}`（Windows UNC 路径）、`{distro}`（实例名）、
  `{home}`（实例家目录）。Windows 宿主机条目必须含 `{dir}` 或 `{unc}`，WSL 内条目
  可不含占位符（在实例内直接执行）。
- 内置默认值（`note` 里同时标注不确定项）：
  - IDE：VSCode `code --remote wsl+{distro} "{dir}"`；Trae / Qoder 同参数（按 VS Code 系
    remote 参数估计）；WorkBuddy `workbuddy "{unc}"`（未确认官方 CLI）。
  - AI Agent：PI `pi`（命令名未确认）；OpenCode `opencode`（已确认 TUI）。
  - 默认选中 VSCode / OpenCode；内置项可编辑 / 删除，也可自行添加。
- Windows 宿主机：`shutil.which` 预检后按 argv 无窗口启动，`.cmd/.bat` 经
  `cmd.exe /d /s /c` 包装（避免 WinError 193）；WSL 内：先在实例内
  `bash -lc 'command -v <命令>'` 预检，再经终端 `wsl.exe -d <实例> --cd <目录>
  --exec bash -lc <模板>`（`_terminal.build_command` 的 `run` 参数）启动。
- 工具未安装 / 启动失败都会弹窗并在文件页状态行写明原因与处理建议，不静默失败。

### D11 配置

`~/.fcmd/wslman.json`（CLI 与 GUI 共用），字段级容错：

```json
{
  "schema": "wslman-config/1",
  "download": {"dir": "", "manifest_url": null, "sha256": {}},
  "transfer": {"temp_dir": "", "safety_margin": 1.1, "min_free_after_mb": 512,
               "keep_partial": false},
  "terminal": {"program": "auto", "extra_args": []},
  "safety": {"require_backup_on_replace": true},
  "backups": {"dir": ""}
}
```

- 缺失字段回落默认；未知字段忽略；单字段非法只回落该字段并给 `warn`；
  文件损坏 / 非 JSON / 非对象才整份回落默认（与 filescan 配置容错口径一致）；
- **v1 `wslman config` 只读**：只展示当前 `wslman.json` / `.wslconfig` 的解析结果与
  警告，不改写任何文件；写入能力留到后续版本单独决策；
- `backups.dir` 默认为空字符串：CLI 的 `backups` / `backup-*` 仍**必须显式指定**目录，
  避免悄悄写满系统盘；GUI 与 `doctor` 的推荐值统一指向工作目录的 `backup`
  （`~/.fcmd/wsl/backup`），`doctor` 只给目录与卷剩余空间，不代填配置；
- 写入复用 `fcmd.gui.config.write_settings`（该模块只依赖标准库，原子的临时文件 +
  `.bak`），CLI 不因此引入 GUI 依赖。

### D12 环境探测与无 WSL 环境

`preflight()` 返回 `Environment(wsl_present, enabled, version, distros, problems)`：

- `wsl.exe` 不在 PATH → `NOT_FOUND`：提示安装 WSL 或检查 PATH；
- 存在但功能未启用 / 服务不可用 → `RUNTIME_ERROR`：提示 `wsl --install` 或启用
  虚拟机平台后重启；
- 已启用但没有发行版：`list` 返回成功 + 空列表 + 一句提示（不是错误）；其他
  子命令给 `NOT_FOUND` 并提示 `wslman available`；
- 注册表不可读：只降级 `info` / `doctor`，`list` 仍可用。

`doctor` 检查项：平台、`wsl.exe` 可执行、`--version` 可解析、`-l -v` 可解析、
注册表可读、`DefaultDistribution` 与 `*` 一致、`.wslconfig` 可解析、默认版本、
备份目录。备份目录一项在 `backups.dir` 为空时报告推荐目录
（`%UserProfile%\wsl-backups`）与**所在卷剩余空间**，剩余不足 2 GiB 给 `warn`；
不自动创建目录、不自动写入配置。每项给出 `detail` 与可执行的 `hint`。

### D13 不变量

- 只读命令不启动任何发行版；`open` / `terminate` / `info --include-config` 是
  唯一会触及运行状态的非只读路径，且必须显式。
- 不写注册表、不写 `wsl.conf`、不写 `.wslconfig`。
- 破坏性命令必须 `--yes`；GUI 必须手输名称或勾选风险声明。
- 导出成功前不覆盖旧备份（`.part` + 原子改名）。
- 导入失败不自动注销；清理命令以文本给出。
- 所有外部副作用出现在 JSON `commands[]` 里，不含隐式调用。
- 单元测试不执行 `wsl.exe`；真实环境用例标 `slow`，不进主门禁。
- 下载源可配置，且未校验时不得标记 `verified`。

### D14 落地阶段（每阶段独立可提交、可回退）

| 阶段 | 内容 | 验收 |
|---|---|---|
| P1（已完成） | `_errors` / `_runner` / `_decode` / `_registry` / `_wslconfig` / `_config` / `_disk` / `_distro` + `list` / `status` / `info` / `config` / `doctor` | 147 项单测（假执行器）；未启用 WSL / 无发行版 / 无 `wsl.exe` 三种情况稳定退出码；`--json` 信封 |
| P2（已完成） | `_terminal` + `open` / `terminate` / `set-default` | 37 项单测（假执行器 + 假启动器）：argv 构造（含空格名）、`wt` 失败回退、`--preview` 零调用、双读 |
| P3（已完成） | `_transfer` + `export` / `import` / `copy` / `compress` / `backups` / `backup-verify` / `backup-remove` | 56 项单测：预检、进度、中断、`.part` 原子改名、导入残留提示、导出运行中二选一、preview 零副作用 |
| P4（已完成） | `_source` + `available` / `download` / `unregister` / `shutdown` | 47 项单测：来源降级阶梯、`wsl --install` 风险提示、sha256 判定、断点续传/中转 200 重启、注销强制先备份、preview 零调用 |
| P5（已完成） | `wslmanw` | 窗口骨架复用、后台任务与取消（取消信号透传 `_source.download`，`.part` 保留可续传）、破坏性确认（注销需手输名称、关闭全部需勾选影响）、导出复用 CLI 的预检与 `.part` 原子改名；导出进度经 Qt 信号桥写状态栏 |

每阶段跑 `make check-fast`，收尾跑 `make test`；覆盖率门禁 95%，新增纯逻辑模块的
单测不得低于现有平均水平。涉及 `apps/` 目录形状后由统一会话重生成
`cli/_manifest_data.py`。

## 风险与边界

- `--unregister` 删除虚拟磁盘且无回收站，只能靠 `--backup` 与手输确认兜底；
- `--shutdown` 会连带停掉 Docker Desktop、IDE Remote 等 WSL 后端，必须在提示中
  点名，不能只写「停止所有发行版」；
- 运行中导出可能得到不一致的数据，因此运行中必须显式二选一
  （`--terminate-first` 或 `--allow-running`），不自动停机、也不静默继续；
- `wsl.exe` 输出本地化、编码随版本波动，解析必须按结构而非文案；
- 注册表可能因权限或策略不可读，所有依赖它的字段都要可缺省；
- Store 版 WSL 会自动更新，能力探测必须每次运行现算，不能缓存成常量；
- 非管理员可完成导出 / 导入 / 注销，但 `set-version` 与 WSL 功能启用可能受虚拟化
  与策略限制；不静默提权。

## 已定稿的设计选择（2026-11-13）

以下九条由用户定稿，实现按此执行，不再作为开放问题：

1. `set-version` 归**破坏性**档，需要 `--yes`。
2. `terminate` 不需要 `--yes`（GUI 侧保留二次确认）。
3. `--replace` 保持**强制先备份**：没有可用备份就拒绝覆盖。
4. `download` 默认允许走 `wsl --install -d <Name> --no-launch`，但帮助与文档必须
   写明：会触发系统级安装、可能弹 UAC、且不能指定自定义镜像；要特定版本改用
   清单或本地文件。
5. `config` v1 保持**只读**。
6. 不引入 zstd，只用 `export` 的 tar 与可选 gzip。
7. 导出**不强制**先停机，但也不允许静默：运行中必须显式二选一
   `--terminate-first` / `--allow-running`，没有默认值，缺省即报错并给出两条完整命令。
8. GUI 注销要求**手输发行版名**，保留。
9. 备份默认目录保持空、必须显式指定；`doctor` 报出推荐目录与所在卷剩余空间。

## 交接：需要统一会话处理的共享改动

入口注册（本轮不改 `pyproject.toml`，实现阶段需要加入）：

```toml
wslman  = "fcmd.apps.wslman.wslman:main"
wslmanw = "fcmd.apps.wslman.wslmanw:main"
```

清单重生成（实现阶段在干净工作区执行）：

```powershell
uv run python -m fcmd.cli._manifest_data
```

测试镜像：`tests/apps/wslman/`，与 `src/fcmd/apps/wslman/` 一对一；CLI 用例不真跑
`wsl.exe`，真实环境用例标 `slow`。

## 后果

- 发行版事实有单一模型（`Distro`），CLI / GUI / JSON 三处呈现同源，避免各自解析；
- 破坏性操作在命令面与 GUI 上都能一眼分辨，且都有可复制的确认命令；
- 大文件传输有预检、有进度、可中断，且中断不会毁掉旧备份；
- 下载不绑定单一 URL，格式不可用时明确降级而不是假装成功；
- 代价是新增一个执行器抽象层与若干假实现，测试量增加，但换来了无 WSL 环境下
  的完整可测性与稳定的机读输出。

## 落地状态与交接

- 已完成：CLI 与 wslmanw 主窗口（实例 / 文件 / 镜像 / 设置四页）、启停与看护、
  导出 / 导入 / 注销、下载与校验，以及实例配置对话框里的「初始化环境」与
  「迁移 SSH 文件」两张卡片，均有单测覆盖。
- 文件页纯逻辑在 `_files.py`：UNC 浏览、上传下载、新建 / 重命名 / 删除，删除与
  覆盖都要确认；SSH 迁移纯逻辑在 `_sshfiles.py`（来源类型 / 跨实例 `cat` 文件流 /
  实际读取路径），真实实例（Ubuntu 24.04）上已用隔离 HOME 冒烟验证落盘权限、
  拒覆盖（退出码 3）与备份目录。
- 下一步：本轮改动尚未提交，提交前按 L2 跑 `make flow` 并更新受影响的文档；
  CI 仍负责 Linux 兼容性验证。
