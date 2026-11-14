# 0024 devman：Windows 开发环境管理（Node.js / Python 多版本）

- 状态：已实施（P1 CLI 已落地，P2 GUI 待实施）
- 日期：2026-11-14
- 相关文档：[ADR 0001](0001-layering.md)、[ADR 0003](0003-package-layout.md)、[ADR 0013](0013-gui-toolkit-feasibility.md)、[ADR 0023](0023-wslman.md)

## 背景

Windows 上并存多套 Node.js / Python 是常态，但现有做法各有缺口：

1. **装法不统一**：Node.js 有官方 zip、`.msi`、winget/choco、nvm-windows；
   Python 有官方安装器、embeddable zip、NuGet、conda/miniforge、uv 托管版本。
   前两者装完即改 PATH，多版本只能靠手工解压到不同目录再手改环境变量。
2. **切换没有统一语义**：nvm-windows 用符号链接接管 `node`，pyenv-win 用 shim，
   conda 用 `activate`，uv 用 `--python` 参数；混用时谁生效取决于 PATH 顺序。
3. **卸载留残留**：手工解压的版本目录、写进用户 PATH 的条目、`%APPDATA%\npm`
   的全局包都靠人记；卸载后 `where node` 还指向已删目录是常见状态。
4. **无冲突检测**：装完才发现新目录排在 conda 的 `python.exe` 之后，
   `python -V` 仍是旧版本，却没有任何提示。

本机实测现状（2026-11-14，只读探测）：

| 事实 | 值 |
|---|---|
| `where node` | `C:\Environment\node-v25.2.1-win-x64\node.exe`（v25.2.1，用户 PATH） |
| `where python` | `C:\Environment\miniforge3\python.exe`（conda）、`%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe`（商店别名） |
| `py` 启动器 | 不存在 |
| `uv` | `C:\Users\zhou\.local\bin\uv.exe`，0.13.1；托管 Python 在 `%APPDATA%\uv\python`（已装 3.13.14 / 3.14.6） |
| `winget` | 存在 |

`devman` 只做一件事：把「取一个独立版本的 node / python → 装进自己的目录 →
可切换 → 可完整卸载」收敛成一套默认安全、可机读、可脚本化的命令；
**不接管**用户已有的 node / python / conda / venv，也不碰系统级 PATH。

非目标（v1 明确不做）：

- 不改机器（Machine）级 PATH、不写 HKLM、不静默提权、不弹 UAC；
- 不管理 npm / yarn / pnpm 全局包与 `node_modules`；不设 `npm` prefix；
- 不创建 / 激活 / 删除 venv（venv 是 uv 的职责），不安装 pip 包；
- 不配置镜像源（`envdev` / `mirrortool` 的职责），devman 只读命令行里给的源；
- 不管理 conda / miniforge 环境，不替换 `py` 启动器的注册表项；
- 不做 Linux / macOS 上的安装（v1 仅 Windows；纯逻辑模块保持跨平台可测）；
- 不做后台自动升级检查，`upgrade` 必须显式调用。

## 决策

### D1 包布局与入口

新增 `src/fcmd/apps/devman/`，一层平铺，遵守 [ADR 0003](0003-package-layout.md) 与
`tests/test_layout.py`（模块不得深于 `apps/devman/<模块>.py`）：

