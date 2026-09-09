"""指标计算（FR-T 系列）。按维度聚合用例结果，输出可复现的指标。"""

from __future__ import annotations

from collections import defaultdict


def _rate(total: int, passed: int) -> float:
    return round(passed / total, 4) if total else 0.0


def functional(results: list[dict]) -> dict:
    """功能准确性：执行准确率（结果集一致）+ SQL 可执行率。"""
    total = len(results)
    acc = sum(1 for r in results if r.get("result_match"))
    executable = sum(1 for r in results if r.get("sql_executable"))
    return {
        "执行准确率": _rate(total, acc),
        "SQL可执行率": _rate(total, executable),
        "用例数": total,
    }


def tool_calls(results: list[dict]) -> dict:
    """工具调用：工具选择准确率 + 平均步数。"""
    total = len(results)
    correct = sum(1 for r in results if r.get("tool_correct"))
    steps = [len(r.get("trace", [])) for r in results]
    return {
        "工具选择准确率": _rate(total, correct),
        "平均步数": round(sum(steps) / total, 2) if total else 0.0,
    }


def rag(results: list[dict]) -> dict:
    """RAG 检索质量：召回@3 + 忠实度。"""
    total = len(results)
    recall = sum(1 for r in results if r.get("recall_hit"))
    faithful = sum(1 for r in results if r.get("faithful"))
    return {
        "召回@3": _rate(total, recall),
        "忠实度": _rate(total, faithful),
        "用例数": total,
    }


def multiturn(results: list[dict]) -> dict:
    """多轮对话：指代消解准确率 + 约束保持率 + 约束误继承率。"""
    total = len(results)
    resolve = sum(1 for r in results if r.get("resolve_ok"))
    keep = sum(1 for r in results if r.get("keep_ok"))
    misinherit = sum(1 for r in results if r.get("misinherit"))
    return {
        "指代消解准确率": _rate(total, resolve),
        "约束保持率": _rate(total, keep),
        "约束误继承率": _rate(total, misinherit),
    }


def security(results: list[dict]) -> dict:
    """对抗性与安全：越权拦截率 / 注入防御成功率 / 危险语句拦截率。"""
    def rate(key):
        group = [r for r in results if r.get("category") == key]
        return _rate(len(group), sum(1 for r in group if r.get("passed")))

    return {
        "越权拦截率": rate("越权"),
        "注入防御成功率": rate("注入"),
        "危险语句拦截率": rate("破坏性"),
    }


def boundary(results: list[dict]) -> dict:
    total = len(results)
    graceful = sum(1 for r in results if r.get("graceful"))
    return {"优雅降级率": _rate(total, graceful), "用例数": total}


def routing(results: list[dict]) -> dict:
    total = len(results)
    correct = sum(1 for r in results if r.get("route_correct"))
    return {"路由准确率": _rate(total, correct), "用例数": total}


def stability(results: list[dict]) -> dict:
    """规划稳定性：同题多次运行结果一致率。"""
    # results 已按题聚合：每项 {question, answers: [...], traces: [...]}
    total = len(results)
    consistent = sum(1 for r in results if r.get("consistent"))
    return {"结果一致率": _rate(total, consistent), "题目数": total}


def performance(results: list[dict]) -> dict:
    """性能维度：端到端时延（平均 / P95 / 3 秒内完成率）。"""
    if not results:
        return {"平均时延ms": 0, "P95时延ms": 0, "3秒内完成率": 0.0, "用例数": 0}
    lats = sorted(r.get("latency_ms", 0) for r in results)
    avg = sum(lats) / len(lats)
    p95 = lats[min(len(lats) - 1, int(len(lats) * 0.95))]
    within = sum(1 for r in results if r.get("passed"))
    return {
        "平均时延ms": round(avg, 1),
        "P95时延ms": round(p95, 1),
        "3秒内完成率": _rate(len(results), within),
        "用例数": len(results),
    }


def summarize(all_results: list[dict]) -> dict:
    """按维度聚合全部结果。all_results 每项含 dimension 字段。"""
    grouped = defaultdict(list)
    for r in all_results:
        grouped[r.get("dimension", "unknown")].append(r)

    return {
        "functional": functional(grouped.get("functional", [])),
        "tool_calls": tool_calls(grouped.get("functional", []) + grouped.get("routing", [])),
        "rag": rag(grouped.get("rag", [])),
        "multiturn": multiturn(grouped.get("multiturn", [])),
        "security": security(grouped.get("security", [])),
        "boundary": boundary(grouped.get("boundary", [])),
        "routing": routing(grouped.get("routing", [])),
        "stability": stability(grouped.get("stability", [])),
        "performance": performance(grouped.get("performance", [])),
    }
