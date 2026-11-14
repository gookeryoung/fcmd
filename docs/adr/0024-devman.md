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

**默认写入用户级 PATH**（HKCU，无 UAC、幂等），`--no-path` 关闭：

| 方式 | 生效范围 | 风险 | 采用 |
|---|---|---|---|
| shim 目录 + 写用户 PATH | 新开的终端 | 会遮蔽已有 node/python，需显式授权 | `install` / `upgrade` / `init` 默认；`--no-path` 关闭 |
| shell hook（改 profile） | 只对该 shell | 改用户 profile 文件，跨 shell 各写一份 | 不默认；只打印片段供用户自行粘贴 |
| 进程内注入 PATH | 单条命令 | 无持久化副作用 | `devman exec`（默认） |

采用默认写入的理由：P1 的目标是「装完打开新终端就能用」。若把 PATH 集成留给用户手工
执行，首次安装会在最后一步断掉，且用户很难判断该加哪个目录。写入范围严格限制在
HKCU 用户级 PATH（不碰系统 PATH、不触发 UAC），且只记录、只回滚 devman 自己新增的
条目（见第 3 点），因此默认开启的风险可控；需要完全零持久化的用户随时可用 `--no-path`。

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

2. **PATH 写入（默认）**：`install` / `upgrade` 成功安装后、`init` 默认把
   `<root>/bin` 写入 **HKCU\Environment\Path**（用户级，不需要管理员），默认前置
   （`init --append` 可追加到末尾），写后广播 `WM_SETTINGCHANGE`
   （与 `infras.system.persist_env` 同口径）。`--no-path` 跳过 shim 生成与 PATH 写入，
   只装版本。
   - **遮蔽校验**：新增条目会遮蔽已存在的 node/python 时默认拒绝（列出被遮蔽的路径与
     提示 `--allow-shadow`）；条目已在 PATH 中（幂等重装）不再重复校验，成功返回
     `changed=false`。
   - **写入顺序**：先装版本（失败即中止，不写 PATH），再生成 shim，最后写 PATH；
     PATH 写入失败时版本仍可用 `devman exec` / `devman env`，错误码 `PATH_WRITE_FAILED`。
3. **回滚**：`state.json` 的 `path_added` **只记录 devman 实际新增的条目**（PATH 里原本
   就有、或本次写入失败的不记）。`uninit` 默认、`uninstall` 卸掉最后一个版本时，
   按 `;` 段精确移除这些条目：保留其余条目、顺序、空段与 `%VAR%` 原文，逐字回到
   写入前的文本；不整份恢复旧 PATH 快照（会把用户后续改动一起抹掉），快照只用于诊断。
   未被 `path_added` 记录的条目（用户手工添加）不动。
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
   `<root>/bin` 与用户 PATH 条目（还有其他版本时保留）。卸掉最后一个版本
   （node 与 python 都没有了）时，只移除 `state.json` 里 `path_added` 记录的条目，
   用户手工添加的条目不删；`uninit` 随时可完整撤销 shim 与 PATH。
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
| `list` | `--json` | 只读：已安装版本、活动版本、外部探测、shim 状态、项目绑定 |
| `versions <tool>` | `--remote`、`--prerelease`、`--json` | 只读（`--remote` 联网读索引，不下载构件） |
| `install <spec>` | `--dry-run`、`--json`、`--sha256`、`--from <zip>`、`--force`、`--no-path`、`--allow-shadow` | 安装 + 默认写 HKCU PATH；`--from` 离线导入本地 node zip；`--dry-run` 零副作用 |
| `uninstall <spec>` | `--yes`、`--dry-run`、`--json`、`--if-present`、`--all`、`--purge-cache` | 删除版本目录；必须 `--yes`；删掉最后一个版本时清理 devman 写入的 PATH 条目 |
| `use <spec>` / `use --clear <tool>` | `--json`、`--project` | 改 `active/<tool>.txt`；`--project` 只写当前目录的版本文件 |
| `upgrade <tool>` | `--to <spec>`、`--purge-old`、`--yes`、`--dry-run`、`--json`、`--no-path`、`--allow-shadow` | 安装 + 切换（+ 可选卸载旧版）；默认写 PATH |
| `init` | `--no-path`、`--append`、`--allow-shadow`、`--dry-run`、`--json` | 生成 shim + 默认写 HKCU PATH |
| `uninit` | `--no-path`、`--dry-run`、`--json` | 删 shim + 精确移除 devman 写入的 PATH 条目 |
| `env` | `--shell pwsh\|cmd\|bash`、`--json` | 只打印；有项目绑定时按项目版本输出 |
| `exec` | `-- <argv…>` | 起子进程；有项目绑定时子进程用项目版本 |
| `doctor` | `--json` | 只读；有 `fail` 项时退出 5；含 `project` 检查项 |
| `size` | `[tool]`、`--json` | 只读：版本目录 / 缓存 / shim 的占用与文件数 |
| `verify` | `[tool]`、`--json` | 只读：标记、可执行文件、活动版本、shim、残留；有问题退出 5 |
| `repair` | `[tool]`、`--yes`、`--dry-run`、`--purge-incomplete`、`--json` | 重建 shim、清残留、清空损坏的活动版本；必须 `--yes` |
| `export [output]` | `--force`、`--dry-run`、`--json` | 只读（写锁文件）：导出可复现清单 |
| `restore <lock>` | `--yes`、`--dry-run`、`--no-path`、`--allow-shadow`、`--json` | 按锁文件安装 + 切换；必须 `--yes` |