| 文件 | 职责 |
|---|---|
| `__init__.py` | 包门面，不注册逻辑 |
| `_errors.py` | `DevmanError`、错误码常量、场景 → 错误码映射 |
| `_paths.py` | 根目录解析（`--root` > `DEVMAN_ROOT` > 默认）、布局路径、目录创建 |
| `_state.py` | `state.json` / `config.json` / 安装标记的读写（原子替换 + `.bak`） |
| `_net.py` | `Fetcher` 协议 + `UrllibFetcher`、sha256 校验、带进度的下载到 `.part` |
| `_archive.py` | zip 安全解压（拒绝绝对路径 / `..` / 符号链接），纯函数 |
| `_toolchain.py` | 版本模型 `Toolchain` / `installed_version` / 版本规格解析（`node@20` 等） |
| `_node.py` | Node.js 发布索引与 `SHASUMS256.txt` 解析、URL 组装、安装 / 卸载 |
| `_python.py` | uv 委托（`uv python list/install` 的 argv 构造与 JSON 解析）、安装 / 卸载、venv/conda 冲突识别 |
| `_env.py` | `which` / PATH 顺序、用户环境变量读写协议、shim 文本生成、shell 激活片段、冲突检测 |
| `_ops.py` | `install` / `uninstall` / `use` / `upgrade` 的状态机：预检、锁、回滚、dry-run 计划 |
| `devman.py` | CLI 子命令注册与双读输出 |
| `devmanw.py` | Tk 窗口（P2） |

入口注册（P1 写入 `pyproject.toml [project.scripts]`）：

```toml
devman  = "fcmd.apps.devman.devman:main"
devmanw = "fcmd.apps.devman.devmanw:main"
```

`devmanw` 在 P2 落地前不注册，避免出现必然 ImportError 的入口。

### D2 根目录与目录布局

根目录优先级：`--root <dir>` > `DEVMAN_ROOT` > `%LOCALAPPDATA%\devman`。
选 `%LOCALAPPDATA%` 而非 `%APPDATA%`：工具链是几十 MB 到几百 MB 的本机缓存，
不该跟着漫游配置文件同步；也不复用 `~/.fcmd`（其中是配置与 UI 设置，量级不同）。

```
<root>/
  config.json                       # 可选手工配置
  state.json                        # 活动版本、PATH 记录
  node/<version>/                   # node-v20.11.1-win-x64 解压后的内容
  node/<version>/.devman.json       # 安装标记（最后写）
  python/cpython-<ver>-windows-x86_64-none/   # uv 的 install-dir 布局
  python/cpython-<ver>-.../.devman.json
  active/node.txt                   # 单行：活动版本号
  active/python.txt                 # 单行：活动安装键
  bin/                              # shim：node.cmd / npm.cmd / npx.cmd / python.cmd / python3.cmd
  cache/downloads/                  # 安装包缓存（.part 与成品）
  .lock                             # 安装互斥锁
  .trash-<ver>-<ts>/                # 卸载中转目录，成功后删除
```

- 路径一律由 `_paths.py` 计算，业务代码不拼字符串；所有目录在**首次真正写入**时才创建，
  `--dry-run` / `list` / `doctor` 不建目录。
- 版本目录名 = 该生态的既有约定（node 是纯版本号，python 是 uv 的 key），
  不用 devman 自定义编码，便于用户直接进目录排查。

### D3 安装源与可信校验

| 工具 | 来源 | 校验 | 被否决的方案 |
|---|---|---|---|
| node | 官方 `https://nodejs.org/dist/v<ver>/node-v<ver>-win-<arch>.zip` | 同目录 `SHASUMS256.txt`（官方）比对 sha256 | `.msi`（要 UAC、写机器 PATH、无法多版本共存）；winget/choco（版本落在系统目录、卸载语义不可控）；nvm-windows（符号链接接管全局 `node`，与既有安装冲突） |
| python | `uv python install <ver> --install-dir <root>/python --no-bin --no-registry --managed-python` | uv 自带的 python-build-standalone 清单与 sha256；装完再跑 `<path>/python.exe -V` 复核版本 | 官方安装器（UAC + 注册表 + 写 PATH）；embeddable zip（无 pip、`._pth` 语义特殊、无 venv）；NuGet 包（版本滞后、校验链在 NuGet 侧）；自建 python-build-standalone 下载器（与 uv 重复实现、校验清单易漂移） |

约束：

