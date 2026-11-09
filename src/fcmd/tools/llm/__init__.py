"""fcmd.tools.llm —— 大模型相关工具：模型下载、量化、推理服务与性能测试。

领域子包：内含的工具模块经 `@fx.tool` 注册为顶层命令（工具名 = 模块名，
调用方式 `fcmd <工具名>`），本包门面不 re-export 任何工具符号。

可复用子模块（单向依赖，均不打印、不注册命令）::

    llmbase    公共基元与数据类型
    llmenv     路径 / 配置 / 状态文件 / 客户端环境变量
    llmmodel   模型扫描、定位与显存估算
    llmengine  引擎安装、切换与量化器定位
    llmserve   启动计划与进程生命周期
    llmbench   OpenAI 兼容服务性能测试（只依赖 llmbase.Endpoint）
    llmconvert HF safetensors → GGUF 转换
    llmdoctor  运行环境体检
"""

from __future__ import annotations

__all__: list[str] = []