公共参数（全部子命令）：`--root <dir>`、`--json`（函数签名 `json_output: bool = False`，
沿用仓库 `--json` 别名约定）。破坏性操作 `--yes` 缺失 → `CONFIRM_REQUIRED`（退出 2）；
`--dry-run` 一律不建目录、不下载、不写注册表与状态文件。

机读输出沿用 [core/result.py](../../src/fcmd/core/result.py)：

- 成功：`emit_result("devman", "<子命令>", data)`，`data.schema = "devman/<子命令>/1"`，
  安装类命令另带 `actions[]`（实际执行的 argv / 下载 URL / 目标路径）、
  `changed`、`duration_ms`、`destructive`/`confirmed`（适用时）；
  涉及 PATH 的子命令（`install` / `upgrade` 透传 / `init` / `uninit` / `uninstall`）
  带 `user_path: {enabled, changed, entry, position, shadowed}`，可直接判断本次是否
  写了 PATH（`changed`）以及是否因 `--no-path` 跳过（`enabled=false`）；
- 可运维五命令的 schema 分别是 `devman/size/1`、`devman/verify/1`、`devman/repair/1`、
  `devman/export/1`、`devman/restore/1`，锁文件本体为 `devman-lock/1`；
  `verify` / `repair` 额外带 `ok`，`ok=false` 时沿用「有检查项未通过」的退出 5；
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
  "network": {"timeout_s": 120, "progress": true, "proxy": null}
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
- 分页（沿用 locllmw 的分页经验，避免表单堆叠）：
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
- 默认写 HKCU 用户 PATH（`install` / `upgrade` / `init`），`--no-path` 可完全关闭；
  只把 devman 实际新增的条目记入 `state.path_added`，撤销时只移除这些条目，
  其余 PATH 文本（顺序、空段、`%VAR%`）逐字保留。
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
7. `install --dry-run --json` / `init --dry-run --json` 只输出计划（含 `user_path`
   的 `already_present` / `shadowed`），注册表写入路径另用假 `EnvStore` 单测覆盖。

P1 验收记录 A（2026-11-14 首次落地，root = `%TEMP%\devman-verify-20261114-104606`，
当时 PATH 集成默认关闭）：

| 项 | 结果 |
|---|---|
| `install node@20.11.1` | 官方 `node-v20.11.1-win-x64.zip`，官方 `SHASUMS256.txt` 校验通过 |
| `install python@3.12` | 经 uv 安装 `cpython-3.12.15-windows-x86_64-none` 到隔离目录 |
| `use` + `exec` | 分别输出 `v20.11.1` / `3.12.15`，执行文件位于隔离 root |
| 外部环境 | `node -v` 仍为 v25.2.1、`python -V` 仍为 3.13.13；HKCU `Path` 与基线逐字一致；`uv python list` 既有条目未变 |
| 卸载 | 版本目录、`active/*.txt`、uv junction 别名均清空；node root 为空，python root 仅剩 uv 自身元数据（`.temp` / `.gitignore` / `.lock`） |
| `doctor` | 仅提示 `shim_on_path`（当时默认不改 PATH）与 conda 解释器存在 |
| 单测 | `tests/apps/devman` 220 项通过，覆盖率 98%；`ruff` / `pyrefly` 0 错误 |