- **校验和来源与构件来源分离**：node 的 zip 可以走镜像（`config.json` 的
  `node.mirror`，默认 `auto` = 官方优先），但 `SHASUMS256.txt` **只认官方**；
  官方清单不可达时拒绝安装，除非调用方显式给 `--sha256 <hex>`。
- 镜像只改下载 URL，不改「校验通过」的判定；输出里 `verify` 字段写明本次用的
  是 `official-shasums` 还是 `explicit-sha256`，不把镜像自带的校验标成官方已验证。
- Python 的下载源由 uv 决定（本机 uv 已指向 npmmirror 的 python-build-standalone），
  devman 不覆盖 uv 的源配置，只透传 `--mirror`（如调用方显式给）。
- 安装完成后写 `.devman.json` 记录 `source` / `sha256` / `installed_at`，
  `doctor` 用它与实际可执行文件做一致性检查。

### D4 版本模型与安装标记

- 安装完成的唯一判据：版本目录存在 **且** `.devman.json` 可解析 **且**
  标记里的 `tool`/`version` 与目录名一致 **且** 主可执行文件存在。
  三者缺一即「半完成」，`doctor` 报 `fail`，`list` 标 `incomplete`，重新
  `install` 会先清掉残目录再装（不需要人工删目录）。
- 标记内容（`devman-install/1`）：

```json
{
  "schema": "devman-install/1",
  "tool": "node",
  "version": "20.11.1",
  "source": "https://nodejs.org/dist/v20.11.1/node-v20.11.1-win-x64.zip",
  "sha256": "…",
  "verify": "official-shasums",
  "installed_at": "2026-11-14T10:00:00+08:00"
}
```

- 版本规格（`<tool>@<spec>`）：
  - 完整版本：`node@20.11.1` / `python@3.12.15`；
  - 前缀：`node@20`、`python@3.12` —— 在**已安装**集合里取最高匹配；
    `install` 时若已安装集合无匹配，则在远端索引里取最高匹配；
  - 通道：`latest`；（`lts` 仅 node，按官方 `index.json` 的 `lts` 字段判定）；
  - 非法规格 → `INVALID_ARGUMENT`（退出 3），错误信息给出合法示例。

### D5 PATH 与 shell 集成

三种方式对比后，**默认不改 PATH**：

| 方式 | 生效范围 | 风险 | 采用 |
|---|---|---|---|
| shim 目录 + 写用户 PATH | 新开的终端 | 会遮蔽已有 node/python，需显式授权 | `devman init --path`（显式、可逆） |
| shell hook（改 profile） | 只对该 shell | 改用户 profile 文件，跨 shell 各写一份 | 不默认；只打印片段供用户自行粘贴 |
| 进程内注入 PATH | 单条命令 | 无持久化副作用 | `devman exec`（默认） |

具体设计：

1. **shim**：`<root>/bin` 下生成 `.cmd` 包装器，内容只做「读活动版本文件 → 转发 argv」，
   纯文本、可单测。例（`node.cmd`，`python.cmd` 同构）：

```bat
@echo off
setlocal
set "DEVMAN_ACTIVE=%~dp0..\active\node.txt"
if not exist "%DEVMAN_ACTIVE%" exit /b 127
for /f "usebackq delims=" %%v in ("%DEVMAN_ACTIVE%") do set "DEVMAN_VERSION=%%v"
if not defined DEVMAN_VERSION exit /b 127
"%~dp0..\node\%DEVMAN_VERSION%\node.exe" %*
exit /b %ERRORLEVEL%
```

   选文本 shim 而不是目录联结（`mklink /J current\node`）：切换只需原子替换一行文本，
   不存在「旧联结删掉、新联结未建好」的窗口；shim 内容可完全离线单测，
   而联结需要 shell 外调且不可跨平台测试。代价是 `.cmd` 对个别只认 `*.exe`
   的构建脚本不透明——这类场景用 `devman exec` 或 `devman env` 的片段。

