# fcmd

极速 Python 工具集应用：DAG 任务调度 + 组合 CLI。

## 特性

- **极速冷启动**（< 100ms）：三层懒加载，核心模块零外部依赖
- **DAG 任务调度**：四种执行策略（sequential/thread/async/dependency）
- **组合 API 简洁**：`@fx.task` 装饰器 + 自动依赖推断 + `@fx.tool` CLI 工具
- **纯 CLI（rich 增强）**：彩色输出、表格、进度条
- **便捷脚本模式**：`fcmd pymake b` 一键调用
- **YAML 编排**：GitHub Actions 风格的 `jobs`/`needs`/`matrix`/`if` 任务图
- **零配置工具发现**：`fcmd/cli/` 下的模块自动注册为 CLI 子命令

## 安装

```bash
pip install fcmd          # 或 uv add fcmd
```

可选依赖（按需安装）：

| Extra | 安装命令 | 提供的工具/能力 |
|-------|---------|---------------|
| `img` | `pip install fcmd[img]` | `imagetool`（Pillow） |
| `pdf` | `pip install fcmd[pdf]` | `pdftool`（PyMuPDF + pypdf） |
| `ocr` | `pip install fcmd[ocr]` | `pdftool ocr`（pytesseract，需系统 tesseract） |
| `office` | `pip install fcmd[office]` | img + pdf + ocr 全家桶 |
| `lint` | `pip install fcmd[lint]` | 开发用：ruff + pyrefly |
| `test` | `pip install fcmd[test]` | 开发用：pytest + cov + xdist |
| `dev` | `pip install fcmd[dev]` | lint + test + office + prek + tox |

要求 Python ≥ 3.8。

## 快速上手

### Python API

```python
import fcmd as fx

@fx.task
def extract() -> list[int]: return [1, 2, 3]

@fx.task
def double(extract: list[int]) -> list[int]: return [x * 2 for x in extract]

graph = fx.graph(extract, double)  # double 自动依赖 extract
report = fx.run(graph)
print(report["double"])  # [2, 4, 6]
```

### CLI

```bash
fcmd                   # 列出所有工具
fcmd pymake b          # 构建
fcmd pymake tc         # 类型检查
fcmd info pymake       # 查看 pymake 子命令
fcmd graph pymake tc   # 输出 DAG（Mermaid）
fcmd env               # 查看运行环境
fcmd doctor            # 诊断环境问题
```

## 内建命令

`fcmd` 自带 6 个内建命令（不通过 `@fx.tool` 注册）：

| 命令 | 说明 |
|------|------|
| `fcmd graph <tool> [subcommand]` | 可视化工具子命令的 DAG（Mermaid/layers/describe） |
| `fcmd info [tool] [subcommand]` | 展示工具或子命令的元信息（不执行） |
| `fcmd completion --shell bash\|zsh\|fish` | 生成 shell 补全脚本 |
| `fcmd yaml <file> [job]` | 从 YAML 加载任务图并执行（支持 `--dry-run`/`--strategy`） |
| `fcmd env [--json]` | 展示运行环境（fcmd 版本、Python、平台、工具数、可选依赖） |
| `fcmd doctor` | 诊断环境问题（Python 版本、核心模块、工具模块、可选依赖、PATH 命令） |

shell 补全安装：

```bash
eval "$(fcmd completion --shell bash)"       # bash
eval "$(fcmd completion --shell zsh)"         # zsh
fcmd completion --shell fish | source         # fish
```

## 工具列表

24 个工具模块，按用途分组：

### 项目构建与发布

| 工具 | 别名 | 说明 |
|------|------|------|
| `pymake` | `pm` | 项目构建/测试/检查/发布全流程（b/t/tc/cov/bump/all 等） |
| `autofmt` | - | 代码格式化与检查（封装 ruff format/check） |
| `bumpversion` | - | 版本号自动管理（patch/minor/major + git tag） |
| `packtool` | - | Python 打包（源码/依赖/wheel/嵌入式 Python/zip/清理） |
| `piptool` | - | pip 包管理（安装/卸载/重装/下载/升级/冻结） |

### 文件与目录

| 工具 | 别名 | 说明 |
|------|------|------|
| `writefile` | - | 写入文本内容到文件 |
| `filedate` | - | 文件日期前缀处理（添加/清除） |
| `filelevel` | - | 文件等级标记重命名（PUB/NOR/INT/CON/CLA） |
| `folderback` | - | 文件夹备份（自动清理旧备份） |
| `folderzip` | - | 压缩当前目录下所有子文件夹 |
| `hashfile` | - | 文件哈希计算（md5/sha256/sha1，支持目录批量） |

### 多媒体（可选依赖）

| 工具 | 别名 | 说明 |
|------|------|------|
| `imagetool` | - | 图片处理（resize/crop/rotate/flip/convert/watermark/compress/info/exif/histogram/colors），依赖 `fcmd[img]` |
| `pdftool` | - | PDF 处理（merge/split/compress/encrypt/decrypt/extract/ocr 等 15 子命令），依赖 `fcmd[pdf]`，OCR 需 `fcmd[ocr]` |

### 系统与进程

| 工具 | 别名 | 说明 |
|------|------|------|
| `sysinfo` | - | 系统信息收集（Python/平台/内存/磁盘/CPU） |
| `portcheck` | - | 端口检查与扫描（纯 socket，跨平台） |
| `taskkill` | - | 按名称终止进程（Windows taskkill / Unix pkill） |
| `which` | - | 查找可执行命令路径 |
| `clr` | - | 跨平台清屏 |
| `screenshot` | - | 跨平台截图（Windows PowerShell / macOS screencapture / Linux gnome-screenshot） |
| `reseticoncache` | - | 重置 Windows 图标缓存（仅 Windows） |