P1 验收记录 B（2026-11-14 改为默认写 PATH，root = `%TEMP%\devman-verify-53w22gr9`）：

| 项 | 结果 |
|---|---|
| 基线 | `winreg` 读出 HKCU `Path` 原文（REG_EXPAND_SZ，23 段，含 `%PNPM_HOME%` / `%USERPROFILE%`），记录全部用户变量及其类型 |
| 默认拒绝遮蔽 | `install node@22`（无 `--allow-shadow`）退出 3，stderr 列出被遮蔽的 `node.exe` / `python.exe` / 商店别名，HKCU `Path` 不变 |
| `install node@22 --allow-shadow` | 装成 22.23.5；`--json` 的 `data.user_path = {changed: true, position: "prepend", entry: "<root>\\bin"}`；HKCU `Path` 变为 `<root>\bin;<原值>`，其余段逐字不变 |
| 新终端可见 | 按「机器 PATH + 用户 PATH」拼出的新登录环境：`where node` 首行为 `<root>\bin\node.cmd`，`node -v` = `v22.23.5`；`install python@3.12` + `use` 后 `python -V` = `Python 3.12.15` |
| `uninit` | shim 目录删除；HKCU `Path` 与基线**逐字一致**；`Path` 之外的用户变量（值 + 类型）零差异；同一新登录环境 `node -v` 回到 `v25.2.1` |
| `init` + 卸载 | `init` 重新写入（`user_path.changed=true`）；卸 node 后仍有 python 时条目保留（`changed=false`）；卸掉最后一个版本后 `user_path.removed=1`，`Path` 回到基线 |
| 单测 | `tests/apps/devman` 235 项通过，覆盖率 97.88%；`ruff` / `pyrefly` 0 错误 |

（验证前清理了上一轮验证遗留在 HKCU `Path` 的两条 `%TEMP%\devman-verify-*\bin` 死条目。）

### D15 项目级版本绑定（P0，2026-11-14）

- 读取约定：`devman.toml` 的 `[versions]`（`node` / `python`，值为版本号或前缀）优先；
  同目录再回落 `.node-version` / `.python-version`；从当前目录向上逐级查找，
  **每个工具取最近的一条**。项目文件只接受数字开头的具体版本/前缀，
  `latest` / `lts` 通道会被忽略并给出 `warn`（新机复现不可控）。
- 不读用户主目录及更上层：`~/.python-version` 属于 pyenv 的全局语义，
  devman 的全局版本由 `active/<tool>.txt` 表示，混用会互相覆盖。
- 生效范围：`exec` 在子进程内把项目版本目录前置到 PATH，`env` 输出同样顺位的片段，
  `list` / `doctor` 展示绑定来源与是否已安装。**不改全局 active、不写 PATH、不碰注册表**；
  shim 仍只跟随全局 active，因此项目绑定不会影响其他终端或其它项目。
- 缺失行为：绑定版本未安装 → `NOT_INSTALLED`（退出 4），提示
  `fcmd devman install <spec>` 或修改文件；**不自动下载**。
- `use <tool>@<ver> --project` 只写当前目录的单行版本文件，`--project --clear` 删除；
  `devman.toml` 由用户手写、devman 不重写（避免重排注释）；同目录 toml 已绑定该工具时
  给出「单行文件不会生效」的 `warn`。
- 不做「cd 自动切换」：需要 shell hook 改全局 active，会污染全局且多目录互相踩；
  需要自动化的场景用 `devman exec`（可选后续做 shim 读文件，另立决策）。

验收记录 C（root = `%TEMP%\devman-b1\root`，全程 `--no-path`；全局 active 先设为 20.20.2）：