2. **PATH 写入**：`devman init --path [--append]` 只写 **HKCU\Environment\Path**
   （用户级，不需要管理员），默认前置；写前广播 `WM_SETTINGCHANGE`
   （与 `infras.system.persist_env` 同口径）。以下情形拒绝执行并给出原因：
   - 前置会遮蔽已存在的 node/python 且未给 `--allow-shadow`（列出被遮蔽的路径）；
   - 目标条目已在 PATH 中（幂等，成功返回 `changed=false`）。
3. **回滚**：`devman uninit --path` 只删除与 `<root>/bin` **完全相等**的条目，
   保留其余条目与顺序；PATH 被外部改过也照此处理。同时清掉 `state.json` 里的记录。
   不在 `uninit` 时整份恢复旧 PATH 快照（会把用户后续改动一起抹掉），快照只用于诊断。
4. **不改环境变量的场景**：`devman env --shell pwsh|cmd|bash` 打印把
   `<root>/node/<active>` 与 `<root>/python/<active>` 前置到 PATH 的一行命令；
   `devman exec -- <argv…>` 在子进程环境里做同样的事，当前 shell 不变。
   两者都不写注册表、不落盘。
5. **不使用系统级 PATH、不使用 UAC**：所有写入落在 HKCU 与 `<root>` 内。

### D6 多版本共存与切换语义

- 多版本共存 = 版本目录并列；互不覆盖。同一工具可同时装任意多个版本。
- 切换 = 原子替换 `<root>/active/<tool>.txt`（先写 `.tmp` 再 `os.replace`），
  不动目录、不改 PATH、不重启终端；shim 下一次调用即生效，
  `devman exec` / `devman env` 立刻生效（每次都读文件）。
- `use` 前置校验：目标版本必须「已安装且完整」，否则 `NOT_INSTALLED`（退出 4），
  提示 `devman install <spec>`；不允许把活动版本切到半完成目录。
- `use --clear <tool>` 清空活动版本（shim 退出码 127 并提示先 `devman use`，
  不静默回退到某个版本）。
- 已激活虚拟环境时（`VIRTUAL_ENV` / `CONDA_PREFIX` 存在），`use` 照常改活动版本，
  但提示：当前 shell 的 venv/conda 激活会优先于 devman，需要 `deactivate` 后
   或在新终端里生效。devman 不主动改 `VIRTUAL_ENV`。

### D7 与既有 node / python / uv / venv 的边界

**Node.js**：devman 只写 `<root>/node/**` 与自己生成的 shim；不碰
`%APPDATA%\npm`、不碰 `C:\Environment\node-v25.2.1-win-x64`、不碰 nvm-windows
的 `NVM_HOME` / `NVM_SYMLINK`。

**Python 与 uv 的关系**（关键边界）：

- devman 把 uv 当作**下载器**：`uv python install <ver> --install-dir <root>/python
  --no-bin --no-registry --managed-python`。`--no-registry` 确保不写
  `HKCU\Software\Python`（即不让 `py` 启动器看到 devman 的解释器），
  `--no-bin` 确保不在 `~/.local/bin` 留链接，`--install-dir` 确保装到 devman 根下。
- `UV_PYTHON_INSTALL_DIR` 只作为**子进程环境变量**传入，不持久化；uv 自己的默认
  安装目录（本机 `%APPDATA%\uv\python`）与既有安装完全不动。
- **venv 仍是 uv 的事**：devman 不创建、不激活、不删除 venv。`use python@3.12` 后，
  推荐用法由 `doctor` 与 `use` 的输出直接给出：
  `uv venv --python <root>\python\cpython-3.12.15-windows-x86_64-none\python.exe`。
- `devman` 不修改 `UV_PYTHON` / `UV_MANAGED_PYTHON` / `UV_NO_PYTHON_DOWNLOADS`。

**冲突检测**（`list` / `doctor`，只读）：

