# 0023 wslman：Windows 上的 WSL 发行版管理

- 状态：P1 已落地（只读 CLI：`list` / `status` / `info` / `config` / `doctor`）；P2–P5 待实现
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

`wslman`（CLI）与 `wslmanw`（Tk GUI）只做一件事：把上述能力收敛成一套可脚本化、
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
| `wslmanw.py` | Tk 窗口，复用 `fcmd.gui` 主题与设置持久化 |

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

`--cwd` 必须是 Windows 路径（WSL 内部路径用户自行传 `--cd` 语义），含空格时依赖
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
  允许 100%，避免提前报满；
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

- 复用 `fcmd.gui.window.create_root` / `start_worker` / `create_log` 与
  `fcmd.gui.theme`；设置持久化 `~/.fcmd/ui/wslmanw.json`（`fcmd.gui.config`）。
- 布局：左侧发行版表（名称 / 状态 / 版本 / 默认 / 占用），右侧详情（BasePath、
  默认用户、vhdx 路径与大小、`.wslconfig` 摘要），底部日志与进度条；
  「下载」为独立分页，避免与发行版操作抢空间（沿用 llmdownw 的分页经验）。
- 长任务在后台线程执行，`root.after` 轮询；「取消」置 `Event` 并终止子进程；
  关闭窗口时若有传输在跑，先确认，再终止子进程并按 CLI 同规则清理 `.part`。
- 破坏性操作：`unregister` 要求**手动输入发行版名**匹配才启用按钮，并展示
  BasePath 与占用；`shutdown` 需勾选「我明白会停止全部发行版」；两者用错误色按钮。
- GUI 与 CLI 调同一套 `_transfer` / `_source` / `_distro` 函数，不复制业务判断。

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
- `backups.dir` 默认为空字符串 = **必须显式指定**备份目录，不提供隐式默认，避免
  悄悄写满系统盘；`doctor` 只给推荐目录与卷剩余空间，不代填；
- 写入复用 `fcmd.gui.config.write_settings`（该模块只依赖标准库，原子的临时文件 +
  `.bak`），CLI 不因此引入 tkinter。

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
| P2 | `_terminal` + `open` / `terminate` / `set-default` | argv 构造单测（含空格名）、回退路径、双读 |
| P3 | `_transfer` + `export` / `import` / `copy` / `compress` | 预检、进度、中断、`.part` 原子改名、导入残留提示；导出运行中二选一 |
| P4 | `_source` + `download` | 来源降级阶梯、`wsl --install` 风险提示、sha256 判定、断点续传状态机 |
| P5 | `wslmanw` | 窗口骨架复用、后台任务与取消、破坏性确认（注销需手输名称） |

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