| 项 | 结果 |
|---|---|
| 项目文件生效 | `.node-version` = `22` → `exec node -v` = `v22.23.5`（覆盖全局 20.20.2）；`env` 片段前缀为 `root\node\22.23.5` |
| 删除文件回落 | 删除 `.node-version` 后 `exec node -v` = `v20.20.2` |
| 未装版本 | `.node-version` = `18` → 退出 4，提示 install / 改文件；`doctor` 的 `project` 项为 `warn` |
| `use --project` | 写入 `22`，`active\node.txt` 未变；`--project --clear` 删除文件 |
| 现场恢复 | HKCU `Path` 与验证前逐字一致（未调 `init`）；临时 root / home 已删除 |

### D16 可运维性（P0，2026-11-14）

五个命令分成两半：`size` / `verify` / `export` 只读，`repair` / `restore` 改状态且必须 `--yes`。

- `size`：按「版本目录 / 下载缓存 / shim」三段统计字节数与文件数，另给 `total_bytes`。
  递归时跳过符号链接与 junction（`entry.is_junction()`），避免把用户自己做的目录联接
  重复计入；只读，不触碰状态文件。
- `verify`：逐版本检查安装标记（缺失 / schema、`tool`、`version` 不符）与主可执行文件，
  再检查活动版本是否指向完整安装、shim 是否指向已不存在的版本、`.staging-*` /
  `.trash-*` 与 `cache/*.part` 残留。shim 检查仅在 shim 目录已存在且非空时进行——
  用 `--no-path` 的纯进程内模式本就可能没有 shim，不能据此判失败。有问题时 `ok=false`、
  退出 5，与 `doctor` 同一条契约。
- `repair`：只做可安全自动化的四类动作——重建 shim（`write_shims`）、删残留目录、
  清空指向损坏安装的活动版本、（可选）删半完成版本目录。**默认不删半完成版本**，
  需要 `--purge-incomplete` 显式要求；`--dry-run` 只列计划且不改文件。
  成功与否按「修复后仍存在的问题」判定，而不是「有没有执行动作」，否则修好也会报失败。
- `export`：导出 `devman-lock/1`，每个版本带 `version` / `name`（python 的 uv key）/
  `status` / `source` / `sha256` / `verify`，足以在新机复现来源与校验方式；
  目标文件已存在且无 `--force` → `INVALID_ARGUMENT`（退出 3），不静默覆盖。
- `restore`：读锁文件 → 生成计划（缺的版本 `install`、`active` 不符则 `use`）→ 执行。
  结构、schema、未知工具、条目缺 `version` 都归为 `INVALID_ARGUMENT`（`field=lock`），
  不抛出 traceback。已存在的版本默认跳过（`force=false`），想重装用 `install --force`。
- 共用的代价：`verify` / `size` 都是遍历目录，root 很大时是 O(文件数)；
  `repair` 只能修「devman 自己写的东西」，用户改过的文件不猜、不覆盖。

验收记录 D（root = `%TEMP%\devman-b2\root`，`FCMD_HOME` 同样隔离，全程 `--no-path`，
安装真实 node 22.23.5）：

| 项 | 结果 |
|---|---|
| `size` | node 94.6 MiB、缓存 34.0 MiB、shim 1.1 KiB，合计 128.6 MiB，文件数与人读一致 |
| `verify` 干净 | 「完整性检查通过」，退出 0 |
| `verify` 损坏 | 给 `node.cmd` 追加 `rem damage` + 建 `.staging-junk` → 列出 2 项，退出 5 |
| `repair --dry-run` | 列出 2 项动作，文件未改（`node.cmd` 内容与残留目录都在），退出 5 |
| `repair --yes` | 重建 shim、删残留，退出 0；再次 `verify` 通过 |
| `repair` 无 `--yes` | 退出 2，提示缺确认，零改动 |
| `export` | 写出锁文件，含 `sha256` / `source` / `verify=official-shasums` |
| 卸载后 `restore --dry-run` | 计划「安装 node@22.23.5 + 切换」，不下载，退出 0 |
| `restore --yes --no-path` | 2.8 s 完成（命中本地缓存），`verify` 再次通过 |
| 活动版本卸载 | `uninstall --yes --force` 清空活动标记并删目录，再 `restore` 能装回并切换 |
| 现场恢复 | HKCU `Path` 与验证前逐字一致（`path-before.txt` 除 `reg query` 表头外无差异）；临时 root / home 已删除 |

