# -*- coding: utf-8 -*-
"""扩容用例的入库前验证脚手架（一次性，验证后删除）。

纪律（memory 铁律）：先跑「已知正确」的基线，确认基线全过，再下结论。
本脚本把全部用例（旧 165 + 新 144）按 run_suite 的真实评测函数跑一遍，
任何一条失败都不准入库。

用法：
  python _verify_cases.py            # 全量验证 + 打印失败明细
  python _verify_cases.py --dim rag  # 只验证某一维度
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

from agent.graph import CampusAgent
from testsuite.cases import (
    MULTITURN,
    PERFORMANCE_QUESTIONS,
    STABILITY,
    boundary_cases,
    functional_cases,
    rag_cases,
    routing_cases,
    security_cases,
)
from testsuite.run_suite import (
    AgentClient,
    run_boundary,
    run_functional,
    run_multiturn,
    run_performance,
    run_rag,
    run_routing,
    run_security,
    run_stability,
)


def main() -> int:
    only = None
    if len(sys.argv) > 2 and sys.argv[1] == "--dim":
        only = sys.argv[2]

    agent = CampusAgent(db_path="data/campus.db", trace_dir="data/traces_verify")
    client = AgentClient(agent)

    # 计数（与 tests/test_cases_count.py 同一口径）
    counts = {
        "functional": len(functional_cases()),
        "security": len(security_cases()),
        "routing": len(routing_cases()),
        "rag": len(rag_cases()),
        "multiturn": sum(len(c["turns"]) for c in MULTITURN),
        "boundary": len(boundary_cases()),
        "stability": len(STABILITY),
        "performance": len(PERFORMANCE_QUESTIONS),
    }
    total = sum(counts.values())
    print("用例数：", counts, "合计", total)

    failures = []

    def check(dim: str, results: list[dict]) -> None:
        bad = [r for r in results if not r.get("passed")]
        print(f"[{dim}] {len(results) - len(bad)}/{len(results)} 过", flush=True)
        for r in bad[:20]:
            print(f"    FAIL {r.get('id')}: {str(r.get('answer') or r.get('question'))[:70]}", flush=True)
        if len(bad) > 20:
            print(f"    ...另有 {len(bad) - 20} 条失败", flush=True)
        failures.extend((dim, r.get("id")) for r in bad)

    if only in (None, "functional"):
        check("functional", run_functional(functional_cases(), client))
    if only in (None, "security"):
        check("security", run_security(security_cases(), client))
    if only in (None, "routing"):
        check("routing", run_routing(routing_cases(), client))
    if only in (None, "rag"):
        res, _ = run_rag(rag_cases(), client)
        check("rag", res)
    if only in (None, "multiturn"):
        check("multiturn", run_multiturn(MULTITURN, client))
    if only in (None, "boundary"):
        check("boundary", run_boundary(boundary_cases(), client))
    if only in (None, "stability"):
        check("stability", run_stability(STABILITY, client))
    if only in (None, "performance"):
        check("performance", run_performance(agent, client))

    print()
    if failures:
        print(f"合计失败 {len(failures)} 条：")
        for dim, cid in failures:
            print(f"  {dim:10s} {cid}")
        return 1
    print(f"全部通过（{total} 条）。可以更新守卫测试并跑全量套件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
