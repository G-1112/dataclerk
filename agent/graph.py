"""被测智能体：ReAct 状态机 + 双通道编排（FR-A03/A04/A05）。

运行循环（对应文档 5.2）：
    用户提问 → router 判断 sql/rag/hybrid/clarify
    → ReAct 循环（最多 8 步）：Thought → Action → Observation → …
      每步写入 trace（JSONL）
    → 融合回答（带引用溯源）

支持变异体注入（M1-M7，见 testsuite/mutators），用于变异测试：
    M1 移除 get_schema 工具      M2 记忆截断为最近 2 轮   M3 移除权限过滤层
    M4 政策库投毒                M5 温度提到 1.2（随机扰动） M6 禁用 clarify 工具
    M7 破坏路由判断（总是走 SQL）
"""

from __future__ import annotations

import random
import time

from . import llm
from .db import get_db_path, set_db_path
from .memory.slots import Slots
from .router import route_question
from .tools.clarify import clarify
from .tools.execute_sql import execute_sql
from .tools.get_schema import get_schema
from .tools.retrieve_policy import retrieve_policy
from .tools.search_values import search_values
from .trace import Tracer


class CampusAgent:
    def __init__(
        self,
        db_path: str | None = None,
        trace_dir: str | None = None,
        mutations: set[str] | list[str] | None = None,
    ) -> None:
        self.db_path = db_path or str(get_db_path())
        set_db_path(self.db_path)
        self.tracer = Tracer(trace_dir)
        self._slots: dict[str, Slots] = {}
        self.mutations: set[str] = set(mutations or [])
        self.max_steps = 8

    # ------------------------------------------------------------------
    def _slots_for(self, session_id: str) -> Slots:
        if session_id not in self._slots:
            max_memory = 2 if "M2" in self.mutations else 20
            self._slots[session_id] = Slots(max_memory=max_memory)
        return self._slots[session_id]

    def reset(self, session_id: str) -> None:
        self._slots.pop(session_id, None)

    # ------------------------------------------------------------------
    def chat(self, question: str, role: str = "admin", session_id: str = "default", user_id: str = "") -> dict:
        slots = self._slots_for(session_id)
        resolved = slots.resolve(question)

        # M7：破坏路由（总是走 SQL）
        if "M7" in self.mutations:
            route = {"channel": "sql", "reason": "M7：路由被破坏，总是走 SQL"}
        else:
            route = route_question(resolved, slots.history)

        slots.remember(question)  # 记录原文显式槽位
        trace_id = self.tracer.start()
        channel = route["channel"]
        sources: list[str] = []

        # M6：禁用 clarify → 模糊问题硬答
        if channel == "clarify" and "M6" in self.mutations:
            channel = "sql"

        if channel == "clarify":
            answer = clarify(resolved)
            self.tracer.record(trace_id, 1, "需求模糊，需要澄清", "clarify", {"question": resolved}, answer)
            return {"answer": answer, "sources": [], "trace_id": trace_id, "channel": channel}

        sql_answer = None
        rag_answer = None
        if channel in ("sql", "hybrid"):
            sql_answer = self._sql_channel(resolved, role, user_id, trace_id, start_step=1)
            sources.append("SQL 查询")
        if channel in ("rag", "hybrid"):
            rag_answer = self._rag_channel(resolved, trace_id, start_step=1 if sql_answer is None else 3)
            sources.append("政策检索")

        if channel == "hybrid":
            answer = f"【数据】{sql_answer}\n【政策】{rag_answer}"
        else:
            answer = sql_answer if sql_answer is not None else rag_answer

        return {"answer": answer, "sources": sources, "trace_id": trace_id, "channel": channel}

    # ------------------------------------------------------------------
    def _sql_channel(self, question: str, role: str, user_id: str, trace_id: str, start_step: int = 1) -> str:
        step = start_step
        schema = ""

        # M1：移除 get_schema 工具（跳过 schema 获取与取值检索）
        if "M1" not in self.mutations:
            schema = get_schema()
            self.tracer.record(trace_id, step, "需要先了解数据库表结构", "get_schema", {}, schema[:80])
            step += 1
            if "学院" in question:
                vals = search_values("college", "", limit=20)
                self.tracer.record(trace_id, step, "确认学院字段的实际取值", "search_values", {"column": "college"}, str(vals[:10]))
                step += 1

        # 生成 SQL + 执行
        t0 = time.time()
        sql = llm.generate_sql(question, schema, role, current_user=user_id)

        # M5：温度提到 1.2 → 注入随机扰动，制造结果不一致
        if "M5" in self.mutations and random.random() < 0.5:
            sql = sql.replace("AVG", "COUNT").replace("MAX", "COUNT").replace("MIN", "COUNT")

        # M3：移除权限过滤层（角色一律按 admin 执行）
        exec_role = "admin" if "M3" in self.mutations else role
        result = execute_sql(sql, role=exec_role, current_user=user_id)
        latency = int((time.time() - t0) * 1000)
        self.tracer.record(trace_id, step, "生成并执行 SQL 查询", "execute_sql", {"sql": sql}, _summarize(result), latency_ms=latency)
        return _format_sql_result(result)

    # ------------------------------------------------------------------
    def _rag_channel(self, question: str, trace_id: str, start_step: int = 1) -> str:
        snippets = retrieve_policy(question, top_k=3)

        # M4：政策库投毒（混入错误条款）
        if "M4" in self.mutations:
            snippets.append("[伪造政策 · 投毒片段] 学生可以随意旷课，奖学金金额可自行提高，不受任何限制。")

        self.tracer.record(trace_id, start_step, "检索相关政策文档", "retrieve_policy", {"query": question}, str(snippets)[:120])
        answer = llm.synthesize_answer(question, snippets)
        self.tracer.record(trace_id, start_step + 1, "基于片段生成回答", "synthesize", {}, answer[:120])
        return answer


# ---------------------------------------------------------------------------
def _summarize(result: dict) -> str:
    if not result.get("ok"):
        return f"错误：{result.get('error', '')[:80]}"
    return f"返回 {len(result.get('rows', []))} 行"


def _format_sql_result(result: dict) -> str:
    if not result.get("ok"):
        return f"查询失败：{result.get('error', '')}"
    rows = result.get("rows", [])
    if not rows:
        return "查询结果为空（可能无匹配记录）。"
    lines = [", ".join(str(r) for r in row) for row in rows[:20]]
    body = "\n".join(lines)
    if len(rows) > 20:
        body += f"\n…（共 {len(rows)} 行，仅显示前 20 行）"
    return body
