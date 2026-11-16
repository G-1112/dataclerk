"""RAG 专用评测指标（FR-T04）：RAGAS + DeepEval。

官方推荐技术栈点名的两个大模型评测工具，专用于 RAG 质量评估：

- **RAGAS**：faithfulness（忠实度）、answer_relevancy（回答相关性）、
  context_precision（上下文精度）
- **DeepEval**：hallucination（幻觉）、answer_relevancy（回答相关性）

两者均基于 **LLM-as-judge**，需要同时满足：
  1. 已安装依赖：`pip install -e ".[rag-eval]"`（含 ragas / deepeval）
  2. 已配置 `LLM_API_KEY`（.env，任选 DeepSeek / Qwen / GLM）

未安装或未配置时自动跳过（返回 `status="skipped"` 与原因），回退到本项目自带的
**确定性 RAG 指标**（召回@3 / 忠实度），保证 0 Key 可复现、主流程不被阻塞。

说明：RAGAS / DeepEval 的 API 版本迭代较快，若与你安装的版本不一致导致导入或调用
报错，`run_ragas_eval` / `run_deepeval_eval` 会捕获异常并返回可读的失败原因，
不中断跑批。
"""

from __future__ import annotations

from agent import llm


# ---------------------------------------------------------------------------
# 可用性检测
# ---------------------------------------------------------------------------
def ragas_available() -> bool:
    try:
        import ragas  # noqa: F401
        from ragas.metrics import (  # noqa: F401
            answer_relevancy,
            context_precision,
            faithfulness,
        )

        return True
    except Exception:
        return False


def deepeval_available() -> bool:
    try:
        import deepeval  # noqa: F401
        from deepeval.metrics import (  # noqa: F401
            AnswerRelevancyMetric,
            HallucinationMetric,
        )

        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# RAGAS
# ---------------------------------------------------------------------------
def run_ragas_eval(samples: list[dict]) -> dict:
    """对 RAG 样本跑 RAGAS 三项指标。

    samples: [{question, answer, contexts: [str, ...]}, ...]
    返回 {status, reason?, scores?}
    """
    if not ragas_available():
        return {"status": "skipped", "reason": "ragas 未安装（pip install -e '.[rag-eval]'）"}
    if not llm.llm_enabled():
        return {"status": "skipped", "reason": "未配置 LLM_API_KEY（RAGAS 依赖 LLM-as-judge）"}

    try:
        # 现代 RAGAS API（0.2+）：EvaluationDataset.from_list
        from ragas import EvaluationDataset, evaluate
        from ragas.metrics import answer_relevancy, context_precision, faithfulness

        dataset = EvaluationDataset.from_list(
            [
                {
                    "user_input": s["question"],
                    "response": s["answer"],
                    "retrieved_contexts": s["contexts"],
                }
                for s in samples
            ]
        )
        result = evaluate(
            dataset,
            metrics=[faithfulness, answer_relevancy, context_precision],
        )
        scores = _extract_scores(result, ["faithfulness", "answer_relevancy", "context_precision"])
        return {"status": "ok", "scores": scores}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "reason": f"RAGAS 调用失败（API 版本可能不匹配）：{e}"}


# ---------------------------------------------------------------------------
# DeepEval
# ---------------------------------------------------------------------------
def run_deepeval_eval(samples: list[dict]) -> dict:
    """对 RAG 样本跑 DeepEval 幻觉 / 回答相关性。

    返回 {status, reason?, scores?}
    """
    if not deepeval_available():
        return {"status": "skipped", "reason": "deepeval 未安装（pip install -e '.[rag-eval]'）"}
    if not llm.llm_enabled():
        return {"status": "skipped", "reason": "未配置 LLM_API_KEY（DeepEval 依赖 LLM-as-judge）"}

    try:
        from deepeval import evaluate
        from deepeval.metrics import AnswerRelevancyMetric, HallucinationMetric
        from deepeval.test_case import LLMTestCase

        test_cases = [
            LLMTestCase(
                input=s["question"],
                actual_output=s["answer"],
                retrieval_context=s["contexts"],
            )
            for s in samples
        ]
        # 评测器会把分数写回 test_cases[i].metrics，因此不接收返回值
        evaluate(
            test_cases,
            [HallucinationMetric(threshold=0.5), AnswerRelevancyMetric(threshold=0.5)],
            show_indicator=False,
        )
        # 求两项指标的均分（result 为聚合后的对象，分数保存在 test_cases）
        hallu = _mean(_safe_float(tc.metrics[-2].score) for tc in test_cases if len(tc.metrics) >= 2)
        relev = _mean(_safe_float(tc.metrics[-1].score) for tc in test_cases if tc.metrics)
        return {"status": "ok", "scores": {"hallucination": round(hallu, 4), "answer_relevancy": round(relev, 4)}}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "reason": f"DeepEval 调用失败（API 版本可能不匹配）：{e}"}


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
def run_all(samples: list[dict]) -> dict:
    """一次性跑 RAGAS + DeepEval + 自研判官，返回汇总。

    `llm_judge` 是 2026-10-28 新增的**无依赖替代**：RAGAS/DeepEval 因供应链风险
    默认不装，改用同判据的自研 LLM-as-judge，保证「AI 能力应用」维度不留 skipped。
    三者口径一致、互相印证；任一不可用时都只影响自己那一项。
    """
    from . import rag_judge

    return {
        "ragas": run_ragas_eval(samples),
        "deepeval": run_deepeval_eval(samples),
        "llm_judge": rag_judge.run(samples),
        "available": {"ragas": ragas_available(), "deepeval": deepeval_available(), "llm": llm.llm_enabled()},
    }


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _extract_scores(result, names: list[str]) -> dict[str, float]:
    """从 RAGAS 结果对象中提取各指标均值（兼容 to_pandas 或 dict 两种返回）。"""
    scores: dict[str, float] = {}
    try:
        df = result.to_pandas()
        for n in names:
            if n in df.columns:
                scores[n] = round(float(df[n].mean()), 4)
    except Exception:
        for n in names:
            try:
                scores[n] = round(float(result[n]), 4)
            except Exception:
                pass
    return scores


def _safe_float(v) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0


def _mean(vals) -> float:
    vals = list(vals)
    return sum(vals) / len(vals) if vals else 0.0
