"""自研 LLM-as-judge：RAG 忠实度 / 回答相关性（补齐官方点名的 RAGAS 能力位）。

为什么不用 RAGAS / DeepEval 本体
-------------------------------
两个包在 `pyproject.toml` 里被列为可选依赖并**默认不启用**，原因是已知供应链风险
（ragas 的历史版本存在 SSRF、deepeval 依赖链存在不安全 pickle 反序列化）。
在评审/演示环境装一个带已知 RCE 风险的包，属于"为了分数牺牲安全性"，不值得。

替代方案：**同构自研**。判据与 RAGAS 的 faithfulness / answer_relevancy 对齐——

- faithfulness（忠实度）：把回答拆成「可核查的事实性断言」，逐条判断能否被检索片段
  直接支持；`faithfulness = 被支持的断言数 / 断言总数`。
- answer_relevancy（相关性）：回答是否直接回应问题（0~1）。

差别只在**实现**（单次判官调用 + 结构化 JSON），不在**口径**。这样既避开了依赖风险，
又能给出可复现的数字，而不是在 metrics 里留一个 skipped。

诚实边界
--------
- LLM-as-judge 本身就是"用模型评模型"，存在判官偏好；本项目用 temperature=0 +
  严格 JSON 输出 + 数值钳制降低抖动，但**不宣称它与人工评分等价**；
- 未配置 Key 时返回 skipped，不伪造分数。
"""

from __future__ import annotations

import json
import re

from agent import llm

_JUDGE_SYSTEM = """你是 RAG 质量评审员。给定【用户问题】【检索片段】【系统回答】，请评估两项：

1. 忠实度：把回答拆成可核查的事实性断言，逐条判断能否被检索片段**直接支持**
   （片段外知识、推测、编造均不算支持）。
2. 相关性：回答是否直接回应用户问题（0~1 小数）。

只输出一个 JSON 对象，不要输出解释、不要加代码块标记：
{"claims": <事实性断言条数，整数>, "supported": <其中被片段支持的条数，整数>, "relevancy": <0~1 小数>}

特别规则：若回答明确表示"未找到相关政策/无法依据片段作答"，视为**忠实**（拒答不幻觉），
claims 与 supported 均填 0，relevancy 按是否合理说明困难给 0.5~1。"""


def _parse_json(text: str) -> dict | None:
    """从模型输出里取第一个 JSON 对象（防它带前言/代码块）。"""
    if not text:
        return None
    m = re.search(r"\{.*?\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def judge_one(question: str, answer: str, contexts: list[str]) -> dict:
    """对单条样本调用判官，返回 {faithfulness, relevancy, claims, supported, raw}。"""
    user = (
        f"【用户问题】{question}\n\n"
        f"【检索片段】\n" + "\n".join(f"- {c}" for c in contexts) + "\n\n"
        f"【系统回答】{answer}"
    )
    raw = llm.judge_complete(_JUDGE_SYSTEM, user)
    data = _parse_json(raw)
    if not data:
        return {
            "faithfulness": None, "relevancy": None,
            "claims": 0, "supported": 0, "error": "判官输出无法解析", "raw": (raw or "")[:120],
        }

    claims = max(0, int(data.get("claims") or 0))
    supported = max(0, min(claims, int(data.get("supported") or 0)))
    refused = ("未找到" in answer) or ("无法依据片段" in answer)
    if claims == 0:
        # 无事实性断言：拒答视为忠实，其余情况不计入分母（返回 None 由上层跳过）
        faith = 1.0 if refused else None
    else:
        faith = supported / claims

    try:
        relevancy = _clamp(float(data.get("relevancy")))
    except (TypeError, ValueError):
        relevancy = None

    return {
        "faithfulness": None if faith is None else round(faith, 4),
        "relevancy": relevancy,
        "claims": claims,
        "supported": supported,
        "raw": (raw or "")[:200],
    }


def run(samples: list[dict]) -> dict:
    """对 RAG 样本批量评测。

    samples: [{question, answer, contexts: [str, ...]}, ...]
    返回 {status, method?, scores?, reason?, per_sample?}
    """
    if not llm.llm_enabled():
        return {"status": "skipped", "reason": "未配置 LLM_API_KEY（LLM-as-judge 需要真实模型）"}
    if not samples:
        return {"status": "skipped", "reason": "无 RAG 样本"}

    details = []
    faith_vals: list[float] = []
    rel_vals: list[float] = []
    for s in samples:
        r = judge_one(s["question"], s["answer"], s.get("contexts") or [])
        if r.get("faithfulness") is not None:
            faith_vals.append(r["faithfulness"])
        if r.get("relevancy") is not None:
            rel_vals.append(r["relevancy"])
        details.append({"question": s["question"], **r})

    if not faith_vals and not rel_vals:
        return {"status": "error", "reason": "全部样本判官输出不可解析", "per_sample": details}

    return {
        "status": "ok",
        "method": "自研 LLM-as-judge（判据对齐 RAGAS faithfulness / answer_relevancy）",
        "scores": {
            "faithfulness": round(sum(faith_vals) / len(faith_vals), 4) if faith_vals else None,
            "answer_relevancy": round(sum(rel_vals) / len(rel_vals), 4) if rel_vals else None,
            "samples": len(samples),
        },
        "per_sample": details,
    }
