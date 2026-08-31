"""自动化断言（FR-T03）。

三类断言：
- result_hash  ：结果集规范化（排序 + 类型统一）→ SHA256 比对，不受行序影响（NFR-05）
- tool_sequence：工具调用序列是否与期望一致
- llm_judge    ：主观维度用 LLM-as-judge（可选）
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def normalize_rows(rows: list[list[Any]]) -> list[list[str]]:
    """结果集规范化：每格转字符串、None→空、整表排序。"""
    def norm(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:.6f}"  # 浮点统一精度，避免 3.0 vs 3.0000001
        return str(v)

    return sorted(tuple(norm(c) for c in row) for row in rows)


def result_hash(rows: list[list[Any]]) -> str:
    """结果集规范化后取 SHA256。"""
    normalized = normalize_rows(rows)
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False).encode("utf-8")).hexdigest()


def assert_result_hash(actual_rows: list[list[Any]], expected_rows: list[list[Any]]) -> bool:
    return result_hash(actual_rows) == result_hash(expected_rows)


def assert_tool_sequence(trace_steps: list[dict], expected: list[str]) -> bool:
    """校验工具调用序列（忽略非工具步骤）。"""
    actual = [s["tool"] for s in trace_steps if s.get("tool")]
    return actual == expected


def llm_judge(question: str, answer: str, criterion: str) -> bool | None:
    """LLM-as-judge（主观维度）。未配置 LLM 时返回 None（跳过）。"""
    from agent import llm

    if not llm.llm_enabled():
        return None
    system = "你是测试评审员，判断智能体回答是否满足标准，只回答 是/否。"
    user = f"标准：{criterion}\n问题：{question}\n回答：{answer}\n是否满足？"
    try:
        out = llm.LlmClient().complete(system, user).strip()
        return out.startswith("是")
    except Exception:
        return None