- PATH 顺序解析：对 `node`、`python`、`npm` 逐条列出「第几顺位 → 哪个路径」，
  标出 devman shim 的位置与是否会被更前面的条目遮蔽；
- 环境标记：`VIRTUAL_ENV`、`CONDA_PREFIX`、`UV_PYTHON`、`NVM_HOME`、`NVM_SYMLINK`；
  命中即给 `warn` 与一句「devman 不会接管它」；
- 常见安装位置探测：`%LOCALAPPDATA%\Programs\Python\Python*`、
  `%ProgramFiles%\nodejs`、`%APPDATA%\npm`、`~/.pyenv`；
- 探测结果只在输出里展示，不自动修改任何外部配置，也不建议删除。

### D8 卸载边界

`devman uninstall <spec>`：

1. 目标不是活动版本 → 直接把版本目录改名进 `<root>/.trash-<ver>-<ts>` 再递归删除
   （Windows 上先改名能立刻让「已安装」判定失效，也不怕删到一半）。
2. 目标是活动版本 → 默认拒绝（`INVALID_INPUT`，提示先 `devman use` 到别的版本）；
   `--force` 时先清空 `active/<tool>.txt`，再按 1 处理。
3. 目标不存在 → `NOT_INSTALLED`（退出 4）；加 `--if-present` 则成功且 `changed=false`。
4. 保留：其余版本、`config.json`、`cache/downloads`（`--purge-cache` 才清）、
   `<root>/bin` 与 PATH 记录（`uninit --path` 才动）。
5. 明确不删：`%APPDATA%\npm` 里的全局包、用户自己的 venv、
   `%APPDATA%\uv\python` 下的 uv 托管版本、conda 环境。
   `uninstall` 的输出会写一句「npm 全局包不在 devman 管理范围」。

### D9 失败、半完成安装与回滚

安装流水线（node；python 把 1–3 换成 uv 调用）：

1. 取 `<root>/.lock`（`O_CREAT|O_EXCL` + 记录 pid/时间，超过 30 分钟视为陈旧锁）；
2. 下载到 `cache/downloads/<file>.part`，进度回调只在人类可读模式输出；
3. 校验 sha256（不匹配 → `CHECKSUM_MISMATCH`，删除 `.part`，退出 5）；
4. 解压到 `node/.staging-<ver>-<rand>`（拒绝 `..` / 绝对路径 / 链接成员）；
5. 跑一次 `<staging>/node.exe -v` 复核版本（可注入的执行器，单测不跑真二进制）；
6. `os.replace(staging, node/<ver>)` 原子落位；
7. 最后写 `.devman.json` 标记；任一步失败都只删 staging / `.part`，
   **不写标记、不动活动版本**，因此不会留下「看起来装好了」的目录。

- 中断（Ctrl+C）：捕获 `KeyboardInterrupt` → 清理临时目录 → `INTERRUPTED`（130）。
- 锁被占用：`BUSY`（退出 1），提示另一个 devman 正在安装及锁文件路径。
- `use` 只写一行文件，失败即原活动版本不变（写 `.tmp` + `os.replace`）。
- `upgrade` = `install` 新版本 → `use` 新版本 →（`--purge-old` 且 `--yes`）卸载旧版本；
  前三步任一步失败都停在「旧版本仍是活动版本」的状态，不会出现两不沾。

### D10 命令面与机读契约

