"""OpenAI 兼容协议桩：让「测真实开源智能体」这件事在离线环境里可端到端验证。

与 `examples/external_bot/` 的区别
----------------------------------
- `external_bot`：自定义 JSON 协议（`POST /chat`），验证的是 `HttpAgentAdapter`；
- `openai_stub`：**OpenAI 线协议**（`POST /v1/chat/completions`），验证的是
  `OpenAIChatAdapter`——也就是 FastGPT / Dify / vLLM / Ollama / OneAPI 走的那条协议。

它严格实现协议细节（鉴权头、`choices[0].message.content`、`usage`、
400/401 错误体），因此适配器在它身上跑通，等价于适配器在真实服务上"协议层"跑通。
"""

from .brains import GuardedCampusBot, NaiveBrain, get_brain

__all__ = ["GuardedCampusBot", "NaiveBrain", "get_brain"]
