"""适配器契约与结果规范化。

契约极简：一个 `run()` 方法，返回含 `answer` 的 dict。其余字段（channel/trace/sql/rows）
**按需提供**——缺什么就自动补默认值，缺得太多只会让对应维度的用例判失败，
而不会让整个跑批崩掉（对第三方智能体宽容，是本工具能"测别人"的前提）。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AgentAdapter(Protocol):
    """被测智能体协议（结构化子类型，无需继承）。"""

    name: str
    #: 是否支持 Text-to-SQL。False 时调用方应跳过功能准确性维度（外部纯对话机器人）。
    supports_sql: bool

    def run(
        self,
        question: str,
        role: str = "admin",
        session_id: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        """执行一次问答，返回 {answer, channel, trace, sql, rows, trace_id}。"""
        ...  # pragma: no cover


def normalize_result(raw: dict[str, Any] | str | None) -> dict[str, Any]:
    """把任意适配器的返回统一成执行器期望的形状。

    - 只给字符串 → 当作 answer；
    - 缺 channel → 按 answer 内容猜（含"拒绝/无权"→deny，含"请补充"→clarify，否则 unknown）；
    - 缺 trace/rows → 空列表（依赖 trace 的维度会自然判失败，而不是抛异常）。
    """
    if raw is None:
        raw = {}
    if isinstance(raw, str):
        raw = {"answer": raw}
    if not isinstance(raw, dict):
        raw = {"answer": str(raw)}

    answer = str(raw.get("answer") or "")
    channel = raw.get("channel") or _guess_channel(answer)
    return {
        "answer": answer,
        "channel": channel,
        "trace": list(raw.get("trace") or []),
        "trace_id": raw.get("trace_id") or "",
        "sql": raw.get("sql"),
        "rows": list(raw.get("rows") or []),
    }


def _guess_channel(answer: str) -> str:
    """从回答文本推断通道——第三方智能体往往不返回 channel 字段。"""
    if any(w in answer for w in ("无权", "越权", "拒绝", "没有权限", "无法访问")):
        return "deny"
    if any(w in answer for w in ("不够明确", "需要澄清", "请补充", "澄清")):
        return "clarify"
    if any(w in answer for w in ("政策", "规定", "办法", "条例")):
        return "rag"
    return "unknown"
