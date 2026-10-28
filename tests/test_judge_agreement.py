"""判官可信度验证的测试（2026-10-28 新增）。

要证明的不是"判官很准"，而是**这套验证手段能如实反映判官的好坏**：
- 稳定判官 → 一致率 1.0、κ 有定义；
- 摇摆判官 → 一致率下降、MAE > 0；
- 完全反向的判官 → 金标准确率 0、忠实组均值低于编造组（即判别力方向被抓住）；
- 全同类标签 → κ 返回 None（不高编 1.0）。
"""

from __future__ import annotations

import pytest

from testsuite import judge_agreement as ja


# ---------------------------------------------------------------------------
# Cohen's κ
# ---------------------------------------------------------------------------
def test_kappa_perfect_agreement():
    assert ja.cohen_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0


def test_kappa_complete_disagreement():
    assert ja.cohen_kappa([1, 0, 1, 0], [0, 1, 0, 1]) == -1.0


def test_kappa_undefined_when_all_same_label():
    """两边都全判忠实 → po=1 且 pe=1，κ 无定义，必须返回 None 而不是 1.0。"""
    assert ja.cohen_kappa([1, 1, 1, 1], [1, 1, 1, 1]) is None


def test_kappa_length_mismatch_and_empty():
    assert ja.cohen_kappa([1, 0], [1]) is None
    assert ja.cohen_kappa([], []) is None


# ---------------------------------------------------------------------------
# 稳定性
# ---------------------------------------------------------------------------
def _judge_factory(scores):
    """构造一个按预设序列返回分数的假判官（每次调用弹一个）。"""
    seq = list(scores)

    def _judge(question, answer, contexts):
        val = seq.pop(0)
        return {"faithfulness": val, "relevancy": 1.0}

    return _judge


def test_self_consistency_stable_judge():
    samples = [{"question": f"q{i}", "answer": "a", "contexts": ["c"]} for i in range(3)]
    # 每次重复都给同样的分数
    rep = ja.self_consistency(samples, repeats=2, judge=_judge_factory([1.0, 0.0, 1.0] * 2))
    assert rep["status"] == "ok"
    assert rep["decided"] == 3
    assert rep["agreement"] == 1.0
    assert rep["mean_abs_diff"] == 0.0
    assert rep["kappa"] == 1.0


def test_self_consistency_flaky_judge():
    samples = [{"question": "q", "answer": "a", "contexts": ["c"]}]
    # 第一次判忠实 1.0，第二次判不忠实 0.2 → 结论翻转
    rep = ja.self_consistency(samples, repeats=2, judge=_judge_factory([1.0, 0.2]))
    assert rep["agreement"] == 0.0
    assert rep["max_abs_diff"] == pytest.approx(0.8)
    # 单样本、结论相反 → po=0、pe=0 → κ=0（与"随机一致"持平），不是 None
    assert rep["kappa"] == 0.0


def test_self_consistency_skips_undecided():
    """判官没给结论（None）的样本不能计入一致率分母。"""
    samples = [{"question": "q1", "answer": "a", "contexts": []},
               {"question": "q2", "answer": "b", "contexts": []}]
    rep = ja.self_consistency(samples, repeats=2, judge=_judge_factory([1.0, None, 1.0, None]))
    assert rep["decided"] == 1 and rep["agreement"] == 1.0


def test_self_consistency_all_undecided_is_skipped():
    samples = [{"question": "q", "answer": "a", "contexts": []}]
    rep = ja.self_consistency(samples, repeats=2, judge=_judge_factory([None, None]))
    assert rep["status"] == "skipped"
    assert rep["agreement"] is None


def test_self_consistency_requires_two_repeats():
    with pytest.raises(ValueError, match="至少为 2"):
        ja.self_consistency([{"question": "q", "answer": "a"}], repeats=1)


def test_self_consistency_empty_samples():
    assert ja.self_consistency([], repeats=2)["status"] == "skipped"


# ---------------------------------------------------------------------------
# 判别力（金标）
# ---------------------------------------------------------------------------
def test_calibration_perfect_judge():
    """完美判官：金标里 label=1 给 1.0，label=0 给 0.0。"""
    mapping = {g["answer"]: (1.0 if g["label"] else 0.0) for g in ja.GOLD}

    def _judge(q, a, c):
        return {"faithfulness": mapping[a], "relevancy": 0.9}

    rep = ja.calibration(judge=_judge)
    assert rep["accuracy"] == 1.0
    assert rep["precision"] == 1.0 and rep["recall"] == 1.0
    assert rep["mean_faithful"] == 1.0
    assert rep["mean_fabricated"] == 0.0
    assert rep["confusion"] == {"tp": 3, "fp": 0, "tn": 3, "fn": 0}


def test_calibration_inverted_judge_is_caught():
    """反向判官（把编造判成忠实）：准确率必须掉到 0。"""
    mapping = {g["answer"]: (0.0 if g["label"] else 1.0) for g in ja.GOLD}

    def _judge(q, a, c):
        return {"faithfulness": mapping[a], "relevancy": 0.5}

    rep = ja.calibration(judge=_judge)
    assert rep["accuracy"] == 0.0
    assert rep["mean_fabricated"] > rep["mean_faithful"]
    assert rep["confusion"]["fp"] > 0


def test_calibration_indifferent_judge_has_no_discrimination():
    """永远给 0.9 的判官：准确率等于金标里"忠实"的占比，且两组均值相同 → 无判别力。"""
    def _judge(q, a, c):
        return {"faithfulness": 0.9, "relevancy": 0.9}

    rep = ja.calibration(judge=_judge)
    assert rep["mean_faithful"] == rep["mean_fabricated"] == 0.9
    assert rep["accuracy"] == pytest.approx(0.5)
    assert rep["recall"] == 1.0 and rep["precision"] == pytest.approx(0.5)


def test_calibration_records_per_sample_notes():
    def _judge(q, a, c):
        return {"faithfulness": 1.0 if g_label(q) else 0.0, "relevancy": 0.8}

    def g_label(q):
        return next((x["label"] for x in ja.GOLD if x["question"] == q), 0)

    rep = ja.calibration(judge=_judge)
    assert len(rep["per_sample"]) == len(ja.GOLD)
    assert all("note" in r and r["pred"] in (0, 1) for r in rep["per_sample"])


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def test_run_skipped_without_llm(monkeypatch):
    monkeypatch.setattr(ja.llm, "llm_enabled", lambda: False)
    rep = ja.run([{"question": "q", "answer": "a", "contexts": []}])
    assert rep["status"] == "skipped"
    assert "LLM_API_KEY" in rep["reason"]


def test_run_with_injected_judge_produces_conclusion():
    def _judge(q, a, c):
        return {"faithfulness": 1.0, "relevancy": 0.9}

    rep = ja.run([{"question": "q", "answer": "a", "contexts": []}], repeats=2, judge=_judge)
    assert rep["status"] == "ok"
    assert rep["consistency"]["agreement"] == 1.0
    assert "判官" in rep["conclusion"]
    assert "不等价于人工评分" in rep["conclusion"] or "复核" in rep["conclusion"]


def test_verdict_flags_instability():
    v = ja._verdict({"agreement": 0.5}, {"accuracy": 0.9})
    assert "不稳定" in v


def test_verdict_reports_missing_metrics():
    assert "不足以" in ja._verdict({"agreement": None}, {"accuracy": 0.9})
