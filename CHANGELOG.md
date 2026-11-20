# Changelog

本文件记录 fcmd 的版本变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 变更

- 应用 `locllm` 更名为 `llm`：包目录改为 `src/fcmd/apps/llm/`，模块 `locllmw.py` 与
  `_locllmw_*` 统一改为 `llmw.py` / `_llmw_*`；命令名统一为 `llmw`（旧命令 `locllmw`
  已移除）；窗口设置 `~/.fcmd/ui/locllmw.json` 在首次访问时
  一次性搬迁为 `ui/llmw.json`（主文件与 `.bak` 一起移动并删除旧文件），模型列表磁盘缓存改名为
  `~/.fcmd/cache/llmw-model-list.json`（缓存可重建，不做迁移）。
- Python 版本要求放宽到 ≥3.10：3.11+ 继续使用 PySide6，3.10 使用 PySide2；新增运行时
  绑定映射与 3.10 标准库兼容垫片（`tomllib` / `StrEnum` / `datetime.UTC` / `itertools.batched` /
  `typing.override` / `Path.is_junction`），CI 与 tox 矩阵加入 3.10。
- GUI 全面迁移到 Qt（PySide6），移除全部 Tk 工具窗口：
  - `filescanw`：扫描 / 结果 / 规则三页改为 Qt，白名单与规则编辑、后台扫描进度与取消保持原有口径；
  - `wslmanw`：发行版表 + 详情 / 下载 / 日志分页，3 秒运行状态轮询、下载源选择与安装本地镜像对话框迁到 Qt；
  - `llmw`：模型页补齐 ModelScope 浏览 / 下载、已安装仓库、转换与量化向导，移除 `--ui tk` 与全部 Tk 模块；
  - `alarmclock` / `checksum`：数字时钟提醒与校验和计算器改为 Qt 窗口；
  - `cmderw`：双面板文件管理器改为 Qt（面板 / 标签 / 传输队列 / 快速查看 / 缩略图 / 命令行 /
    目录树 / 搜索 / 比较同步 / 多重命名 / 用户命令 / 颜色规则 / 打包解压 / 分割合并 / 查重等），
    `app.py` 只保留纯逻辑；
  - 删除 Tk 共享层（`fcmd.gui.window` / `fcmd.gui.sidebar` 与 `theme.py` 的 ttk 渲染），
    新增 `fcmd.gui.pages`，主题注册表保留为纯数据模块；
  - 断言 Tk 内部实现的测试改写为 Qt 等价断言，纯逻辑与设置测试保持原样。
- 新增 `fcmd.gui.geometry` 纯函数模块，各窗口与设置统一复用 `WxH+X+Y` 解析、夹紧与居中算法。
- `llmw` 设置页移除存储 / 下载 / 转换 / 引擎卡片下方的静态小字说明；目录来源与旧配置迁移提示保留，引擎操作反馈按需显示。
- `llmw` 设置页将存储与引擎拆为独立卡片，引擎卡片紧随存储下方；
  引擎路径名称同步为「引擎目录」，与「模型目录」对齐。
- `llmw` 设置页引擎列表压缩为单行行式：标签 + 可执行文件 +
  右侧「默认」或「设为默认」+ CUDA / CPU 类别胶囊，移除来源角标与重复的当前默认按钮；
  列表高度随内容伸缩，超过 6 个引擎才出现滚动条。标签使用常规标签参与布局，
  避免与状态胶囊 / 可执行文件路径重叠。引擎卡片移除状态标签与「体检」入口，
  「刷新」与「引擎」标题同行靠右；「导入目录」「导入压缩包」两个独立按钮放在
  引擎目录行的浏览按钮右侧。
- `llmengine` 新增 `releases` 子命令列出可下载的 llama.cpp 版本（含变体与大小，
  `--assets` 展开资产名），`install --asset` 可按选中资产下载安装；
  `llmw` 设置页引擎卡片新增「下载引擎」对话框：版本 / 变体列表、实时下载进度，
  安装后刷新引擎列表，尚无默认引擎时自动设为默认；版本 / 变体下拉附用途说明
  （CUDA / Vulkan / ROCm / HIP / SYCL / OpenVINO / Metal / OpenCL / CPU），
  已安装引擎的类别胶囊 tooltip 同样带说明，`llmengine releases` 输出变体说明。
- `llmengine` 下载默认优先国内镜像（`gh-proxy.com` / `gh-proxy.net`），失败按顺序回退
  下一镜像与 GitHub 直连；可用 `FCMD_GITHUB_MIRROR` 覆盖（`direct` 表示只用直连）。
  版本列表 / 下载请求都带连接与读取超时，全部失败时一次回报完整地址列表；
  「下载引擎」对话框可随时关闭，不再因后台请求无法退出。
  「下载引擎」后台请求改用守护线程执行，下载卡住时关闭窗口 / 退出应用
  不会被 ``QThreadPool`` 等待，可直接结束进程。
- `llmw` 设置页「导入目录」改为把所选 llama.cpp 目录复制到引擎根目录下，
  不再只登记源路径；「导入压缩包」仍解压到同一引擎目录。存储 / 引擎路径行的
  选择按钮文案由「浏览…」改为「更改」。

### 修复

- 修复 `filescanw` / `pptmake` / `wslmanw` / `envw` 启动时把 `app_name` 按位置传给
  仅关键字参数的 `run_window()` 而抛 `TypeError` 的问题；新增 Qt 窗口入口签名回归测试，
  逐项调用各 `run_qt()` 并以共享入口的真实签名校验。
- `llmw` / `llmserve` 启动兼容性：
  - `--flash-attn` 按引擎 `--help` 探测语法（旧版布尔开关 / 新版 `on|off|auto`），
    按可执行文件路径与修改时间缓存，升级引擎后自动失效，不再因参数解析失败让引擎直接退出；
  - 引擎日志出现 `unknown model architecture: 'xxx'` 时，启动失败提示直接给出升级命令与日志路径，
    而不是只贴一段日志尾部。

### 重构

- 清理迁移后遗留的死代码与常量（cmder / llmw / alarmclock / paramcad / envdev 等）。
- 合并重复实现：Qt 后台事件桥统一为 `EventBridge`、等宽字体统一为 `mono_font()`、
  终端显示宽度填充统一到 `infras.text`、窗口默认尺寸统一走 `geometry.size_from_geometry`、
  压缩包格式探测统一为 `archivex.try_detect_format`、llmw 转换 / 量化共用一次
  `scan_model_env()`；cmder 的大小格式化复用卷容量格式化。

## [0.3.12] - 2026-11-11

### 新增

- `filesync` / `filesyncw`：文件夹比较与同步，支持 compare / sync / mirror / update 四种模式，
  可预览变更、按规则过滤、跳过冲突项；`filesyncw` 为 Tk 窗口版。
- CI 发布流水线接入 fspack 多平台打包：推 `v*.*.*` tag 时产出 Windows / Linux 便携 zip，
  与 PyPI 产物一并发到 GitHub Release；支持 `workflow_dispatch` dry-run 与 `release_tag` 预检。

### 变更

- `cmder`：文件面板刷新改为后台扫描，补齐高 DPI 观感与主题热切换。

### 修复

- 修正 Windows 上回收站、文本预览与排序相关测试的平台假设。
- 修正 Linux 下 pyrefly 对 `ctypes.windll` 的类型推断，发布流水线可在 Linux runner 上通过类型检查。