| 子命令 | 关键参数 | 副作用 |
|---|---|---|
| `list` | `--json` | 只读：已安装版本、活动版本、外部探测、shim 状态 |
| `versions <tool>` | `--remote`、`--prerelease`、`--json` | 只读（`--remote` 联网读索引，不下载构件） |
| `install <spec>` | `--dry-run`、`--json`、`--sha256`、`--force` | 安装；`--dry-run` 零副作用 |
| `uninstall <spec>` | `--yes`、`--dry-run`、`--json`、`--if-present`、`--all`、`--purge-cache` | 删除版本目录；必须 `--yes` |
| `use <spec>` / `use --clear <tool>` | `--json` | 改 `active/<tool>.txt` |
| `upgrade <tool>` | `--to <spec>`、`--purge-old`、`--yes`、`--dry-run`、`--json` | 安装 + 切换（+ 可选卸载旧版） |
| `init` | `--path`、`--append`、`--allow-shadow`、`--dry-run`、`--json` | 生成 shim；`--path` 时写 HKCU PATH |
| `uninit` | `--path`、`--dry-run`、`--json` | 删 shim 目录与 PATH 条目 |
| `env` | `--shell pwsh\|cmd\|bash`、`--json` | 只打印 |
| `exec` | `-- <argv…>` | 起子进程 |
| `doctor` | `--json` | 只读；有 `fail` 项时退出 5 |

公共参数（全部子命令）：`--root <dir>`、`--json`（函数签名 `json_output: bool = False`，
沿用仓库 `--json` 别名约定）。破坏性操作 `--yes` 缺失 → `CONFIRM_REQUIRED`（退出 2）；
`--dry-run` 一律不建目录、不下载、不写注册表与状态文件。

机读输出沿用 [core/result.py](../../src/fcmd/core/result.py)：

- 成功：`emit_result("devman", "<子命令>", data)`，`data.schema = "devman/<子命令>/1"`，
  安装类命令另带 `actions[]`（实际执行的 argv / 下载 URL / 目标路径）、
  `changed`、`duration_ms`、`destructive`/`confirmed`（适用时）；
- 失败：`report_error(...)`，人读与机读共用同一退出码；
- `doctor` 的 `checks[]` 每项 `{name, status: ok|warn|fail, detail, hint}`，
  有 `fail` 时 `emit_result(..., ok=False)` + 退出 5，不写 stderr 错误体。

新增错误码（必须在 [core/exit_codes.py](../../src/fcmd/core/exit_codes.py) 的
`ERROR_CODE_KINDS` 登记，否则会回退成 `input`）：

| 场景 | `code` | `kind` | 退出码 |
|---|---|---|---|
| 未知工具名 | `UNKNOWN_TOOL` | usage | 2 |
| 缺 `--yes` | `CONFIRM_REQUIRED` | usage | 2 |
| 规格不合法 / 参数冲突 | `INVALID_ARGUMENT` | input | 3 |
| 目标版本不存在（本地或远端） | `VERSION_NOT_FOUND` | not_found | 4 |
| 目标版本未安装 | `NOT_INSTALLED` | not_found | 4 |
| 官方校验清单不可达 | `CHECKSUM_UNAVAILABLE` | runtime | 1 |
| sha256 不匹配 | `CHECKSUM_MISMATCH` | check | 5 |
| 下载 / 解压失败 | `DOWNLOAD_FAILED` | runtime | 1 |
| uv 不存在或调用失败 | `UV_UNAVAILABLE` | runtime | 1 |
| 根目录不可写 | `ROOT_NOT_WRITABLE` | runtime | 1 |
| 另一个安装进行中 | `DEVICE_BUSY` | runtime | 1 |
| PATH 写入 / 回滚失败 | `PATH_WRITE_FAILED` | runtime | 1 |
| 半完成安装需修复 | `INCOMPLETE_INSTALL` | check | 5 |
| 用户 Ctrl+C | `INTERRUPTED` | interrupted | 130 |

### D11 配置与状态文件

`<root>/config.json`（可选，缺失即全默认；字段级容错，非法单字段只回落实字段并给 `warn`）：

```json
{
  "schema": "devman-config/1",
  "node": {"mirror": "auto"},
  "python": {"uv": "auto", "mirror": null},
  "network": {"timeout_s": 120, "progress": true}
}
```

`<root>/state.json`（devman 自己写；损坏时按「无活动版本 + 无 PATH 记录」处理并给 `warn`）：

