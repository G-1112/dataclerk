"""Agent 调用封装：运行智能体、抽取 trace、复算结果集（供断言）。"""

from __future__ import annotations

from typing import Any

from agent.db import connect
from agent.graph import CampusAgent


class AgentClient:
    def __init__(self, agent: CampusAgent) -> None:
        self.agent = agent

    def run(self, question: str, role: str = "admin", session_id: str = "", user_id: str = "") -> dict:
        """运行一次，返回 {answer, channel, trace_id, trace, sql, rows, columns}。"""
        result = self.agent.chat(question, role=role, session_id=session_id, user_id=user_id)
        trace = self.agent.tracer.get(result["trace_id"])
        sql = self._extract_sql(trace)
        rows, columns = self._exec_sql_full(sql) if sql else ([], [])
        return {**result, "trace": trace, "sql": sql, "rows": rows, "columns": columns}

    @staticmethod
    def _extract_sql(trace: list[dict]) -> str | None:
        """从 trace 中取最后一步 execute_sql 的 SQL。"""
        for step in reversed(trace):
            if step.get("tool") == "execute_sql":
                return step.get("args", {}).get("sql")
        return None

    @staticmethod
    def _exec_sql_full(sql: str | None) -> tuple[list[list[Any]], list[str]]:
        """以 admin 身份复算 SQL，返回 (行, 列名)。

        第十四轮：多返回列名，供 `equivalence.compare` 做按列对齐 ——
        没有列名就只能按位置比，"多选了一列"会直接判错。
        """
        if not sql:
            return [], []
        conn = connect()
        try:
            cur = conn.execute(sql)
            cols = [d[0] for d in (cur.description or [])]
            return [list(r) for r in cur.fetchall()], cols
        except Exception:
            return [], []
        finally:
            conn.close()

    @staticmethod
    def _exec_sql(sql: str) -> list[list[Any]]:
        rows, _ = AgentClient._exec_sql_full(sql)
        return rows