### D17 网络：代理、断点续传、离线导入（P1，2026-11-14）

三项能力都收在 `_net` 一层，`_node.py` / `_python.py` 的组装方式不变：

- 代理：`config.network.proxy` 三态。`null` 跟随系统（含 `HTTP_PROXY` / `HTTPS_PROXY`，
  即标准库默认行为）；`off` / `none` / `direct` 用空 `ProxyHandler` 显式禁用；其余值按
  URL 使用（缺 scheme 补 `http://`），同时套到 http 与 https。**只作用于 devman 自己的
  `UrllibFetcher`**；python 安装走 uv 子进程，代理由 uv 读环境变量，devman 不改写子进程环境。
  `doctor` 的 `network` 项只读展示 mirror / proxy / 超时；非 http(s) scheme（如 `socks5://`）
  给 `warn` 并提示改用系统代理或本地转发。
- 断点续传：`download` 每次尝试从 `<dest>.part` 的现存字节数发 `Range: bytes=N-`；
  只有响应 `206` 才追加，`200` 一律截断重写；`416`（半成品大于服务端文件）丢弃 `.part`
  后自动从头重来一次。进度回调的 `total` 为「已有字节 + Content-Length」，`done` 从已有
  字节起算。仍保持「先 `.part` 再原子 replace」；传输失败保留 `.part` 供下次续传，
  `doctor` 的 `leftovers` 会列出它。
- 离线导入：`install node@<精确版本> --from <zip>`。不访问网络，`InstallPlan.local` 指向
  本地文件，构件复制进缓存（同样先 `.part` 再落位）后，走与在线安装相同的 sha256 校验、
  解压、自检、标记流水线。校验值来源优先级：`--sha256` → `<zip>.sha256` →
  同目录 `SHASUMS256.txt`；都不可用则 `INVALID_ARGUMENT`（退出 3），**不允许无校验导入**。
  `--dry-run` 打印本地路径与目标目录；`--json` 的 `planned.local` 非空、
  `actions[0].action = import`；标记的 `verify` 为 `explicit-sha256` / `local-sha256` /
  `local-shasums`。`--from` 只支持 node，且要求精确三段版本号（`node@20` 先报参数错误，
  不会联网解析）。
- 代价与边界：镜像只加速构件，校验清单仍取官方（离线时改用本地校验文件或 `--sha256`）；
  续传依赖服务端支持 Range，返回 200 的镜像会整段重下；离线导入的 `source` 记录本地路径，
  跨机复现需重新指定 zip。

验收记录 E（root = `%TEMP%\devman-b3\root`，`FCMD_HOME` 同样隔离，全程 `--no-path`）：

| 项 | 结果 |
|---|---|
| 真实下载 | node 22.23.5，构件 35,630,346 字节，官方 `SHASUMS256.txt` 校验通过 |
| Range 支持 | 对同一 URL 发 `Range: bytes=0-99` → `206` + `Content-Range: bytes 0-99/35630346` |
| 续传成功 | 预置前 1 MiB 的 `.part` → 只补齐剩余字节，sha256 通过；缓存只剩完整 zip |
| 续传反证 | 预置 1 MiB 全零 `.part` → 退出 5 `CHECKSUM_MISMATCH`（证明是追加而非整段覆盖） |
| 离线导入 | 删缓存后用 zip + `<zip>.sha256` 导入，`actions[0].action=import`、`verify=local-sha256` |
| 离线拒收 | 同一 zip 去掉校验文件 → 退出 3，提示 `--sha256` 或校验文件 |
| `--dry-run` | 打印本地来源与目标目录，缓存目录仍为空 |
| 代理 | `proxy=off` → doctor `network`「显式禁用代理」；`socks5://…` → `warn` + 提示 |
| 现场恢复 | 全程 `--no-path`；HKCU `Path` 与验证前逐字一致；临时 root / home 已删除 |

### D18 运行时扩展：Go / Deno / Bun / JDK（P1，2026-11-14）

先把「版本目录 + 标记 + shim」之外的差异收敛成通用 zip 流水线
（`_install.py`：缓存校验 → 下载 → sha256 → 解压到 `.staging-*` → 主程序自检 →
原子落位 → 写标记），再按运行时补「来源 + 校验值」两段。逐项结论：

