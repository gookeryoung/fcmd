---
description: 仅在开发或修改 fcmd 的 tkinter/ttk 窗口工具（*w.py、alarmclock、checksum 等 GUI）时启用。
globs: "**/*w.py, **/alarmclock.py, **/checksum.py, **/test_cli_alarmclock.py, **/test_cli_checksum.py"
alwaysApply: false
---

# Tk GUI 工具开发规则

适用于 `src/fcmd/tools/**` 与 `src/fcmd/apps/**` 的 tkinter/ttk 窗口工具，基准为 `apps/locllm`。

## 目录与注册

- 独立桌面应用放 `src/fcmd/apps/`，其余窗口与核心逻辑同域存放；禁止新建 `tools/ui`；有 CLI 对应物用 `<tool>w.py` / 命令 `<tool>w`，纯 GUI 用功能名（如 `alarmclock.py`）。
- 必须有模块 docstring、`_DESCRIPTION`、`@fcmd.tool(..., description=_DESCRIPTION)`、`@fcmd.main(...)`，
  并在 `pyproject.toml [project.scripts]` 注册；同步 README 工具表。

## 分层与导入

- 共享窗口骨架、主题与 UI 设置持久化在 `fcmd/gui/`（`window.py` / `theme.py` / `config.py`），窗口只引用它，不复制骨架。
- 窗口类只管界面与事件；业务逻辑放同域核心模块或纯函数，禁止复制 CLI 实现，纯函数必须可单测。
- 禁止顶层导入 `tkinter` 与重依赖；在 `Window.__init__` 惰性导入并保存 `_tk` / `_ttk` / `_messagebox`，可选依赖进 extras。
- `run_gui()` 捕获 `ImportError`，用 `fcmd.console.print_error` 提示后返回。

## 线程

- 控件与变量只在主线程操作；耗时任务用 daemon 线程 + 每次新建 `queue.Queue`，事件为 `(kind, payload)`。
- 主线程用 `root.after(60–200, self._poll_*)` 消费，任务结束即停止续排。
- `_running` / `_closing` 防重入并禁用按钮；后台异常转事件在主线程提示，禁止跨线程操作 Tk。
- 持续监控用 `threading.Event` 停止；`WM_DELETE_WINDOW` 绑定 `_close`，运行中先 `askyesno` 再 `destroy`。

## 界面

- 用 ttk + `grid` 与 `weight` 自适应，禁止 `place()`；设置 geometry、`minsize`、状态栏。
- 日志区只读、带时间戳、自动滚动；GUI 内禁止 `print`。
- 输入先校验，错误在主线程用 `messagebox` 提示；路径用 `Path` + `expanduser()`，文件选择用 `filedialog`。
- 文案统一中文，样式常量集中。

## 配置

- 优先读 `llmenv` / `llmserve` / `llmengine` 既有状态，禁止另造配置源；llm 窗口默认不持久化 UI 设置。
- 确需持久化时放 `${FCMD_HOME:-~/.fcmd}/ui/<tool>.json`，读写独立成函数且读取容错；geometry 校验后再保存。
- 禁止保存明文密钥。

## 测试与门禁

- 窗口类标注 `# pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）`，仅视图代码可豁免。
- 纯逻辑必须有单测；禁止创建真实 `Tk()`，用 `monkeypatch` 覆盖 `ImportError` 降级与 `mainloop`。
- 覆盖率按 `.coveragerc`（≥95%）；L2 跑 `make check-fast` → `make test`；禁止 `--no-verify` 与跳过 prek。

## 其他

- 平台特性需能力检测并降级；外部命令用参数列表，禁止 `shell=True` 或拼接字符串。
