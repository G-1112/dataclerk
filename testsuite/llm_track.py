"""真实 LLM 测试轨道（P1：补齐「AI 能力应用」维度）。

设计原则
--------
- 本文件**只**显式加载 `.env.llm` 里的 LLM_API_KEY；官方 `run_suite` / `pytest`
  不读取本文件，因此默认保持 mock 模式、不产生 API 费用、不污染 307 套件基线。
- 与官方套件的关系：官方 307 用例在 mock 模式给出「确定性可复现」的满分基线；
  本轨道则在**真实大模型**下复跑一组代表性用例，回答评审最关心的那句话——
  「你号称测一个 AI 智能体，那真实大模型那条链路，你到底测了没有？」

测量口径（与官方套件一致）
--------------------------
- 功能准确性：真实 NL→SQL 由 LLM 生成后执行，结果集 SHA256 与标准答案执行结果比对
  （与 `testsuite/assert_result.result_hash` 同一套规范化逻辑，不受行序影响）。
- RAG 检索质量：确定性 TF-IDF 召回@3（与 mock 一致）+ LLM 合成回答抽样。
- 规划稳定性：temperature=0 下同一问题多次生成 SQL，结果集应一致。

运行
----
    python -m testsuite.llm_track                 # 用默认 data/campus.db
    python -m testsuite.llm_track --db data/campus_small.db
    python -m testsuite.llm_track --no-write      # 仅打印不落盘
    python -m testsuite.llm_track --prompt legacy # A/B：用「修复前」提示词跑基线

A/B 闭环复现（同一 59 条用例，两个提示词各跑一遍）
----------------------------------------------------
    python -m testsuite.llm_track --prompt legacy   # → metrics_llm_legacy.json（约 41–46%）
    python -m testsuite.llm_track                   # → metrics_llm.json（约 90%）

产出
----
    data/report/metrics_llm.json   （真实 LLM 功能准确率 / RAG 召回 / 稳定性一致率）
    data/report/llm_track.md        （可直接贴入作品说明 §3 的实测片段）
    --prompt legacy 时写成 *_legacy.json / *_legacy.md，不覆盖正式产物
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _load_llm_env() -> None:
    """仅加载 `.env.llm`（若存在），且只填补尚未设置的变量，绝不覆盖。"""
    env_file = ROOT / ".env.llm"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


_load_llm_env()

# 必须在 import agent.llm 之前设置好 LLM_API_KEY，否则模块级 LlmClient 会判定未启用。
if not os.getenv("LLM_API_KEY"):
    sys.exit(
        "✗ 未检测到 LLM_API_KEY（应在 .env.llm 中配置）。\n"
        "  真实 LLM 轨道为可选能力，未配置时请勿运行本脚本；"
        "官方 307 套件请在 mock 模式下运行。"
    )

# DB 路径：默认 data/campus.db，可被 --db 覆盖；必须在 import agent.db 前设置。
_db_path = os.getenv("DATACLERK_DB") or str(ROOT / "data" / "campus.db")
os.environ.setdefault("DATACLERK_DB", _db_path)

from agent import db as agent_db  # noqa: E402
from agent import llm as agent_llm  # noqa: E402
from agent.router import route_question  # noqa: E402
from agent.tools.execute_sql import execute_sql  # noqa: E402
from agent.tools.get_schema import get_schema  # noqa: E402
from agent.tools.retrieve_policy import retrieve_policy  # noqa: E402
from testsuite import equivalence  # noqa: E402
from testsuite.assert_result import result_hash  # noqa: E402

agent_db.set_db_path(_db_path)

SCHEMA = get_schema()

# 代表用例：与官方套件同一套 functional_cases()（59 条 = 手写 10 + 模板扩展 49），
# 保证「mock 基线」与「真实 LLM 轨道」口径一致、样本量足够支撑百分比结论。
RAG_IDS = [f"D6-{i:03d}" for i in range(1, 7)]

# 「修复前」的 SQL 提示词（A/B 对照基线，仅供 --prompt legacy 使用）。
# 保留在代码里是为了让「测试→Prompt 修复→回归」这段闭环可被任何人一键复跑，
# 而不是只在文档里写一个无法复现的数字。生产路径始终用 agent.llm._SQL_SYSTEM。
LEGACY_SQL_SYSTEM = """你是校园数据智能体的 SQL 生成器。根据表结构和用户问题生成一条只读 SQLite 查询。
只输出 SQL 语句本身，不要输出解释、不要加代码块标记。
若问题涉及政策、规定、流程等非结构化内容，或无法用 SQL 回答，输出空字符串。"""


def _load_functional() -> dict[str, dict]:
    """加载与官方套件完全一致的 59 条功能用例（含模板扩展）。"""
    from testsuite.cases import functional_cases

    return {c["id"]: c for c in functional_cases()}


def _functional_ids() -> list[str]:
    from testsuite.cases import functional_cases

    return [c["id"] for c in functional_cases()]


def _load_rag() -> dict[str, dict]:
    from testsuite import cases as C

    return {c["id"]: c for c in C.RAG}


def _project(rows: list, cols: list, wanted: list[str]) -> list | None:
    """按期望列名（大小写不敏感）从结果集中投影；找不到任一期望列则返回 None。"""
    idx: list[int] = []
    for wc in wanted:
        found = False
        for i, c in enumerate(cols):
            if str(c).lower() == wc.lower():
                idx.append(i)
                found = True
                break
        if not found:
            return None
    return [[r[i] for i in idx] for r in rows]


def run_functional(cases: dict) -> dict:
    """真实 NL→SQL 功能准确性：LLM 生成 SQL 执行结果 vs 标准答案执行结果。

    给出两个口径：
    - strict（严格）：结果集逐行逐列完全一致（含列数/列顺序）；
    - lenient（宽松）：将 LLM 结果按期望列名投影后比对，容忍「多选冗余列」类差异。
    两种口径的差异本身就揭示了 LLM 的具体失败模式。
    """
    out: dict = {"total": 0, "passed": 0, "passed_lenient": 0, "passed_semantic": 0, "details": []}
    for cid in _functional_ids():
        c = cases.get(cid)
        if not c:
            continue
        q = c["question"]
        role = c.get("role", "admin")
        out["total"] += 1
        try:
            sql_llm = agent_llm.generate_sql(q, SCHEMA, role, "")
            res_llm = execute_sql(sql_llm, role=role, current_user="")
        except Exception as e:  # noqa: BLE001
            out["details"].append(
                {"id": cid, "question": q, "passed": False, "passed_lenient": False,
                 "error": f"{type(e).__name__}: {e}", "sql_llm": None}
            )
            continue
        res_exp = execute_sql(c["expected_sql"], role=role, current_user="")
        strict = (
            bool(res_llm.get("ok")) and bool(res_exp.get("ok"))
            and result_hash(res_llm["rows"]) == result_hash(res_exp["rows"])
        )
        # 宽松口径：按期望列名投影后比对（容忍冗余列 / 列顺序）
        lenient = strict
        if not strict and res_llm.get("ok") and res_exp.get("ok"):
            wanted = [str(x).lower() for x in res_exp["columns"]]
            proj_llm = _project(res_llm["rows"], res_llm["columns"], wanted)
            proj_exp = _project(res_exp["rows"], res_exp["columns"], wanted)
            lenient = proj_llm is not None and proj_exp is not None and result_hash(proj_llm) == result_hash(proj_exp)

        # 语义等价口径（第十四轮）：数值容差 + 按列名对齐（同义列名）+ 允许**多选列**。
        # 不允许少列：丢掉被问到的列会让行含义变得不可判读（如按学院统计却丢了学院名），
        # 那种情况我们宁可判错也不放宽。
        semantic, equiv_level = strict, "exact"
        if not semantic and res_llm.get("ok") and res_exp.get("ok"):
            e = equivalence.compare(
                res_exp["rows"], res_llm["rows"],
                res_exp["columns"], res_llm["columns"],
                spec={"allow_extra_columns": True},
            )
            semantic, equiv_level = e["equivalent"], e["level"]
        if strict:
            out["passed"] += 1
        if lenient:
            out["passed_lenient"] += 1
        if semantic:
            out["passed_semantic"] += 1
        out["details"].append({
            "id": cid, "question": q, "passed": bool(strict), "passed_lenient": bool(lenient),
            "passed_semantic": bool(semantic),
            "equiv_level": equiv_level,
            "sql_llm": (sql_llm or "")[:300],
            "exec_ok": bool(res_llm.get("ok")),
            "exp_ok": bool(res_exp.get("ok")),
            "error": res_llm.get("error", "") if not res_llm.get("ok") else "",
        })
    out["accuracy"] = (out["passed"] / out["total"]) if out["total"] else 0.0
    out["accuracy_lenient"] = (out["passed_lenient"] / out["total"]) if out["total"] else 0.0
    out["accuracy_semantic"] = (out["passed_semantic"] / out["total"]) if out["total"] else 0.0
    return out


def run_rag(cases: dict) -> dict:
    """RAG：确定性召回@3（与 mock 一致）+ LLM 合成回答抽样。"""
    out: dict = {"total": 0, "recall3": 0, "details": []}
    for cid in RAG_IDS:
        c = cases.get(cid)
        if not c:
            continue
        q = c["question"]
        kws = c.get("doc_keywords", [])
        snippets = retrieve_policy(q, top_k=3)
        titles_blob = " ".join(snippets)
        hit = any(k in titles_blob for k in kws)
        answer = agent_llm.synthesize_answer(q, snippets)
        out["total"] += 1
        if hit:
            out["recall3"] += 1
        out["details"].append({
            "id": cid, "question": q, "expected_keywords": kws,
            "recall_hit": bool(hit),
            "answer_llm": (answer or "")[:300],
            # 保留完整片段与完整回答，供 LLM-as-judge 评忠实度/相关性用
            # （截断后的文本会让"片段是否支持断言"判不准）
            "contexts": snippets,
            "answer_full": answer or "",
        })
    out["recall3_rate"] = (out["recall3"] / out["total"]) if out["total"] else 0.0
    return out


def run_stability() -> dict:
    """规划稳定性：temperature=0 下同一问题多次生成 SQL，结果集应一致。"""
    qs = [
        "计算机学院有多少学生？",
        "软件学院的平均绩点是多少？",
        "各学院的学生人数分布是怎样的？",
    ]
    runs = 3
    out: dict = {"questions": len(qs), "runs": runs, "consistent": 0, "details": []}
    for q in qs:
        distinct: set[str] = set()
        for _ in range(runs):
            sql = agent_llm.generate_sql(q, SCHEMA, "admin", "")
            res = execute_sql(sql, role="admin", current_user="")
            distinct.add(result_hash(res["rows"]) if res.get("ok") else f"ERR:{res.get('error')}")
        consistent = len(distinct) == 1
        if consistent:
            out["consistent"] += 1
        out["details"].append({"question": q, "distinct_results": len(distinct), "consistent": bool(consistent)})
    out["consistency_rate"] = out["consistent"] / len(qs)
    return out


def _md(s: dict) -> str:
    lines = [
        "# 真实 LLM 测试轨道 · 本机实测片段",
        "",
        f"- 模型：`{s['llm_model']}`｜数据库：`{Path(s['db']).name}`",
        f"- 功能准确性（严格·结果集完全一致）：**{s['functional']['passed']}/{s['functional']['total']} = {s['functional']['accuracy']:.1%}**",
        f"- 功能准确性（宽松·按期望列投影）：**{s['functional']['passed_lenient']}/{s['functional']['total']} = {s['functional']['accuracy_lenient']:.1%}**",
        f"- 功能准确性（语义等价·数值容差+列对齐+允许多选列）：**{s['functional'].get('passed_semantic')}/{s['functional']['total']} = {s['functional'].get('accuracy_semantic', 0):.1%}**",
        f"- RAG 召回@3（确定性 TF-IDF 检索 + LLM 合成回答）：**{s['rag']['recall3']}/{s['rag']['total']} = {s['rag']['recall3_rate']:.1%}**",
        f"- 规划稳定性（temp=0 多次生成一致率）：**{s['stability']['consistent']}/{s['stability']['questions']} = {s['stability']['consistency_rate']:.1%}**",
        _md_judge_line(s.get("rag_judge") or {}),
        _md_agreement_line(s.get("judge_agreement") or {}),
        "",
        "## 功能用例（真实 LLM 生成 SQL 抽样）",
        "",
    ]
    for d in s["functional_details"]:
        mark = "✅" if d["passed"] else "❌"
        lines.append(f"- {mark} `{d['id']}` {d['question']}")
        if d.get("sql_llm"):
            lines.append(f"  - LLM SQL：`{d['sql_llm']}`")
        if d.get("error"):
            lines.append(f"  - 错误：`{d['error']}`")
    lines += ["", "## RAG 用例（LLM 合成回答抽样）", ""]
    for d in s["rag_details"]:
        mark = "✅" if d["recall_hit"] else "⚠️"
        lines.append(f"- {mark} `{d['id']}` {d['question']}（期望关键词 {d['expected_keywords']}）")
        if d.get("answer_llm"):
            lines.append(f"  - LLM 回答：`{d['answer_llm']}`")
    lines += _md_judge_section(s.get("rag_judge") or {})
    return "\n".join(lines) + "\n"


def _md_judge_line(j: dict) -> str:
    if j.get("status") != "ok":
        return f"- RAG 忠实度 / 相关性（自研 LLM-as-judge）：{j.get('status', '未运行')}"
    sc = j.get("scores", {})
    return (
        f"- RAG 忠实度（自研 LLM-as-judge，判据对齐 RAGAS）：**{sc.get('faithfulness')}**｜"
        f"回答相关性：**{sc.get('answer_relevancy')}**（{sc.get('samples')} 条样本）"
    )


def _md_agreement_line(a: dict) -> str:
    """判官可信度一行（评委最容易追问的一项）。"""
    if a.get("status") != "ok":
        return f"- 判官可信度验证：{a.get('status', '未运行')}（{a.get('reason', '')}）"
    c, cal = a.get("consistency", {}), a.get("calibration", {})
    return (
        f"- 判官可信度（重复判定 + 金标判别）：一致率 **{c.get('agreement')}**"
        f"（κ={c.get('kappa')}，MAE={c.get('mean_abs_diff')}）｜"
        f"金标准确率 **{cal.get('accuracy')}**"
        f"（忠实组均值 {cal.get('mean_faithful')} vs 编造组 {cal.get('mean_fabricated')}）"
    )


def _md_judge_section(j: dict) -> list[str]:
    if j.get("status") != "ok":
        return []
    out = ["", "## RAG 忠实度逐条判分（LLM-as-judge）", ""]
    for d in j.get("per_sample", []):
        out.append(
            f"- `{d['question'][:24]}` → 断言 {d.get('claims')} 条 / 支持 {d.get('supported')} 条，"
            f"忠实度 {d.get('faithfulness')}，相关性 {d.get('relevancy')}"
        )
    return out


def _md_rounds(rep: dict) -> str:
    lines = [
        "# 真实 LLM 多轮稳定性（同一批功能用例重复跑）",
        "",
        f"- 模型：`{rep['llm_model']}`｜数据库：`{rep['db']}`｜提示词：`{rep['prompt_variant']}`",
        f"- 轮数：**{len(rep['rounds'])}**｜每轮严格通过数：{rep['passed_list']}",
        f"- 准确率：均值 **{rep['mean']:.1%}**，区间 {rep['min']:.1%}–{rep['max']:.1%}，"
        f"标准差 {rep['std']:.4f}｜各轮完全一致：**{rep['all_identical']}**",
        "",
        "| 轮次 | 严格 | 语义 | 失败用例 |",
        "|---|---|---|---|",
    ]
    for r in rep["rounds"]:
        lines.append(
            f"| {r['round']} | {r['passed']}/{r['total']} = {r['accuracy']:.1%} "
            f"| {r['passed_semantic']}/{r['total']} | {', '.join(r['failed_ids']) or '—'} |"
        )
    flaky = rep.get("case_flakiness") or {}
    lines += ["", f"- 跨轮不稳定用例（同一题有时对有时错）：{len(flaky)} 条"
                  + (f" → {', '.join(f'{k}(错 {v} 轮)' for k, v in flaky.items())}" if flaky else "（无）")]
    return "\n".join(lines) + "\n"


def run_rounds(args) -> None:
    """多轮稳定性：同一批功能用例跑 N 轮，报每轮准确率、标准差与跨轮不稳定题目。

    只跑功能维度是有意的取舍：RAG/判官/规划稳定性每轮都要额外几十次调用，
    而「真实模型在多轮之间是否稳定给出正确 SQL」这个问题只由功能维度回答。
    产物单独写 `llm_rounds.json`，不覆盖主基线 `metrics_llm.json`。
    """
    import statistics

    rounds: list[dict] = []
    for i in range(args.rounds):
        r = run_functional(_load_functional())
        failed = [d["id"] for d in r["details"] if not d["passed"]]
        item = {
            "round": i + 1, "passed": r["passed"], "total": r["total"],
            "accuracy": round(r["accuracy"], 4),
            "passed_semantic": r["passed_semantic"], "failed_ids": failed,
        }
        rounds.append(item)
        print(
            f"第 {i + 1}/{args.rounds} 轮：{r['passed']}/{r['total']} = {r['accuracy']:.1%}"
            f"（语义 {r['passed_semantic']}/{r['total']}）"
            + (f"｜失败 {len(failed)} 条" if failed else ""),
            flush=True,
        )

    accs = [x["accuracy"] for x in rounds]
    flakiness: dict[str, int] = {}
    for x in rounds:
        for cid in x["failed_ids"]:
            flakiness[cid] = flakiness.get(cid, 0) + 1
    flaky = {k: v for k, v in flakiness.items() if v < len(rounds)}
    rep = {
        "llm_model": os.getenv("LLM_MODEL"), "db": args.db,
        "prompt_variant": args.prompt, "rounds": rounds,
        "passed_list": [x["passed"] for x in rounds],
        "accuracy_list": accs,
        "min": round(min(accs), 4), "max": round(max(accs), 4),
        "mean": round(statistics.fmean(accs), 4),
        "std": round(statistics.pstdev(accs), 4),
        "all_identical": len({x["passed"] for x in rounds}) == 1,
        "failed_ids_union": sorted(flakiness),
        "case_flakiness": flaky,
    }
    print(
        f"\n多轮稳定性：均值 {rep['mean']:.1%}，区间 {rep['min']:.1%}–{rep['max']:.1%}，"
        f"标准差 {rep['std']:.4f}，各轮一致 {rep['all_identical']}"
    )
    if flaky:
        print(f"跨轮不稳定题目：{flaky}")
    if not args.no_write:
        # 输出目录可由 args.out_dir 覆盖（测试用），默认仍是 data/report
        rep_dir = Path(getattr(args, "out_dir", ROOT / "data" / "report"))
        rep_dir.mkdir(parents=True, exist_ok=True)
        (rep_dir / "llm_rounds.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (rep_dir / "llm_rounds.md").write_text(_md_rounds(rep), encoding="utf-8")
        print(f"已写出：{rep_dir / 'llm_rounds.json'}  {rep_dir / 'llm_rounds.md'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=_db_path)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument(
        "--prompt",
        choices=["fixed", "legacy"],
        default="fixed",
        help="fixed=当前生产提示词（默认）；legacy=修复前基线，用于 A/B 闭环对照",
    )
    ap.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="多轮稳定性模式：只跑功能维度 N 轮（默认 1=完整轨道）。"
             "N>1 时跳过 RAG/判官/稳定性维度，省 API 成本，结果写 llm_rounds.json，不覆盖主基线",
    )
    args = ap.parse_args()
    db_path = args.db
    if db_path != _db_path:
        agent_db.set_db_path(db_path)
    if args.prompt == "legacy":
        agent_llm._SQL_SYSTEM = LEGACY_SQL_SYSTEM
        print("⚠ 使用【修复前】SQL 提示词（A/B 基线对照），不影响生产代码")

    print(f"LLM enabled: {agent_llm.llm_enabled()}  model={os.getenv('LLM_MODEL')}  db={db_path}")
    if args.rounds > 1:
        run_rounds(args)
        return
    func = run_functional(_load_functional())
    rag = run_rag(_load_rag())
    stab = run_stability()

    # RAG 忠实度 / 相关性：自研 LLM-as-judge（判据对齐 RAGAS；不依赖 ragas 包）
    from testsuite import rag_judge

    judge = rag_judge.run(
        [
            {"question": d["question"], "answer": d["answer_full"], "contexts": d["contexts"]}
            for d in rag["details"]
        ]
    )

    # 判官可信度（第十四轮）：重复判定测稳定性 + 金标样本测判别力。
    # 只取前 10 条做重复判定，控制 API 成本（每多一次重复 = 多一轮全样本调用）。
    from testsuite import judge_agreement

    agreement = judge_agreement.run(
        [
            {"question": d["question"], "answer": d["answer_full"], "contexts": d["contexts"]}
            for d in rag["details"][:10]
        ],
        repeats=2,
    )

    summary = {
        "llm_model": os.getenv("LLM_MODEL"),
        "db": db_path,
        "prompt_variant": args.prompt,
        "functional": {"accuracy": round(func["accuracy"], 4), "accuracy_lenient": round(func["accuracy_lenient"], 4),
                        "accuracy_semantic": round(func["accuracy_semantic"], 4),
                        "passed": func["passed"], "passed_lenient": func["passed_lenient"],
                        "passed_semantic": func["passed_semantic"], "total": func["total"]},
        "rag": {"recall3_rate": round(rag["recall3_rate"], 4), "recall3": rag["recall3"], "total": rag["total"]},
        "rag_judge": judge,
        "judge_agreement": agreement,
        "stability": {"consistency_rate": round(stab["consistency_rate"], 4), "consistent": stab["consistent"], "questions": stab["questions"]},
        "functional_details": func["details"],
        "rag_details": rag["details"],
        "stability_details": stab["details"],
    }
    print("\n=== 真实 LLM 轨道结果 ===")
    print(f"功能准确性（严格·结果集完全一致）：{func['passed']}/{func['total']} = {func['accuracy']:.1%}")
    print(f"功能准确性（宽松·按期望列投影）：{func['passed_lenient']}/{func['total']} = {func['accuracy_lenient']:.1%}")
    print(f"功能准确性（语义等价·数值容差+列对齐+允许多选列）：{func['passed_semantic']}/{func['total']} = {func['accuracy_semantic']:.1%}")
    print(f"RAG 召回@3：{rag['recall3']}/{rag['total']} = {rag['recall3_rate']:.1%}")
    if judge.get("status") == "ok":
        s = judge.get("scores", {})
        print(f"RAG 忠实度（自研判官）：{s.get('faithfulness')} ｜ 回答相关性：{s.get('answer_relevancy')}")
    else:
        print(f"RAG 忠实度（自研判官）：{judge.get('status')} —— {judge.get('reason', '')}")
    if agreement.get("status") == "ok":
        c = agreement["consistency"]
        cal = agreement["calibration"]
        print(f"判官可信度：重复一致率 {c.get('agreement')}（κ={c.get('kappa')}，MAE={c.get('mean_abs_diff')}）｜"
              f"金标准确率 {cal.get('accuracy')}（忠实组均值 {cal.get('mean_faithful')} vs 编造组 {cal.get('mean_fabricated')}）")
        print(f"  → {agreement.get('conclusion')}")
    print(f"规划稳定性（temp=0 多次一致率）：{stab['consistent']}/{stab['questions']} = {stab['consistency_rate']:.1%}")

    if not args.no_write:
        rep = ROOT / "data" / "report"
        rep.mkdir(parents=True, exist_ok=True)
        suffix = "" if args.prompt == "fixed" else f"_{args.prompt}"
        (rep / f"metrics_llm{suffix}.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (rep / f"llm_track{suffix}.md").write_text(_md(summary), encoding="utf-8")
        print(f"\n已写出：{rep / f'metrics_llm{suffix}.json'}  {rep / f'llm_track{suffix}.md'}")


if __name__ == "__main__":
    main()
