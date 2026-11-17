# Changelog

本文件记录 fcmd 的版本变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 变更

- GUI 全面迁移到 Qt（PySide6），移除全部 Tk 工具窗口：
  - `filescanw`：扫描 / 结果 / 规则三页改为 Qt，白名单与规则编辑、后台扫描进度与取消保持原有口径；
  - `wslmanw`：发行版表 + 详情 / 下载 / 日志分页，3 秒运行状态轮询、下载源选择与安装本地镜像对话框迁到 Qt；
  - `locllmw`：模型页补齐 ModelScope 浏览 / 下载、已安装仓库、转换与量化向导，移除 `--ui tk` 与全部 Tk 模块；
  - `alarmclock` / `checksum`：数字时钟提醒与校验和计算器改为 Qt 窗口；
  - `cmderw`：双面板文件管理器改为 Qt（面板 / 标签 / 传输队列 / 快速查看 / 缩略图 / 命令行 /
    目录树 / 搜索 / 比较同步 / 多重命名 / 用户命令 / 颜色规则 / 打包解压 / 分割合并 / 查重等），
    `app.py` 只保留纯逻辑；
  - 删除 Tk 共享层（`fcmd.gui.window` / `fcmd.gui.sidebar` 与 `theme.py` 的 ttk 渲染），
    新增 `fcmd.gui.pages`，主题注册表保留为纯数据模块；
  - 断言 Tk 内部实现的测试改写为 Qt 等价断言，纯逻辑与设置测试保持原样。
- 新增 `fcmd.gui.geometry` 纯函数模块，各窗口与设置统一复用 `WxH+X+Y` 解析、夹紧与居中算法。

### 重构

- 清理迁移后遗留的死代码与常量（cmder / locllmw / alarmclock / paramcad / envdev 等）。
- 合并重复实现：Qt 后台事件桥统一为 `EventBridge`、等宽字体统一为 `mono_font()`、
  终端显示宽度填充统一到 `infras.text`、窗口默认尺寸统一走 `geometry.size_from_geometry`、
  压缩包格式探测统一为 `archivex.try_detect_format`、locllmw 转换 / 量化共用一次
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