```json
{
  "schema": "devman-state/1",
  "active": {"node": "20.11.1", "python": "cpython-3.12.15-windows-x86_64-none"},
  "path_added": ["C:\\Users\\<user>\\AppData\\Local\\devman\\bin"],
  "path_previous_user_path": "…",
  "updated_at": "2026-11-14T10:00:00+08:00"
}
```

- 写入复用 `fcmd.gui.config.write_settings` 的「临时文件 + `os.replace` + `.bak`」口径
  （该模块只依赖标准库，CLI 不因此引入 tkinter）；
- `path_previous_user_path` 只用于 `doctor` 展示与人工排查，不在 `uninit` 时整体回滚。

### D12 GUI（`devmanw`，P2）

- 复用 `fcmd.gui.window` 的 `create_root` / `start_worker` / `create_log` 与
  `fcmd.gui.theme`；设置持久化 `~/.fcmd/ui/devmanw.json`（`fcmd.gui.config`）。
- 分页（沿用 llmservew 的分页经验，避免表单堆叠）：
  「已安装」版本表（工具 / 版本 / 活动 / 大小 / 完整性）＋切换按钮；
  「可用版本」按工具筛选、勾选安装、显示下载源与校验方式；
  「环境」展示 PATH 顺序、外部 node/python、venv/conda 状态与 shim 开关；
  「日志」实时回显安装进度。
- 长任务在后台线程执行，`root.after` 轮询；「取消」置 `Event`，安装走与 CLI
  同一套 `_ops`（含回滚），不在 GUI 里复制业务判断。
- 破坏性操作：卸载活动版本要求手输版本号；改用户 PATH 的开关需勾选风险说明，
  并展示「将被遮蔽的可执行文件」。
- GUI 与 CLI 共用 `_ops` / `_env` / `_state`，不新增第二套判定。

### D13 不变量

- 不写 Machine 级 PATH / HKLM，不发起 UAC 提权；所有持久化写入落在 `<root>` 与 HKCU。
- 不删除 `<root>` 之外的任何文件；`uninstall` 只动 `<root>/node`、`<root>/python`。
- 不改动既有 node / python / conda / venv 的目录、注册表项与配置文件。
- `--dry-run` 零副作用：不建目录、不下载、不写状态、不写注册表。
- 先校验 sha256 再解压；校验不通过不落位、不写标记。
- 安装标记后写；没有标记的目录一律视为半完成，不参与 `use`。
- 所有外部副作用出现在 JSON `actions[]` 或 `commands[]` 里，无隐式调用。
- 单元测试不联网、不执行真 `node` / `uv` / 注册表写入：`Fetcher`、`Runner`、
  `EnvStore` 全部可注入假实现；真机用例标 `slow`，不进主门禁。

### D14 落地阶段与验收

| 阶段 | 内容 | 验收 |
|---|---|---|
| P1（本轮） | `_errors` / `_paths` / `_state` / `_net` / `_archive` / `_toolchain` / `_node` / `_python` / `_env` / `_ops` + 全部 CLI 子命令 + 入口注册 + 清单重生成 | 单测覆盖纯逻辑与错误路径（假 `Fetcher` / `Runner` / `EnvStore`）；`--json` 信封、退出码、`--dry-run` 零调用断言；真机隔离验证通过 |
| P2 | `devmanw` Tk 窗口 | 复用 `fcmd.gui`；后台安装可取消、失败可回滚；破坏性操作二次确认 |
| 后续（不在 v1） | Linux/macOS、后台升级检查、npm 全局包纳管 | 另立 ADR |

P1 真机验证（`%TEMP%\devman-verify-<ts>` 作 root，不碰任何既有安装）：

1. 记录基线：`where node` / `where python` / `py -0p` / 用户 PATH / 系统 PATH /
   `uv python list` / `node -v` / `python -V`；
