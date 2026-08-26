"""双通道路由（FR-A03）：判断问题应走 sql / rag / hybrid / clarify。

- 确定性关键词路由（mock/默认）；
- 路由结果写入 trace，供"路由正确性"测试使用。
"""

from __future__ import annotations

_SQL_KEYWORDS = [
    "成绩", "绩点", "选课", "课程", "学生", "同学", "老师", "教师", "教室",
    "图书", "借阅", "借了", "消费", "奖学金", "人数", "统计", "平均", "最高",
    "最低", "多少", "几个", "数量", "哪个学院", "各学院", "分布", "排名",
]

_RAG_KEYWORDS = [
    "政策", "规定", "流程", "申请", "要求", "怎么", "如何", "条件", "标准",
    "评定", "处分", "考试纪律", "选课规则", "学分", "毕业", "学籍", "请假",
    "休学", "复学", "转专业", "重修", "缓考",
]


def route_question(question: str, history: list[str] | None = None) -> dict:
    """返回 {channel, reason}。"""
    q = question or ""
    sql_hits = [k for k in _SQL_KEYWORDS if k in q]
    rag_hits = [k for k in _RAG_KEYWORDS if k in q]

    # 指代消解：历史里有 SQL 上下文时，模糊追问默认延续 sql 通道
    if not sql_hits and not rag_hits:
        if history:
            return {"channel": "sql", "reason": "指代延续：沿用上一轮数据通道"}
        return {"channel": "clarify", "reason": "问题过于简短/模糊，需要澄清"}

    if sql_hits and rag_hits:
        # 有 RAG 命中但无明确数据意图（统计/数量/平均/名单）→ 倾向政策通道
        strong_sql = any(k in q for k in ["人数", "多少", "统计", "平均", "最高", "最低", "名单", "哪些", "几个", "数量", "分布"])
        if not strong_sql:
            return {"channel": "rag", "reason": f"命中政策关键词：{rag_hits[0]}（无明确数据意图）"}
        return {"channel": "hybrid", "reason": f"同时命中数据({sql_hits[0]})与政策({rag_hits[0]})"}

    if rag_hits:
        return {"channel": "rag", "reason": f"命中政策关键词：{rag_hits[0]}"}

    # 只命中 sql 关键词但过于模糊（无具体对象）
    if len(q) < 8 and not any(c.isdigit() for c in q):
        return {"channel": "clarify", "reason": "问题缺少具体对象，需要澄清"}
    return {"channel": "sql", "reason": f"命中数据关键词：{sql_hits[0] if sql_hits else '默认'}"}
