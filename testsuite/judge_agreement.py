"""判官可信度验证：把「模型评模型」从"只能信我"变成"可测量"。

问题
----
`rag_judge.py` 用 LLM 打分，评委的合理质疑是：**判官自己准不准？**
没有这一步，"忠实度 0.93"只是个自说自话的数字。

本模块给两个可测维度（都可以在没有人工标注的情况下做）：

1. **稳定性（self-consistency）**：同一批样本重复判 k 次，看判官会不会自我矛盾。
   - `agreement`：所有重复给出**同一二元结论**（忠实 / 不忠实）的样本占比；
   - `mae`：重复之间 faithfulness 的平均绝对差；
   - `kappa`：前两次重复二元标签的 Cohen's κ（扣除偶然一致）。
   不稳定 ⇒ 单次结果不可用于对外宣称任何精度。

2. **判别力（calibration）**：拿一组**已知好坏**的样本（人为构造的"忠实回答"与
   "编造回答"），看判官能不能把它们分开。
   - `accuracy` / `precision` / `recall`：以"忠实"为正类；
   - 同时报 `fabricated` 组的平均分，如果它跟忠实组差不多，说明判官没有判别力。

诚实边界（必须写在报告里）
--------------------------
- 这里的"金标"是**我们自己按判据构造的**，不是第三方人工评分，
  所以它证明的是"判官与设计意图一致"，**不等于**"判官与人类专家评分一致"；
- 稳定性高 ≠ 正确（一个稳定地判错的判官，agreement 也是 1.0）；
  两个指标必须一起看；
- 未配置 Key 时返回 skipped，绝不伪造。
"""

from __future__ import annotations

import statistics

from agent import llm

from . import rag_judge

# 忠实度 >= 该阈值视为"判为忠实"（与 run() 的聚合口径一致，可调）
FAITH_THRESHOLD = 0.8

# ---------------------------------------------------------------------------
# 金标样本：够小、够典型、且每条的"对/错"都有明确判据
# ---------------------------------------------------------------------------
GOLD_CONTEXT = "《本科生奖学金评定办法》规定：国家奖学金按学年综合测评排名评定，" "要求无不及格课程，奖励标准为每人每年 8000 元。"

GOLD = [
    {
        "label": 1,
        "note": "完全依据片段，无编造",
        "question": "国家奖学金的评定条件是什么？",
        "answer": "按学年综合测评排名评定，要求无不及格课程。",
    },
    {
        "label": 1,
        "note": "拒答不幻觉，视为忠实",
        "question": "国家奖学金的申请截止日期是哪天？",
        "answer": "未找到相关政策：检索片段未提及申请截止日期。",
    },
    {
        "label": 0,
        "note": "数字被改（8000 → 5000），典型幻觉",
        "question": "国家奖学金多少钱？",
        "answer": "国家奖学金每人每年 5000 元。",
    },
    {
        "label": 0,
        "note": "片段外知识（比例、名额），无支持",
        "question": "国家奖学金的比例和名额是多少？",
        "answer": "国家奖学金覆盖前 5% 的学生，每年全校 30 个名额。",
    },
    {
        "label": 0,
        "note": "半真半假：前半句有支持，后半句凭空加条件",
        "question": "国家奖学金怎么评？",
        "answer": "按学年综合测评排名评定，且必须获得过校级竞赛奖项。",
    },
    {
        "label": 1,
        "note": "逐项复述片段，含金额，完全可支持",
        "question": "国家奖学金的评选方式和金额？",
        "answer": "按学年综合测评排名评定，要求无不及格课程，每人每年 8000 元。",
    },
]


def cohen_kappa(a: list[int], b: list[int]) -> float | None:
    """Cohen's κ：两评判者（或同一判官两次）在二元标签上的一致性。

    全同类标签时 κ 无定义（po=1 且 pe=1，分母为 0），此时返回 None
    而不是硬编 1.0——否则"两个判官全判忠实"会被误报成完美一致。
    """
    if len(a) != len(b) or not a:
        return None
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa1 = sum(a) / n
    pb1 = sum(b) / n
    pe = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    if pe >= 1.0:
        return None
    return round((po - pe) / (1 - pe), 4)


def _binary(faith: float | None) -> int | None:
    return None if faith is None else (1 if faith >= FAITH_THRESHOLD else 0)


