"""被测智能体适配器层（通用性补强）。

**为什么需要这一层**
--------------------
改造前，测试执行器（`testsuite/run_suite.py`）直接依赖 `AgentClient`，而 `AgentClient`
硬编码了 `agent.graph.CampusAgent`。结果是：这套测试框架只能测**自己**——评委问
「你能测别人的智能体吗」时，只能回答"改代码就行"，这在「实用性/商业价值」维度是硬伤。

引入适配器后，执行器只依赖一个 **鸭子类型契约**：任何对象只要实现
`run(question, role, session_id, user_id) -> {answer, channel, trace?, sql?, rows?}`
就能被同一套用例驱动。内置两种实现：

- `LocalAgentAdapter`：包本仓库的 `CampusAgent`（默认，行为与改造前完全一致）；
- `HttpAgentAdapter`：任意 HTTP + JSON 的智能体服务（跨语言、跨仓库），
  通过请求/响应字段映射适配，无需改被测方代码；
- `OpenAIChatAdapter`：OpenAI 兼容协议（`POST /v1/chat/completions`），
  一条协议覆盖 vLLM / Ollama / FastGPT / Dify / OneAPI / 百炼等真实智能体服务。

第三方想要接入，只需实现上述一个方法（或起一个 HTTP 端点）。
"""

from __future__ import annotations

from .base import AgentAdapter, normalize_result
from .http_agent import HttpAgentAdapter
from .local import LocalAgentAdapter
from .openai_chat import OpenAIChatAdapter

__all__ = [
    "AgentAdapter",
    "HttpAgentAdapter",
    "LocalAgentAdapter",
    "OpenAIChatAdapter",
    "get_adapter",
    "normalize_result",
]

_ADAPTERS = {
    "local": LocalAgentAdapter,
    "http": HttpAgentAdapter,
    "openai": OpenAIChatAdapter,
}


def get_adapter(kind: str = "local", **kwargs) -> AgentAdapter:
    """按名字取适配器。`local` 走本仓库智能体，`http` 走远端 HTTP 服务。"""
    try:
        cls = _ADAPTERS[kind]
    except KeyError:
        raise ValueError(f"未知适配器：{kind}（可选：{', '.join(_ADAPTERS)}）") from None
    return cls(**kwargs)
