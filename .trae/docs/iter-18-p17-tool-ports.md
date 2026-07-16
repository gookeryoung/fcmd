# P17：参考 pyflowx/bitool 完善工具设计

## 需求清单

- [x] 参考 pyflowx 与 bitool 的工具，完善 fcmd 的工具集
- [x] 5 个新工具 + 1 个新子命令实现
- [x] 全套门禁通过（ruff/pyrefly/pytest --cov）
- [x] 总覆盖率不低于 P16（98.96%）

## 迭代目标

参考 pyflowx 和 bitool 的工具设计，将通用、无外部依赖的工具移植到 fcmd，扩展 CLI 工具集。

## 改动文件清单

- `src/fcmd/cli/setenv.py`：新增，环境变量设置工具（单命令，default 模式）
- `src/fcmd/cli/reseticoncache.py`：新增，Windows 图标缓存重置工具（单命令，平台守卫）
- `src/fcmd/cli/sshcopyid.py`：新增，SSH 公钥部署工具（单命令，sshpass 调用）
- `src/fcmd/cli/screenshot.py`：新增，跨平台截图工具（full/area 子命令）
- `src/fcmd/cli/envdev.py`：新增，开发环境镜像源配置工具（9 个子命令）
- `src/fcmd/cli/gittool.py`：修改，新增 `isub` 子命令（初始化子目录 Git 仓库）
- `tests/test_cli_tools_p17.py`：新增，50 个测试用例覆盖全部新工具

## 关键决策与依据

### 工具选型

对比 pyflowx 的 28+ 工具与 bitool 的工具分类，按"无外部依赖 + 通用性 + 标准库优先"原则筛选：

| 工具 | 来源 | 选型理由 |
|------|------|---------|
| setenv | pyflowx | 最简工具，1 函数 10 行，os.environ 操作 |
| reseticoncache | pyflowx | Windows 专用，taskkill/del/start 序列 |
| sshcopyid | pyflowx | SSH 公钥部署，sshpass + ssh |
| screenshot | pyflowx | 跨平台截图，PowerShell/screencapture/gnome-screenshot |
| envdev | pyflowx | 镜像源配置，9 子命令覆盖 pip/conda/rust + Linux 专用 |
| gittool isub | pyflowx | 子目录 Git 仓库批量初始化 |

跳过的工具：imagetool（需 Pillow）、pdftool（需 PyMuPDF/pypdf）、lscalc（过于专用）、dockercmd（仅腾讯云）。

### pyflowx → fcmd 映射

- `px.sh()` → `run_command()`（fcmd.models）
- `Constants.IS_WINDOWS` → `sys.platform == "win32"`
- `ensure_platform()` → 内联 `sys.platform` 检查
- `sys.exit(1)` → `return`（CLI 工具不直接退出）

### 平台守卫与测试策略

所有平台相关代码通过 `sys.platform` 检查守卫，非目标平台打印提示并 `return`。测试通过 `monkeypatch.setattr(sys, "platform", ...)` 模拟各平台，配合 `run_command` mock 避免实际子进程执行。

### envdev 模块级常量 mock

`_RUST_SCCACHE_DIR = Path.home() / ".cargo" / "sccache"` 在导入时求值，测试中 `monkeypatch.setattr(Path, "home", ...)` 无效。需通过 `monkeypatch.setattr("fcmd.cli.envdev._RUST_SCCACHE_DIR", tmp_path / ...)` 直接 mock 模块常量。

### setup_linux_system_mirror 的 try/except/else 模式

pyrefly 要求 `content` 变量在 `try` 块外使用时必须保证已绑定。采用 `try/except/continue/else` 模式，将 `if any(...)` 检查放入 `else` 块，确保 `content` 已赋值。

### gittool isub 使用 subprocess.run 直接调用

`run_command` 不支持 `cwd` 参数，`isub` 需在每个子目录下执行 git 命令，故直接使用 `subprocess.run(cmd, cwd=subdir, check=False, capture_output=True, text=True)`。

## 代码实现情况

### setenv.py（10 行，100% 覆盖）

```python
@fcmd.tool("setenv", help="设置环境变量")
def setenv_run(name: str, value: str, default: bool = False) -> None:
    if default:
        os.environ.setdefault(name, value)
    else:
        os.environ[name] = value
    print(f"环境变量 {name} 已设置")
```

### reseticoncache.py（29 行，95% 覆盖）

Windows 专用：taskkill explorer → 删除 iconcache 文件 → start explorer。2 处 BrPart 为平台守卫分支。

### sshcopyid.py（18 行，100% 覆盖）

读取公钥文件，构造远程脚本（mkdir/chmod/grep/echo），通过 sshpass + ssh 执行。

### screenshot.py（46 行，100% 覆盖）

跨平台截图：Windows 用 PowerShell + System.Drawing，macOS 用 screencapture，Linux 用 gnome-screenshot（失败回退 scrot）。`_take_windows_screenshot` 用 `del area` 因 Windows 下 area/full 脚本相同。

### envdev.py（133 行，99% 覆盖）

9 个子命令：
- `setup-python`/`setup-conda`/`setup-rust`：镜像源配置（环境变量 + 配置文件）
- `download-rustup`/`install-rust`：Rust 工具链安装
- `setup-linux-mirror`/`install-qt-libs`/`install-fonts`/`install-docker`：Linux 专用

2 处 BrPart 为 `setup_linux_system_mirror` 中 `any()` 的 True/False 分支（已补测覆盖，剩余为 try/except 路径组合的边缘情况）。

### gittool.py isub（新增子命令）

遍历当前目录子目录，对每个子目录执行 git init + add + commit。

## 测试验证结果

| 检查项 | 结果 |
|--------|------|
| ruff check | All checks passed |
| ruff format --check | 65 files already formatted |
| pyrefly check | 0 errors |
| pytest | 911 passed（+50） |
| 总覆盖率 | 98.96% → 98.99%（+0.03%） |

各新工具覆盖率：
- setenv.py: 100%
- sshcopyid.py: 100%
- gittool.py: 100%
- screenshot.py: 100%
- reseticoncache.py: 95%（2 BrPart 平台守卫）
- envdev.py: 99%（2 BrPart try/except 路径组合）

## 整合优化情况

- `gittool.py` 的 `import subprocess` 提升至模块级，避免函数内重复导入
- `envdev.py` 镜像源 URL 字典统一以 `_PIP_INDEX_URLS`/`_CONDA_MIRROR_URLS`/`_RUSTUP_MIRRORS` 命名
- 平台守卫统一使用 `sys.platform.startswith("linux")` 或 `sys.platform == "win32"`

## 遗留事项

- reseticoncache.py 的 2 处 BrPart 为平台守卫分支（非 Windows 平台进入函数后立即返回），在 Windows 测试环境下无法覆盖非 Windows 分支
- envdev.py 的 2 处 BrPart 为 try/except/else 中 `any()` 的路径组合边缘情况

## 下一轮计划

- 评估是否还有值得移植的工具（已筛选完毕，剩余均需外部依赖或过于专用）
- 或转向其他功能开发方向