### 开发环境

| 工具 | 别名 | 说明 |
|------|------|------|
| `gittool` | - | Git 操作（add+commit/init/init-submodules/clean/push/pull） |
| `envdev` | - | 开发环境镜像源配置（Python/Conda/Rust，Linux 专用操作跳过） |
| `setenv` | - | 设置当前进程环境变量（支持 `--default` 仅在未设置时写入） |
| `sshcopyid` | - | SSH 公钥部署到远程服务器（依赖 sshpass） |

查看任意工具的子命令与字段详情：

```bash
fcmd info <tool>                 # 列出工具所有子命令
fcmd info <tool> <subcommand>    # 展示 ToolSpec 完整字段
```

## 任务定义 API

### `@fx.task` —— Python 函数即任务

函数参数名即依赖名，fcmd 据此自动构建 DAG：

```python
import fcmd as fx

@fx.task
def fetch() -> str:
    return "raw data"

@fx.task
def parse(fetch: str) -> list[str]:
    return fetch.split()

@fx.task
def save(parse: list[str]) -> None:
    # parse 自动依赖 fetch，save 自动依赖 parse
    ...

report = fx.run(fx.graph(fetch, parse, save))
```

### `@fx.tool` —— 函数即 CLI 子命令

```python
import fcmd
from fcmd.models import run_command

@fcmd.tool("gittool", subcommand="a", help="git add + commit")
def add_commit(message: str, amend: bool = False) -> None:
    """添加并提交。"""
    cmd = ["git", "add", "-A"]
    run_command(cmd, check=True)
    cmd = ["git", "commit", "-m", message]
    if amend:
        cmd.append("--amend")
    run_command(cmd, check=True)
```

CLI 参数映射规则：

- 无默认值参数 → 位置参数（`message` → `message`）
- 有默认值参数 → `--name` 选项（`amend: bool = False` → `--amend`）
- `bool = False` → `--name`（store_true）
- `bool = True` → `--no-name`（store_false，保留原参数名）
- `X | None` / `Optional[X]` → 自动解包为 `X`（兼容 Python 3.8 PEP 604 字符串注解）

### `cmd` / `aggregate` 任务

无需 Python 函数，直接用命令或聚合依赖：

```python
@fcmd.tool("mytool", subcommand="build", cmd=["uv", "build"], help="构建")
def _build() -> None: ...  # 函数体可空，cmd 优先执行

@fcmd.tool("mytool", subcommand="all", needs=["build", "test"], strategy="dependency")
def _all() -> None: ...  # aggregate：仅声明依赖
```

## YAML 编排

GitHub Actions 风格的 `jobs`/`needs`/`matrix`/`if`：

```yaml
# deploy.yaml
strategy: thread              # 图级默认策略
defaults:
  retry: {max_attempts: 3}
  timeout: 300
  env: {CI: "true"}

jobs:
  setup:
    cmd: ["git", "clone"]

  build:
    needs: [setup]
    cmd: ["uv", "build"]
    timeout: 120

  deploy:
    needs: [build]
    run: "twine upload dist/*"
    env: {TWINE_TOKEN: "..."}
    continue-on-error: true

  notify:
    needs: [build]
    if: "failure()"           # 仅 build 失败时执行
    cmd: ["python", "-m", "notify"]

  test:
    matrix:                    # 笛卡尔积展开为 4 个任务
      py: ["3.8", "3.9"]
      os: ["linux", "windows"]
    cmd: ["pytest"]
```

执行：

```bash
fcmd yaml deploy.yaml                  # 执行全部 jobs
fcmd yaml deploy.yaml build            # 仅执行 build 及其依赖
fcmd yaml deploy.yaml --dry-run        # 打印执行计划不执行
fcmd yaml deploy.yaml --strategy thread  # 覆盖执行策略
```

或 Python API：

```python
from fcmd.yaml_loader import load_yaml
from fcmd.executors import run

graph = load_yaml("deploy.yaml")
report = run(graph, strategy="dependency")
```

## 执行策略

四种策略通过 `fx.run(graph, strategy=...)` 或 `fcmd yaml --strategy ...` 指定：

| 策略 | 说明 |
|------|------|
| `sequential` | 拓扑序串行执行，简单可预测 |
| `thread` | 同层任务用线程池并行（适合 I/O 密集） |
| `async` | 同层任务用 asyncio 并发（适合异步 I/O） |
| `dependency`（默认） | 仅按依赖关系调度，同层并行，兼顾效率与正确性 |

## 开发

```bash
git clone <repo-url>
cd fcmd
pip install -e fcmd[dev]      # 安装开发依赖（lint + test + office + prek + tox）

fcmd pymake tc                # 类型检查（pyrefly + ruff）
fcmd pymake t                 # 运行测试
fcmd pymake cov               # 测试 + 覆盖率
fcmd pymake all               # 全套流程（清理 + 构建 + 测试 + 类型检查）
```

工具链独立配置文件：`ruff.toml` / `pyrefly.toml` / `pytest.ini` / `.coveragerc` / `.bumpversion.toml` / `uv.toml` / `.pre-commit-config.yaml`。`pyproject.toml` 仅含项目元数据。

新增工具：在 `src/fcmd/cli/` 下新建模块（非 `_` 前缀、非 `main`），用 `@fcmd.tool` 装饰器注册即可，无需修改 `main.py`。可选在模块内定义 `__tool_aliases__: list[str]` 声明别名。

## 许可证

MIT
