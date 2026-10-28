"""口径复算：不重跑大模型，直接重放已落盘的 LLM SQL（零 API 成本）。

为什么需要它
------------
真实 LLM 轨道的 `functional_details` 里保存了每条用例模型**实际生成的 SQL**。
当等价判定口径要调整时（例如新增 `column_subset` 档位——允许"问老师只答姓名"
这类少列写法），必须能回答"口径改动会把准确率抬到多少"。重跑 59 条要十几分钟
和真实 token，而 SQL 已经落盘：同一份 SQL + 同一份数据库重放，严格判定必然与
原跑批一致。脚本会把这一点作为自校验（`strict_mismatch` 必须为 0），
一旦不一致说明数据库或期望变更了，结论不可直接引用。

用法
----
python -m testsuite.replay_llm_details \
    --details data/report/metrics_llm.json --db data/campus.db \
    --out data/report/replay_llm.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # Windows 控制台默认 GBK
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

from . import equivalence  # noqa: E402
from .cases import functional_cases  # noqa: E402
from .run_suite import _expected_rows_full, _atomic_write_text  # noqa: E402

RELAX_SPEC = {"allow_missing_columns": True}


def _exec(sql: str) -> tuple[list[list], list[str]]:
    """执行一条只读查询，返回 (rows, columns)。"""
    from agent.db import connect

    conn = connect()
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in (cur.description or [])]
        return [list(r) for r in cur.fetchall()], cols
    finally:
        conn.close()


def replay(details: dict, relax_spec: dict | None = None) -> dict:
    """对已落盘的模型 SQL 复算三种口径，返回报告字典。"""
    relax_spec = {**RELAX_SPEC, **(relax_spec or {})}
    expected_by_id = {c["id"]: c["expected_sql"] for c in functional_cases()}
    rows: list[dict] = []
    strict_mismatch: list[str] = []

    for d in details.get("functional_details", []):
        sql = d.get("sql_llm") or ""
        exp_sql = expected_by_id.get(d["id"], "")
        item = {
            "id": d["id"], "question": d["question"],
            "stored_passed": bool(d.get("passed")), "sql_llm": sql,
        }
        if not sql or not exp_sql:
            item.update(replay_ok=False, note="缺少模型 SQL 或期望 SQL，跳过")
            rows.append(item)
            continue
        try:
            exp_rows, exp_cols = _expected_rows_full(exp_sql)
            act_rows, act_cols = _exec(sql)
        except Exception as e:  # noqa: BLE001
            item.update(replay_ok=False, note=f"重放失败：{type(e).__name__}: {e}")
            rows.append(item)
            continue

        strict = equivalence.compare(exp_rows, act_rows, exp_cols, act_cols)
        relaxed = equivalence.compare(exp_rows, act_rows, exp_cols, act_cols, relax_spec)
        item.update(
            replay_ok=True,
            strict=strict["equivalent"], strict_level=strict["level"],
            relaxed=relaxed["equivalent"], relaxed_level=relaxed["level"],
            relaxed_reason=relaxed["reason"],
        )
        if strict["equivalent"] != item["stored_passed"]:
            strict_mismatch.append(d["id"])
        rows.append(item)

    ok = [r for r in rows if r.get("replay_ok")]
    n = len(ok)
    summary = {
        "total": n,
        "strict_passed": sum(1 for r in ok if r["strict"]),
        "relaxed_passed": sum(1 for r in ok if r["relaxed"]),
        "relaxed_gain": sum(1 for r in ok if r["relaxed"] and not r["strict"]),
        "strict_mismatch": strict_mismatch,
        "relax_spec": relax_spec,
    }
    if n:
        summary["strict_accuracy"] = round(summary["strict_passed"] / n, 4)
        summary["relaxed_accuracy"] = round(summary["relaxed_passed"] / n, 4)
    return {"summary": summary, "cases": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description="复算已落盘 LLM SQL 的等价口径")
    ap.add_argument("--details", default="data/report/metrics_llm.json")
    ap.add_argument("--db", default="data/campus.db")
    ap.add_argument("--out", default="data/report/replay_llm.json")
    args = ap.parse_args()

    from agent.db import set_db_path

    set_db_path(args.db)
    details = json.loads(Path(args.details).read_text(encoding="utf-8"))
    rep = replay(details)
    s = rep["summary"]

    print(f"重放用例：{s['total']} 条（数据库 {args.db}）")
    print(f"严格口径：{s['strict_passed']}/{s['total']} = {s.get('strict_accuracy')}")
    print(f"含列子集：{s['relaxed_passed']}/{s['total']} = {s.get('relaxed_accuracy')}"
          f"（新增 {s['relaxed_gain']} 条）")
    if s["strict_mismatch"]:
        print(f"⚠ 与原跑批严格判定不一致：{s['strict_mismatch']}（数据库或期望已变更，结论不可引用）")
    else:
        print("自校验通过：严格判定与原跑批逐条一致")
    for r in rep["cases"]:
        if r.get("replay_ok") and r["relaxed"] and not r["strict"]:
            print(f"  + {r['id']} → {r['relaxed_level']}｜{r['relaxed_reason']}")

    _atomic_write_text(Path(args.out), json.dumps(rep, ensure_ascii=False, indent=2))
    print(f"已写入 {args.out}")
    return 0 if not s["strict_mismatch"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