| 运行时 | 结论 | 来源与校验 | 落地 |
|---|---|---|---|
| Go | 做 | `go.dev/dl/?mode=json`，索引内自带 sha256 | `<root>\go\<ver>`，shim `go` / `gofmt`，`.go-version` |
| Deno | 做 | `dl.deno.land/release-latest.txt` + GitHub Release zip，`.sha256sum`（`Hash : <hex>` 格式） | 单 exe，`.deno-version` |
| Bun | 做 | GitHub API latest + Release zip，`SHASUMS256.txt` | 单 exe，`.bun-version` |
| JDK | 做 | Adoptium v3 `assets/latest/<feature>` 或 `assets/version/<ver>`，`checksum` 字段 | `<root>\jdk\<semver>`，shim `java` / `javac` / `jar`，`.java-version`；**不写 `JAVA_HOME`** |
| Rust | 不做，委托 rustup | rustup 已覆盖 toolchain、组件、target、镜像与自更新，重复实现无收益 | 文档提示用 rustup |
| .NET SDK | 不做，委托官方安装器 | MSI/签名与全局 SDK 解析不适合 devman 的目录模型 | 文档提示 |
| pnpm / yarn | 不做，走 npm / corepack | 它们是 node 生态的包管理器，corepack 已能按项目锁版本 | 文档提示 |

通用化改动：

- `TOOLS` 注册表统一驱动 `list` / `versions` / `size` / `verify` / `export-lock` / `doctor`，
  新增运行时只需补 `_EXECUTABLES`、`SHIM_TARGETS`、`_BIN_SUBDIRS`、`VERSION_FILES` 与
  一个来源模块；`doctor` 的 PATH 冲突检测按主命令名（JDK → `java`）比较。
- 校验标签新增 `official-index`（Go 索引内 sha256）与 `official-api`（Adoptium API 字段），
  与 `official-shasums` / `explicit-sha256` 一起写入安装标记，`--json` 与退出码不变。
- `--from` 离线导入仍只支持 node：其他运行时的包结构与版本语义不同，先不做。

边界与代价：

- Deno / Bun 不支持版本前缀（用精确版本或 `latest`）；`upgrade` 对这两者直接取 latest。
- Bun 的 `latest` 走 GitHub API 匿名配额；Deno 的 latest 走官方文本文件。
- JDK 只安装 JDK 本体，不设置 `JAVA_HOME`；需要者可用 `devman env` 输出的 `bin` 目录或自行设置。
- JDK 版本号保留 Adoptium semver（如 `21.0.5+11`），目录名与 `.java-version` 都使用它。

## 风险与边界

- Node 官方站点在部分网络下不可达：镜像可下构件，但校验清单只认官方；
  不可达时安装失败并提示用 `--sha256` 显式指定，不降级成「镜像说对就算对」。
- `.cmd` shim 对只认 `*.exe` 的构建脚本不透明；这类场景用 `devman exec` / `env`
  把真实版本目录加进 PATH。
- 默认把 shim 目录前置到用户 PATH，会遮蔽已有 node/python；`install` / `init` 默认
  拒绝遮蔽，必须显式 `--allow-shadow`，且输出逐条列出被遮蔽的路径。用户已确认接管后，
  重装/升级不会再次拦截（条目已在 PATH 中）。
- HKCU `Path` 可能被其他安装器并发修改：devman 每次写入前重读当前值、只增删自己的
  条目，不整份覆盖；但两个进程同时写注册表仍可能丢失其中一次修改，建议避免并行安装。
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
- 默认把 `<root>/bin` 写入用户级 PATH：装完新开终端即可用；写入落在 HKCU，
  只增删 devman 自己的条目，`--no-path` 可退回纯进程内注入；
- 不动既有 node / python / conda / venv，切换只是原子写一行文件，
  借用 uv 取 Python，避免重复实现 python-build-standalone 下载与校验；
- 代价是多了一层目录约定与 shim 机制，`.cmd` shim 对少数只认 `.exe` 的工具不透明，
  且默认 PATH 写入需要处理遮蔽与精确回滚——这些都在 `doctor`、`env` 与 ADR 中显式说明。
