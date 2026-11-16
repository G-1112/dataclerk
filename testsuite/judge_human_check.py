"""判官 vs 人工：导出标注表 + 算一致性（κ）。

为什么要有这一步
----------------
`judge_agreement.py` 证明的是「判官跟设计意图一致」（自造金标 + 重复稳定性），
它**回答不了**评委最想要的那句：「跟人对得上吗？」。
唯一诚实的办法是：抽一批真实回答，让人逐条标「忠实/不忠实」，再算 κ。

流程（两步，中间人工只做一件事：填一列 0/1）
1. `export`：跑 N 条政策问题（检索+合成+判官打分），导出 CSV，`human_faithful` 留空；
2. 人工把每行标 1（回答有片段支撑）或 0（编造/无支撑）；
3. `score`：算 accuracy / precision / recall / F1 / Cohen's κ，并列出分歧样本。

用法
----
python -m testsuite.judge_human_check export --n 20 --out data/report/judge_human_sheet.csv
# 用 Excel 打开 CSV 填 human_faithful 列（1/0），保存
python -m testsuite.judge_human_check score --sheet data/report/judge_human_sheet.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# 补充政策问题池（与 data/policies 的主题对齐）：只用于人工抽检，不进 mock 基线
HUMAN_POOL = [
    "国家励志奖学金的申请条件是什么？",
    "考试违纪会被记入档案吗？",
    "图书借阅可以续借几次？",
    "休学最长可以多久？",
    "转专业需要提交哪些材料？",
    "校园卡补办需要多少钱？",
    "学分绩点是怎么计算的？",
    "课程重修有什么规定？",
    "缓考需要什么手续？",
    "复学申请什么时候提交？",
    "学籍异动包括哪些情形？",
    "请假超过三天需要谁批准？",
    "宿舍调换怎么申请？",
    "毕业设计答辩不通过怎么办？",
]

# 用于测判官"抓编造"的能力：真实回答几乎都忠实，若整张表都是忠实样本，
# κ 只能说明"判官不冤枉人"，说明不了"判官能不能发现问题"。所以按比例注入
# 与片段无关的具体条款（数字/期限都是编的），并把人造标记写进 meta（不进 CSV）。
FABRICATIONS = [
    "另外，该办法规定：连续两学期绩点低于 2.0 的学生将被直接退学，且不得申诉，须在 7 日内办理离校手续。",
    "另外，该办法明确：逾期归还图书每日罚款 5 元，累计上限 200 元，由图书馆直接从校园卡扣除。",
    "另外，该办法补充：转专业学生须缴纳 3000 元专业调整费，且只能在大一第二学期申请一次。",
    "另外，该办法规定：请假超过三天须由校长签字批准，未批准按旷课 20 学时处理。",
    "另外，该办法明确：缓考申请须在考试结束后 3 个工作日内提交，逾期视为放弃，且成绩记 0 分。",
    "另外，该办法补充：宿舍调换每学期只受理一次，且需缴纳 150 元手续费，由宿管中心统一收取。",
]

FIELDS = ["id", "question", "answer", "contexts", "judge_faithfulness",
          "judge_relevancy", "human_faithful", "human_note"]

# 盲标模式：表里不出现判官分数（否则人工会照着判官分填，κ 变成"判官跟自己一致"）。
# 判官分另存 <stem>.judge.csv，score 时按 id 合并回来。
BLIND_FIELDS = ["id", "question", "answer", "contexts", "human_faithful", "human_note"]
JUDGE_FIELDS = ["id", "judge_faithfulness", "judge_relevancy"]

_TRUE = {"1", "y", "yes", "是", "true", "t"}
_FALSE = {"0", "n", "no", "否", "false", "f"}


def build_questions(n: int) -> list[str]:
    """取 RAG 官方问题 + 补充政策问题，凑够 n 条。"""
    from .cases import RAG

    qs = [c["question"] for c in RAG] + HUMAN_POOL
    return qs[:n]


def export_sheet(n: int = 20, out: str | Path = "data/report/judge_human_sheet.csv",
                 fabricate: int = 0, blind: bool = False) -> dict:
    """跑样本并写出待标注 CSV（需要真实 LLM）。"""
    from agent import llm
    from agent.config import RAG_TOP_K
    from agent.tools.retrieve_policy import retrieve_policy
    from testsuite import rag_judge

    if not llm.llm_enabled():
        return {"status": "unavailable", "reason": "未配置 LLM_API_KEY（需 .env.llm），无法生成待标注样本"}

    questions = build_questions(n)
    # 均匀挑选要注入编造的样本（不集中在头部，避免"前几行都是假的"这种暗示）
    inject_at = set()
    if fabricate > 0:
        step = max(1, len(questions) // min(fabricate, len(questions)))
        inject_at = {min(i, len(questions) - 1) for i in range(1, len(questions) + 1, step)}
        inject_at = set(sorted(inject_at)[:fabricate])

    rows: list[dict] = []
    judge_rows: list[dict] = []
    meta: dict[str, str] = {}
    for i, q in enumerate(questions, start=1):
        snippets = retrieve_policy(q, top_k=RAG_TOP_K)
        answer = llm.synthesize_answer(q, snippets)
        if i in inject_at:
            fake = FABRICATIONS[(i - 1) % len(FABRICATIONS)]
            answer = answer.rstrip() + "\n" + fake
            meta[f"H-{i:02d}"] = fake
        j = rag_judge.judge_one(q, answer, snippets)
        rows.append({
            "id": f"H-{i:02d}", "question": q, "answer": answer,
            "contexts": " | ".join(s[:120] for s in snippets[:3]),
            "judge_faithfulness": j.get("faithfulness"),
            "judge_relevancy": j.get("relevancy"),
            "human_faithful": "", "human_note": "",
        })
        judge_rows.append({"id": f"H-{i:02d}", "judge_faithfulness": j.get("faithfulness"),
                           "judge_relevancy": j.get("relevancy")})
        print(f"  [{i}/{n}] {q[:20]} → 判官 {j.get('faithfulness')}", flush=True)

    p = Path(out)
    if not p.is_absolute():
        p = ROOT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    fields = BLIND_FIELDS if blind else FIELDS
    with p.open("w", encoding="utf-8-sig", newline="") as f:  # BOM：Excel 打开不乱码
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    if blind:
        # 判官分另存：人工照着判官分填表，κ 就变成"判官跟自己一致"
        jp = p.with_suffix(".judge.csv")
        with jp.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=JUDGE_FIELDS)
            w.writeheader()
            w.writerows(judge_rows)
        print(f"盲标模式：判官分已移到 {jp.name}，标注表里不含 judge_* 列（避免锚定）")
    if meta:
        meta_path = p.with_suffix(".meta.json")
        meta_path.write_text(json.dumps({"injected": meta}, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f"已记录 {len(meta)} 条人造编造样本的答案位置到 {meta_path.name}（不进 CSV，避免标注时被暗示）")
    print(f"\n已写出 {p}（{len(rows)} 行）；请填 human_faithful 列（1=有片段支撑 / 0=编造）")
    return {"status": "ok", "path": str(p), "rows": len(rows), "fabricated": len(meta),
            "blind": blind}


def _norm_label(v: str) -> int | None:
    s = str(v or "").strip().lower()
    if s in _TRUE:
        return 1
    if s in _FALSE:
        return 0
    return None


def _judge_sidecar(p: Path) -> dict[str, str]:
    """盲标模式下判官分存在 `<stem>.judge.csv`；返回 {id: 分数字符串}。"""
    jp = p.with_suffix(".judge.csv")
    out: dict[str, str] = {}
    if not jp.exists():
        return out
    try:
        with jp.open("r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                out[str(r.get("id", ""))] = r.get("judge_faithfulness", "") or ""
    except OSError:
        return {}
    return out


def _score_of(row: dict, rid: str, side: dict[str, str]) -> float:
    """取判官忠实度分数：表里优先，盲标表回落到 sidecar；取不到返回 nan。"""
    raw = row.get("judge_faithfulness")
    if raw is None or str(raw).strip() == "":
        raw = side.get(rid, "")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float("nan")


def score_sheet(sheet: str | Path, threshold: float = 0.5) -> dict:
    """读已标注 CSV，算判官与人工的一致性与 κ。"""
    from testsuite.judge_agreement import cohen_kappa

    p = Path(sheet)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        return {"status": "error", "reason": f"找不到标注表：{p}"}

    # 人造编造样本的标记在 sidecar 里（不在 CSV，避免标注时被暗示）
    meta_path = p.with_suffix(".meta.json")
    injected: set[str] = set()
    if meta_path.exists():
        try:
            injected = set(json.loads(meta_path.read_text(encoding="utf-8")).get("injected", {}))
        except (ValueError, OSError):
            injected = set()

    human: list[int] = []
    judge: list[int] = []
    disagreements: list[dict] = []
    inj_total = inj_judge_flagged = inj_human_flagged = 0
    unlabeled = 0
    side = _judge_sidecar(p)  # 盲标模式：判官分不在表里
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rid = row.get("id", "")
            h = _norm_label(row.get("human_faithful", ""))
            jf = _score_of(row, rid, side)
            if h is None or jf != jf:  # 未标注 / 判官无结论 → 不进分母
                unlabeled += 1
                continue
            j = 1 if jf >= threshold else 0
            human.append(h)
            judge.append(j)
            if rid in injected:
                inj_total += 1
                inj_judge_flagged += int(j == 0)
                inj_human_flagged += int(h == 0)
            if h != j:
                disagreements.append({
                    "id": rid, "question": row.get("question", "")[:40],
                    "human": h, "judge": j, "judge_faithfulness": jf,
                    "injected": rid in injected,
                    "answer": (row.get("answer") or "")[:120],
                })

    n = len(human)
    if n == 0:
        return {"status": "empty", "reason": "没有任何已标注且判官有结论的样本", "unlabeled": unlabeled,
                "hint": "若只想看「判官能否抓到编造」，无需标注：judge_human_check injected"}

    tp = sum(1 for h, j in zip(human, judge) if h == 1 and j == 1)
    fp = sum(1 for h, j in zip(human, judge) if h == 0 and j == 1)
    fn = sum(1 for h, j in zip(human, judge) if h == 1 and j == 0)
    tn = sum(1 for h, j in zip(human, judge) if h == 0 and j == 0)
    acc = (tp + tn) / n
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else None
    kappa = cohen_kappa(human, judge)

    notes = []
    if n < 10:
        notes.append(f"样本仅 {n} 条，κ 的置信区间很宽，不宜单独下结论")
    if len(set(human)) == 1:
        notes.append("人工标签全同类，κ 无定义（已返回 None，不硬编 1.0）")

    return {
        "status": "ok", "sheet": str(p), "labeled": n, "unlabeled": unlabeled,
        "threshold": threshold,
        "injected": {"total": inj_total, "judge_flagged": inj_judge_flagged,
                     "human_flagged": inj_human_flagged},
        "accuracy": round(acc, 4),
        "precision": None if precision is None else round(precision, 4),
        "recall": None if recall is None else round(recall, 4),
        "f1": None if f1 is None else round(f1, 4),
        "kappa": kappa,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "disagreements": disagreements,
        "notes": notes,
    }


def _resolve(sheet: str | Path) -> Path:
    p = Path(sheet)
    return p if p.is_absolute() else ROOT / p


def injected_report(sheet: str | Path,
                    thresholds: tuple[float, ...] = (0.5, 0.67, 0.75, 0.9)) -> dict:
    """只用人造编造样本算「判官能否抓到编造」——**不需要人工标注**。

    为什么单列一条命令：`score` 必须等人填 `human_faithful` 才有分母，但「抓编造」这件事
    的真值是我们自己注入的（sidecar 里有精确位置），注入那一刻就有答案。把它卡在人工标注后面，
    等于让一个已经能测的指标空转。

    同时必须报代价侧：阈值抬高会多抓到编造，也会把**非注入样本**判到阈值以下。这些样本的
    真值仍然未知（可能就是答得不好），所以字段名用 `others_below_threshold` 而不是"误判"。
    """
    p = _resolve(sheet)
    if not p.exists():
        return {"status": "error", "reason": f"找不到标注表：{p}"}

    meta_path = p.with_suffix(".meta.json")
    injected: set[str] = set()
    if meta_path.exists():
        try:
            injected = set(json.loads(meta_path.read_text(encoding="utf-8")).get("injected", {}))
        except (ValueError, OSError):
            injected = set()
    if not injected:
        return {"status": "no_injected", "sheet": str(p),
                "reason": "该表没有注入人造编造样本；导出时加 --fabricate N 才能测抓编造能力"}

    inj_scores: list[float] = []
    oth_scores: list[float] = []
    side = _judge_sidecar(p)
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rid = row.get("id", "")
            jf = _score_of(row, rid, side)
            if jf != jf:  # 判官无结论 → 不进任何分母
                continue
            (inj_scores if rid in injected else oth_scores).append(jf)

    if not inj_scores:
        return {"status": "empty", "sheet": str(p),
                "reason": "人造样本没有判官分数（判官未运行或全部无结论）"}

    sweep = []
    for t in thresholds:
        flagged = sum(1 for s in inj_scores if s < t)
        sweep.append({
            "threshold": t,
            "injected_flagged": flagged,
            "injected_total": len(inj_scores),
            "detect_rate": round(flagged / len(inj_scores), 4),
            "others_below_threshold": sum(1 for s in oth_scores if s < t),
            "others_total": len(oth_scores),
        })

    full = [r["threshold"] for r in sweep if r["injected_flagged"] == len(inj_scores)]
    best = min(full) if full else None
    notes = []
    if best is None:
        notes.append("所有候选阈值都没能全抓到人造编造：判官对人造编造的分数偏高，需人工复核判据")
    else:
        base = sweep[0]
        if best > base["threshold"]:
            notes.append(f"默认阈值 {base['threshold']} 漏掉 "
                         f"{len(inj_scores) - base['injected_flagged']} 条人造编造；"
                         f"抬到 {best} 才能全抓，代价是非注入样本中被判低于阈值的条数 "
                         f"{base['others_below_threshold']} → "
                         f"{[r['others_below_threshold'] for r in sweep if r['threshold'] == best][0]}")
    notes.append("非注入样本的真值未知，others_below_threshold 不等于误判；要判误伤必须看人工标注")

    return {
        "status": "ok", "sheet": str(p),
        "injected_total": len(inj_scores), "others_total": len(oth_scores),
        "injected_scores": sorted(inj_scores),
        "min_full_detect_threshold": best,
        "sweep": sweep, "notes": notes,
    }


def threshold_sweep(sheet: str | Path, thresholds: tuple[float, ...] = (0.5, 0.67, 0.75, 0.9)) -> list[dict]:
    """阈值敏感性：判官阈值一变，κ 变多少？

    为什么要这个：实测 8 条人造编造样本，判官给的是 0.333 或 0.667——按 0.5 阈值只抓到 5 条，
    按 0.67 阈值能全抓到。**阈值是被默认值藏起来的自由度**，必须显式报出来，
    否则"判官可用"这句话就取决于一个没写进报告的常数。
    """
    out = []
    for t in thresholds:
        rep = score_sheet(sheet, threshold=t)
        if rep.get("status") != "ok":
            out.append({"threshold": t, "status": rep.get("status")})
            continue
        out.append({
            "threshold": t, "accuracy": rep["accuracy"], "precision": rep["precision"],
            "recall": rep["recall"], "kappa": rep["kappa"],
            "injected_flagged": rep["injected"]["judge_flagged"],
            "injected_total": rep["injected"]["total"],
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="判官 vs 人工一致性")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("export", help="生成待标注 CSV")
    e.add_argument("--n", type=int, default=20)
    e.add_argument("--out", default="data/report/judge_human_sheet.csv")
    e.add_argument("--fabricate", type=int, default=0,
                   help="注入 N 条人造编造答案（测判官抓编造的能力；标记写在 sidecar，不进 CSV）")
    e.add_argument("--blind", action="store_true",
                   help="盲标：标注表里不含 judge_* 列，判官分另存 <stem>.judge.csv（避免照着判官分填）")

    s = sub.add_parser("score", help="读取标注结果并计算 κ")
    s.add_argument("--sheet", default="data/report/judge_human_sheet.csv")
    s.add_argument("--out", default="data/report/judge_human_kappa.json")
    s.add_argument("--threshold", type=float, default=0.5, help="判官判为忠实的分数门槛（默认 0.5）")
    s.add_argument("--sweep", action="store_true", help="额外输出阈值敏感性（κ 随门槛变化）")

    i = sub.add_parser("injected", help="只测「判官能不能抓到编造」（无需人工标注）")
    i.add_argument("--sheet", default="data/report/judge_human_sheet.csv")
    i.add_argument("--out", default="data/report/judge_injected.json")

    args = ap.parse_args()

    if args.cmd == "injected":
        rep = injected_report(args.sheet)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        if rep["status"] != "ok":
            print(f"✗ {rep.get('reason', '')}")
            return 1 if rep["status"] == "empty" else 2
        print(f"\n人造编造 {rep['injected_total']} 条，判官分数：{rep['injected_scores']}")
        print("阈值 → 抓到 / 漏 | 非注入样本低于阈值：")
        for r in rep["sweep"]:
            print(f"  {r['threshold']:.2f} → {r['injected_flagged']}/{r['injected_total']} "
                  f"(抓 {r['detect_rate']:.0%}) | {r['others_below_threshold']}/{r['others_total']}")
        for note in rep["notes"]:
            print(f"注意：{note}")
        p = _resolve(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写出：{p}")
        return 0

    if args.cmd == "export":
        from testsuite.llm_track import _load_llm_env  # 只为加载 .env.llm

        _load_llm_env()
        rep = export_sheet(args.n, args.out, fabricate=args.fabricate, blind=args.blind)
        if rep["status"] != "ok":
            print(f"✗ {rep['reason']}")
            return 2
        return 0

    rep = score_sheet(args.sheet, threshold=args.threshold)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if rep["status"] == "ok":
        print(f"\n人工 vs 判官：n={rep['labeled']} 准确率 {rep['accuracy']:.1%}，"
              f"κ={rep['kappa']}，TP/FP/FN/TN={rep['confusion']}")
        for note in rep["notes"]:
            print(f"注意：{note}")
        if rep["disagreements"]:
            print(f"分歧 {len(rep['disagreements'])} 条：")
            for d in rep["disagreements"]:
                print(f"  - {d['id']} 人工={d['human']} 判官={d['judge']}｜{d['question']}")
        if args.sweep:
            rep["threshold_sweep"] = threshold_sweep(args.sheet)
            print("\n阈值敏感性（threshold → accuracy / recall / κ / 抓到的人造样本）：")
            for r in rep["threshold_sweep"]:
                print(f"  {r['threshold']:.2f} → {r.get('accuracy')} / {r.get('recall')} / "
                      f"{r.get('kappa')} / {r.get('injected_flagged')}/{r.get('injected_total')}")
        p = Path(args.out)
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写出：{p}")
        return 0
    print(f"✗ {rep.get('reason', '')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
