"""本仓库智能体适配器：包一层 `AgentClient`，行为与改造前逐字节一致。

保留它的意义不只是"默认实现"——它同时是**适配器契约的参考实现**：
第三方按这个返回结构对齐，就能复用全部维度判定逻辑。
"""

from __future__ import annotations

from typing import Any

from ..agent_client import AgentClient


class LocalAgentAdapter:
    name = "dataclerk-local"
    supports_sql = True

    def __init__(self, agent=None) -> None:
        if agent is None:
            from agent.graph import CampusAgent

            agent = CampusAgent()
        self.agent = agent
        self._client = AgentClient(agent)

    def run(
        self,
        question: str,
        role: str = "admin",
        session_id: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        return self._client.run(question, role=role, session_id=session_id, user_id=user_id)
