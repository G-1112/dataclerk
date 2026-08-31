"""Agent 调用封装：运行智能体、抽取 trace、复算结果集（供断言）。"""

from __future__ import annotations

from typing import Any

from agent.db import connect
from agent.graph import CampusAgent


class AgentClient:
    def __init__(self, agent: CampusAgent) -> None:
        self.agent = agent

    def run(self, question: str, role: str = "admin", session_id: str = "", user_id: str = "") -> dict:
        """运行一次，返回 {answer, channel, trace_id, trace, sql, rows}。"""
        result = self.agent.chat(question, role=role, session_id=session_id, user_id=user_id)
        trace = self.agent.tracer.get(result["trace_id"])
        sql = self._extract_sql(trace)
        rows = self._exec_sql(sql) if sql else []
        return {**result, "trace": trace, "sql": sql, "rows": rows}

    @staticmethod
    def _extract_sql(trace: list[dict]) -> str | None:
        """从 trace 中取最后一步 execute_sql 的 SQL。"""
        for step in reversed(trace):
            if step.get("tool") == "execute_sql":
                return step.get("args", {}).get("sql")
        return None

    @staticmethod
    def _exec_sql(sql: str) -> list[list[Any]]:
        """以 admin 身份复算 SQL，得到原始结果集。"""
        if not sql:
            return []
        conn = connect()
        try:
            cur = conn.execute(sql)
            return [list(r) for r in cur.fetchall()]
        except Exception:
            return []
        finally:
            conn.close()
