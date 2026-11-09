"""fcmd.tools.llm —— 大模型相关工具：模型下载、量化、推理服务与性能测试。

领域子包：内含的工具模块经 `@fx.tool` 注册为顶层命令（工具名 = 模块名，
调用方式 `fcmd <工具名>`），本包门面不 re-export 任何工具符号。

可复用子模块（单向依赖；库函数不打印，命令行入口以同名工具注册）::

    llmbase    公共基元与数据类型
    llmenv     路径 / 配置 / 状态文件 / 客户端环境变量     → fcmd llmenv <show|add|remove|reset|list|env>
    llmmodel   模型扫描、定位与显存估算                   → fcmd llmmodel <list|find|vram>
    llmengine  引擎安装、切换、离线包检查与量化器定位     → fcmd llmengine <list|install|use|check|inspect>
    llmenginew llmengine 的 Tk 窗口版                     → fcmd llmenginew
    llmserve   启动计划与进程生命周期                     → fcmd llmserve <plan|start|stop|status|logs>
    llmservew  llmserve 的 Tk 窗口版                     → fcmd llmservew
    llmbench    OpenAI 兼容服务性能测试（只依赖 llmbase.Endpoint） → fcmd llmbench
    llmchat     OpenAI 兼容服务多轮流式对话客户端                  → fcmd llmchat
    llmchatw    llmchat 的 Tk 窗口版                              → fcmd llmchatw
    llmconvert  HF safetensors → GGUF 转换                        → fcmd llmconvert
    llmconvertw llmconvert 的 Tk 窗口版                           → fcmd llmconvertw
    llmdoctor   运行环境体检                                      → fcmd llmdoctor
"""

from __future__ import annotations

__all__: list[str] = []