def self_consistency(samples: list[dict], repeats: int = 2, judge=None) -> dict:
    """同一批样本重复判 `repeats` 次，测量判官自洽性。

    judge: 可注入的判定函数（测试用），签名 (question, answer, contexts) -> dict；
    默认走 `rag_judge.judge_one`（真实 LLM）。
    """
    judge = judge or rag_judge.judge_one
    if repeats < 2:
        raise ValueError("repeats 至少为 2，否则无法测量一致性")
    if not samples:
        return {"status": "skipped", "reason": "无样本"}

    verdicts: list[list[int | None]] = []
    faith_matrix: list[list[float]] = []
    for _ in range(repeats):
        row_v, row_f = [], []
        for s in samples:
            r = judge(s["question"], s["answer"], s.get("contexts") or [])
            row_v.append(_binary(r.get("faithfulness")))
            if r.get("faithfulness") is not None:
                row_f.append(r["faithfulness"])
        verdicts.append(row_v)
        faith_matrix.append(row_f)

    n = len(samples)
    decided = 0
    agree = 0
    for i in range(n):
        labels = [verdicts[k][i] for k in range(repeats)]
        if any(x is None for x in labels):
            continue  # 判官没给结论的样本不计入一致率分母（避免用 None 充数）
        decided += 1
        if len(set(labels)) == 1:
            agree += 1

    kappas = []
    for k in range(repeats - 1):
        pairs = [(verdicts[k][i], verdicts[k + 1][i]) for i in range(n)
                 if verdicts[k][i] is not None and verdicts[k + 1][i] is not None]
        if pairs:
            kappas.append(cohen_kappa([p[0] for p in pairs], [p[1] for p in pairs]))

    diffs = []
    for i in range(min(len(f) for f in faith_matrix) if faith_matrix else 0):
        col = [f[i] for f in faith_matrix]
        diffs.append(max(col) - min(col))

    return {
        "status": "ok" if decided else "skipped",
        "repeats": repeats,
        "samples": n,
        "decided": decided,
        "agreement": round(agree / decided, 4) if decided else None,
        "mean_abs_diff": round(statistics.fmean(diffs), 4) if diffs else None,
        "max_abs_diff": round(max(diffs), 4) if diffs else None,
        "kappa": round(statistics.fmean([k for k in kappas if k is not None]), 4)
        if any(k is not None for k in kappas) else None,
        "reason": "" if decided else "全部样本判官未给出结论",
    }


def calibration(gold: list[dict] | None = None, judge=None) -> dict:
    """在已知好坏的样本上测判官的判别力（以"忠实"为正类）。"""
    judge = judge or rag_judge.judge_one
    gold = gold or GOLD
    rows = []
    tp = fp = tn = fn = 0
    faith_by_label: dict[int, list[float]] = {0: [], 1: []}
    for g in gold:
        r = judge(g["question"], g["answer"], [GOLD_CONTEXT])
        pred = _binary(r.get("faithfulness"))
        label = int(g["label"])
        if pred is not None and r.get("faithfulness") is not None:
            faith_by_label[label].append(r["faithfulness"])
        if pred == 1 and label == 1:
            tp += 1
        elif pred == 1 and label == 0:
            fp += 1
        elif pred == 0 and label == 0:
            tn += 1
        elif pred == 0 and label == 1:
            fn += 1
        rows.append({
            "question": g["question"], "label": label, "note": g.get("note", ""),
            "pred": pred, "faithfulness": r.get("faithfulness"),
            "relevancy": r.get("relevancy"),
        })

    total = len(gold)
    correct = tp + tn
    return {
        "status": "ok",
        "threshold": FAITH_THRESHOLD,
        "samples": total,
        "accuracy": round(correct / total, 4) if total else None,
        "precision": round(tp / (tp + fp), 4) if (tp + fp) else None,
        "recall": round(tp / (tp + fn), 4) if (tp + fn) else None,
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "mean_faithful": round(statistics.fmean(faith_by_label[1]), 4) if faith_by_label[1] else None,
        "mean_fabricated": round(statistics.fmean(faith_by_label[0]), 4) if faith_by_label[0] else None,
        "per_sample": rows,
    }


def run(samples: list[dict], repeats: int = 2, judge=None, gold: list[dict] | None = None) -> dict:
    """判官可信度总报告（稳定性 + 判别力）。供 run_suite / llm_track 调用。"""
    if judge is None and not llm.llm_enabled():
        return {"status": "skipped", "reason": "未配置 LLM_API_KEY（判官可信度验证需要真实模型）"}

    consistency = self_consistency(samples, repeats=repeats, judge=judge)
    calib = calibration(gold=gold, judge=judge)
    verdict = _verdict(consistency, calib)
    return {
        "status": "ok",
        "method": "重复判定（稳定性）+ 金标样本（判别力）；金标由本团队按判据构造，非第三方人工评分",
        "consistency": consistency,
        "calibration": calib,
        "conclusion": verdict,
    }


def _verdict(consistency: dict, calib: dict) -> str:
    """把两个指标压成一句可引用的话（指标不达标时也必须如实说）。"""
    agree = consistency.get("agreement")
    acc = calib.get("accuracy")
    if agree is None or acc is None:
        return "指标不完整，不足以对判官可信度下结论"
    if agree >= 0.9 and acc >= 0.8:
        return f"判官可用：重复一致率 {agree:.0%}，金标判别准确率 {acc:.0%}（仍不等价于人工评分）"
    if agree < 0.7:
        return f"判官不稳定（一致率 {agree:.0%}），单次评分不应作为对外精度结论"
    return f"判官稳定性可接受但判别力偏弱（准确率 {acc:.0%}），结论需人工抽检复核"