2. `devman --root … install node@<ver> --json`、`install python@<ver> --json`；
3. `list --json`、`versions node --remote`，确认版本可见且标记完整；
4. `use node@<ver>` + `exec -- node -v`、`use python@<ver>` + `exec -- python -V`，
   确认切换生效；`use` 回旧版本再验一次；
5. `uninstall <spec> --yes` 两个工具各一次，确认版本目录消失、活动版本清空、
   `<root>/bin` 不再指向已删目录；
6. 逐项对比基线：用户 / 系统 PATH 未变、`where node` / `where python` 未变、
   `node -v` / `python -V` 未变、`uv python list` 未新增条目；
7. `init --path --dry-run --json` 只输出计划，注册表写入路径另用假 `EnvStore` 单测覆盖。

P1 验收记录（2026-11-14，root = `%TEMP%\devman-verify-20261114-104606`）：

| 项 | 结果 |
|---|---|
| `install node@20.11.1` | 官方 `node-v20.11.1-win-x64.zip`，官方 `SHASUMS256.txt` 校验通过 |
| `install python@3.12` | 经 uv 安装 `cpython-3.12.15-windows-x86_64-none` 到隔离目录 |
| `use` + `exec` | 分别输出 `v20.11.1` / `3.12.15`，执行文件位于隔离 root |
| 外部环境 | `node -v` 仍为 v25.2.1、`python -V` 仍为 3.13.13；HKCU `Path` 与基线逐字一致；`uv python list` 既有条目未变 |
| 卸载 | 版本目录、`active/*.txt`、uv junction 别名均清空；node root 为空，python root 仅剩 uv 自身元数据（`.temp` / `.gitignore` / `.lock`） |
| `doctor` | 仅提示 `shim_on_path`（默认不改 PATH）与 conda 解释器存在 |
| 单测 | `tests/apps/devman` 220 项通过，覆盖率 98%；`ruff` / `pyrefly` 0 错误 |

## 风险与边界

- Node 官方站点在部分网络下不可达：镜像可下构件，但校验清单只认官方；
  不可达时安装失败并提示用 `--sha256` 显式指定，不降级成「镜像说对就算对」。
- `.cmd` shim 对只认 `*.exe` 的构建脚本不透明；这类场景用 `devman exec` / `env`
  把真实版本目录加进 PATH。
- 前置 shim 目录会遮蔽已有 node/python；`init --path` 默认拒绝遮蔽，必须显式
  `--allow-shadow`，且输出逐条列出被遮蔽的路径。
- npm 全局包按 npm 自己的 prefix 落盘（Windows 默认 `%APPDATA%\npm`），
  卸载版本不会清掉；`doctor` 提示该目录但不动它。
- uv 的版本索引随其发布节奏变化（含预发布），`install` 只接受稳定版，
  `--prerelease` 才放开；python 远端列表直接解析 `uv python list --output-format json`，
  uv 输出格式变化时降级为「只列已安装」并给提示，而不是猜文本。
- 用户手工删目录 / 改 `active/*.txt` 会造成状态不一致：`doctor` 报 `fail` 并给出修复命令，
  `use` / `install` 遇到时按实际情况修正，不假定状态文件为真。
- 半完成目录在 Windows 上可能被占用（文件锁）而删不干净：改名进 `.trash-*` 后
  尽力删除，残留由 `doctor` 列出并提示重试，不阻塞后续安装。

## 后果

- node / python 的「装、切、卸」有单一模型（版本目录 + 标记 + 活动文件），
  CLI / JSON / 后续 GUI 三处同源，避免各自判定；
- 默认不动 PATH、不动既有解释器，切换只是原子写一行文件，
  彻底卸载只需删两个目录；
- 借 uv 取 Python，避免重复实现 python-build-standalone 下载与校验，
  也天然与 venv 分工（devman 供解释器，uv 建环境）；
- 代价是多了一层目录约定与 shim 机制，且 `.cmd` shim 对少数只认 `.exe` 的工具不透明；
  这两点都在 `doctor` 与 `env` 输出里显式说明。
