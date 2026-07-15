# P8 迭代记录：移植 which / sysinfo / portcheck / gittool 工具

## 需求清单

- [x] 参考 `ref/pyflowx` 移植 which 工具（命令查找，单命令）
- [x] 参考 `ref/pyflowx` 移植 sysinfo 工具（系统信息收集，单命令）
- [x] 参考 `ref/pyflowx` 移植 portcheck 工具（端口检查，c/s 子命令）
- [x] 参考 `ref/pyflowx` 移植 gittool 工具（Git 执行工具，多子命令）
- [x] 4 个工具的测试覆盖（含跨平台 mock）

## 迭代目标

继续参考 `ref/pyflowx` 的 CLI 工具实现，在 fcmd 框架下移植 4 个跨平台实用工具，
扩展 fcmd 工具生态。

## 改动文件清单

- `src/fcmd/cli/which.py`（新增）：命令查找工具，单命令，用 `shutil.which` 跨平台
- `src/fcmd/cli/sysinfo.py`（新增）：系统信息收集工具，单命令
- `src/fcmd/cli/portcheck.py`（新增）：端口检查工具，c/s 子命令
- `src/fcmd/cli/gittool.py`（新增）：Git 执行工具，a/i/clean/c/p/pl 子命令
- `tests/test_cli_tools_p8.py`（新增）：4 个工具的 45 个测试

## 关键决策与依据

### 1. which 用 `shutil.which` 替代 subprocess 调用

**问题**：pyflowx 的 which 用 subprocess 调用外部 `where`/`which` 命令。

**决策**：改用标准库 `shutil.which`，无需 subprocess。

**依据**：rule-11 "优先标准库"；`shutil.which` 跨平台、零 subprocess 开销，
且返回值等价于 pyflowx 取 `stdout.split("\n")[0]`。

### 2. gittool 移除 isub 子命令

**问题**：pyflowx 的 gittool `isub` 子命令用 `conditions=(lambda: not_has_git_repo(),)`
控制任务执行条件，fcmd 的 P7c 决定不实现 conditions 模块。

**决策**：移除 isub 子命令，保留 a/i/clean/c/p/pl 六个子命令。

**依据**：rule-01 "不写未被要求的功能"；conditions 模块属于高级编排，
当前需求为 CLI 工具移植，不为单一子命令引入整个 conditions 模块。

### 3. gittool fn 子命令用 `subprocess.run(check=False)`

**问题**：pyflowx 的 `px.sh()` 是统一 subprocess 封装，fcmd 没有等价 API。

**决策**：gittool 的 fn 子命令（a/i）直接用 `subprocess.run(cmd, check=False, text=True)`，
输出透传到终端。提取模块级私有函数 `_run` 复用。

**依据**：rule-11 "subprocess 禁用 shell=True，优先 list[str]"；
fn 类型工具直接用标准库是 fcmd 的既有风格（参考 pymake.py 的 c 子命令用 shutil）。

### 4. sysinfo 的 resource 模块跨平台处理

**问题**：`resource` 模块仅 Unix 可用，Windows 上 `import resource` 抛 ImportError。

**决策**：用 try/except 包裹，Windows 上静默跳过内存信息。测试用 monkeypatch
注入 fake resource 模块覆盖 Linux/Darwin 两条路径。

**依据**：rule-11 "仅类型检查的导入放 if TYPE_CHECKING 块内"；
跨平台代码的 except 分支用测试 mock 激活（rule-11 不留死分支）。

### 5. portcheck 测试用动态端口

**问题**：硬编码端口测试在不同环境可能被占用。

**决策**：用 `socket.bind(("127.0.0.1", 0))` 获取系统分配的空闲端口，
再释放或保持监听来模拟空闲/占用场景。

**依据**：测试稳定性；避免端口冲突。

## 代码实现情况

### which（单命令）

- `find_command(command) -> str | None`：`shutil.which` 查找
- `which_run(commands: list[str])`：批量查找并打印

### sysinfo（单命令）

- `_format_bytes(size) -> str`：字节格式化（B/KB/MB/GB/TB/PB）
- `collect_sysinfo() -> dict[str, str]`：收集 Python/平台/内存/磁盘/CPU 信息
- `print_sysinfo()`：格式化打印
- `sysinfo_run()`：CLI 入口

### portcheck（c/s 子命令）

- `is_port_in_use(port, host) -> bool`：socket bind 检测
- `check_port(port, host)`：检查并打印单个端口
- `scan_ports(start, end, host)`：扫描范围并打印占用端口
- 子命令 c（检查）、s（扫描）

### gittool（a/i/clean/c/p/pl 子命令）

- `_run(cmd)`：subprocess.run 封装
- `not_has_git_repo() -> bool`：检查非 git 目录
- `has_files() -> bool`：检查有未提交更改
- fn 子命令 a（添加并提交）、i（初始化并提交）
- cmd 子命令 clean（hidden，清理）、c（清理并状态，needs clean）、p（推送）、pl（拉取）

## 整合优化情况

- which 用 `shutil.which` 替代 subprocess（更 Pythonic，零外部依赖）
- gittool 移除 isub（依赖不存在的 conditions 模块）
- sysinfo resource 模块用 mock 测试覆盖跨平台路径（不留死分支）

## 测试验证结果

- 571 tests passed（P7: 526 → P8: 571，新增 45）
- coverage 97.44%（P7: 97.33% → P8: 97.44%，提升 0.11%）
- 4 个新工具模块全部 100% 覆盖
- ruff check / format / pyrefly 全通过

## 遗留事项

- 后续可移植更多 pyflowx 工具：autofmt（与 pymake lint/fmt 部分重叠）、
  envdev、imagetool、packtool、pdftool 等
- gittool 的 isub 子命令需 conditions 模块支持（若实现 conditions 后可补回）

## 下一轮计划

P8 完成 4 个工具移植。后续方向由用户决定：
继续移植更多 CLI 工具、实现 conditions 模块、或推进其他基础设施。
