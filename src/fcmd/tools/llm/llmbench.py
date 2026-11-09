"""llmbench - 对任意 OpenAI 兼容服务实测延迟与 token 吞吐。

只依赖 :class:`fcmd.tools.llm.llmbase.Endpoint`，不读取进程状态目录，
因此可直接用于 vLLM、LM Studio 或远程推理服务。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from fcmd.tools.llm.llmbase import Endpoint, Opener, ServeError

__all__ = ["BenchResult", "bench", "make_bench_prompt"]

DEFAULT_PROMPT = "请用一句话解释快速排序的核心思想。"
#: bench 长上下文提示的填充片段（英文约 4 字符 1 token）
BENCH_FILLER = (
    "The quick brown fox jumps over the lazy dog. "
    "Pack my box with five dozen liquor jugs. "
    "How vexingly quick daft zebras jump! "
)


@dataclass(frozen=True, slots=True)
class BenchResult:
    """一次请求的实测指标。"""

    latency_s: float
    prompt_tokens: int
    completion_tokens: int
    tokens_per_s: float


def _post_json(url: str, payload: dict[str, Any], api_key: str, timeout: float, opener: Opener | None) -> Any:
    """POST JSON 并解析响应（网络 / 解析错误统一转 :class:`ServeError`）。"""
    from urllib.request import Request, urlopen

    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    open_fn = opener or (lambda request, seconds: urlopen(request, timeout=seconds))
    try:
        with open_fn(Request(url, data=body, headers=headers, method="POST"), timeout) as resp:
            raw = resp.read()
    except OSError as exc:
        raise ServeError(f"请求失败: {url}（{exc}）") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ServeError(f"响应不是合法 JSON: {url}") from exc


def make_bench_prompt(tokens: int) -> str:
    """生成约 ``tokens`` 个 token 的测试提示，用于模拟 dsh 这类长上下文会话。"""
    if tokens <= 0:
        return DEFAULT_PROMPT
    repeats = max(1, tokens * 4 // len(BENCH_FILLER))
    return BENCH_FILLER * repeats + "\n请用一句话总结上面的内容。"


def bench(  # noqa: PLR0913
    endpoint: Endpoint,
    *,
    prompt: str = DEFAULT_PROMPT,
    prompt_tokens: int | None = None,
    max_tokens: int = 128,
    timeout: float = 180.0,
    opener: Opener | None = None,
) -> BenchResult:
    """对运行中的服务发一次非流式请求，返回延迟与吞吐。

    ``prompt_tokens`` 非空时忽略 ``prompt``，改用按目标 token 数生成的填充提示，
    以免短提示测出的吞吐（200+ tok/s）明显高于真实长会话。
    """
    text = make_bench_prompt(prompt_tokens) if prompt_tokens else prompt
    payload: dict[str, Any] = {
        "model": endpoint.model,
        "messages": [{"role": "user", "content": text}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "stream": False,
    }
    start = time.monotonic()
    data = _post_json(endpoint.base_url + "/v1/chat/completions", payload, endpoint.api_key, timeout, opener)
    elapsed = max(time.monotonic() - start, 1e-6)
    usage = data.get("usage") if isinstance(data, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    if not completion_tokens and isinstance(data, dict):
        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str):
                completion_tokens = max(len(content) // 2, 1)
    # llama-server 的 timings.predicted_per_second 只衡量 decode，不含 prefill；
    # 长上下文下用它才与 dsh 的实测吞吐可比。
    timings = data.get("timings") if isinstance(data, dict) else None
    predicted = timings.get("predicted_per_second") if isinstance(timings, dict) else None
    rate = float(predicted) if isinstance(predicted, int | float) and predicted > 0 else completion_tokens / elapsed
    return BenchResult(elapsed, prompt_tokens, completion_tokens, rate)
