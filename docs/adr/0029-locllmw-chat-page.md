# 0029 locllmw 对话页：整合 llmchatw

- 状态：已落地
- 日期：2026-11-14
- 相关文档：[ADR 0025](0025-sidebar-page-navigation.md)、[ADR 0027](0027-locllmw-rename.md)

## 背景

`llmchatw` 是独立的 Tk 聊天窗口：服务选择、自定义 OpenAI 兼容地址、流式回复、
思考过程展示、图片附件与采样参数都已成熟。`locllmw` 的「对话」页从
[ADR 0027](0027-locllmw-rename.md) 起一直是占位，用户需要在两个窗口之间来回切换：
在 `locllmw` 启动服务，再到 `llmchatw` 选服务对话。两边都读取同一份
`llmenv` 状态，却维护两套窗口设置（`locllmw.json` / `llmchatw.json`）。

## 决策

### 1. 对话能力迁入 `locllmw` 对话页

- 新增 `_locllmw_chat.py` 的 `_LocllmWindowChat` mixin，从原 `ChatWindow`
  迁移连接表单、生成参数、流式对话、图片附件与停止 / 清空逻辑；
- 页面控件由 `SidebarNav` 的 chat 页承载，字符串状态与历史保存在控件内，
  **切页不丢失**；
- 新增 `_on_page_changed("chat")`：每次进入对话页刷新 `llmenv` 服务列表，
  启动服务后无需手动点刷新（刷新按钮仍保留）；
- 关窗时 `_close_chat()` 取消当前生成并等待后台线程收尾，避免遗留请求。

### 2. 命名隔离

对话页的状态与控件全部使用 `_chat_*` 前缀，避免与宿主冲突：

| 原 `ChatWindow` | 迁移后 |
|---|---|
| `_thread` / `_messages` / `_running` | `_chat_thread` / `_chat_messages` / `_chat_running` |
| `_status_var` / `_service_var` / `_model_var` | `_chat_status_var` / `_chat_service_var` / `_chat_model_var` |
| `_send` / `_stop` / `_clear` | `_chat_send` / `_chat_stop` / `_chat_clear` |
| `_chat`（Text） | `_chat_text` |

`locllmw` 自身已有启动 / 停止任务线程与状态栏，重名会导致停止按钮操作错对象，
因此不保留原名。

### 3. 移除独立工具 `llmchatw`

- 删除 `src/fcmd/apps/locllm/llmchatw.py`；
- 删除 `pyproject.toml` 入口、工具注册、`_schema` GUI 工具集合与静态清单条目；
- 设置不再写 `~/.fcmd/ui/llmchatw.json`，窗口几何统一由 `locllmw.json` 管理；
- CLI `llmchat` 保留，作为脚本化对话入口；交互式对话统一进 `locllmw`。

### 4. 布局适配

侧边栏页面高度小于原独立窗口，聊天区 `Text` 高度从 18 行降到 12 行，输入区
仍为 4 行；连接 / 生成参数 / 输入框保持原有分组与 Ctrl+Enter 发送快捷键。

## 验证

- `tests/apps/locllm/test_cli_llmchat.py`：注册表断言 `llmchatw` 已移除；
  服务刷新 / 自定义地址 / 不可用服务提示改为针对 `_LocllmWindowChat` 的纯逻辑测试。
- 真实 Tk 冒烟：`locllmw` 创建窗口后切到「对话」页，服务下拉框填充、发送按钮
  与图片按钮可用、切页后状态保留、关闭窗口不留下线程。
- 全量测试与 `make check-fast` 通过。

## 交接

- 对话历史目前不落盘（与原 `llmchatw` 一致）；如需持久化，应在 `LocllmSettings`
  增加会话 ID 与消息存档，而不是恢复独立窗口。
- 若后续要把对话记录导出 Markdown / JSON，可复用 `ChatSession` 的消息列表，
  只在对话页加导出按钮。
