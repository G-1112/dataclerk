"""指标计算（FR-T 系列）。按维度聚合用例结果，输出可复现的指标。"""

from __future__ import annotations

from collections import defaultdict


def _rate(total: int, passed: int) -> float:
    return round(passed / total, 4) if total else 0.0


def functional(results: list[dict]) -> dict:
    """功能准确性：执行准确率（结果集严格一致）+ SQL 可执行率。

    第十四轮新增 `语义等价准确率`：严格比对之外，按「数值容差 / 列对齐 / 集合语义」
    再判一次，用来量化"有多条用例其实写对了、只是写法不同"。
    **主指标仍是严格口径**，语义口径只做补充说明，两者都进报告。
    """
    total = len(results)
    acc = sum(1 for r in results if r.get("result_match"))
    executable = sum(1 for r in results if r.get("sql_executable"))
    semantic = sum(1 for r in results if r.get("equivalent_semantic"))
    rescued = [r for r in results if r.get("equivalent_semantic") and not r.get("result_match")]
    return {
        "执行准确率": _rate(total, acc),
        "语义等价准确率": _rate(total, semantic),
        "严格判错但语义等价": len(rescued),
        "SQL可执行率": _rate(total, executable),
        "用例数": total,
    }


def tool_calls(results: list[dict]) -> dict:
    """工具调用：工具选择准确率 + 平均步数。

    第九轮修复：只统计**真正测量了** tool_correct 的用例。
    原实现把没有该字段的用例一律计为「工具选错」，导致官方维度
    「工具调用」的准确率被稀释（实测 18/(18+33)=35.29%，而功能用例实为 100%）。
    步数优先取结果显式携带的 steps，缺失时回退到 trace 长度。
    """
    measured = [r for r in results if "tool_correct" in r]
    total = len(measured)
    correct = sum(1 for r in measured if r.get("tool_correct"))
    steps = [r.get("steps", len(r.get("trace", []))) for r in measured]
    return {
        "工具选择准确率": _rate(total, correct),
        "平均步数": round(sum(steps) / total, 2) if total else 0.0,
        "用例数": total,
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
    """多轮对话：指代消解准确率 + 约束保持率 + 约束误继承率。

    第九轮修复：与 tool_calls 同理，各子指标只统计**真实赋值过**的轮次。
    原实现把未赋值的 keep_ok 一律当 False → 「约束保持率」恒为 0.0，
    会被评委误读为多轮上下文记忆完全失效。
    """

    def rate_of(key):
        measured = [r for r in results if key in r]
        return _rate(len(measured), sum(1 for r in measured if r.get(key)))

    return {
        "指代消解准确率": rate_of("resolve_ok"),
        "约束保持率": rate_of("keep_ok"),
        "约束误继承率": rate_of("misinherit"),
        "用例数": len(results),
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
        "用例数": len(results),
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
    return {"结果一致率": _rate(total, consistent), "题目数": total, "用例数": total}


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


# ---------------------------------------------------------------------------
# 与上一次跑批对比（第四轮交互增强）
#
# 测试套件的价值不止于「这次多少分」，更在于「比上次退步了吗」。
# 这里把两次 metrics 逐指标比对，标出提升/回归/持平，
# 让评审与开发一眼看到改动带来的影响。
# ---------------------------------------------------------------------------
# 越小越好的指标（时延/错误类）。其余指标默认越大越好。
# 第四轮实测发现的语义 bug：首次实现把所有指标的「数值下降」都判为回归，
# 于是把「P95 时延 95.8ms → 5.5ms」这种明显优化报成了回归。
# 第十二轮修复（实证 P1）：「约束误继承率」0.0 → 0.3（变差）曾被判为
# direction=up（提升）且 has_regression=False —— 回归门禁方向判反。
_LOWER_IS_BETTER_HINTS = ("时延", "latency", "耗时", "延迟", "误继承", "失败率", "错误率", "error", "fail")


def _lower_is_better(metric: str) -> bool:
    name = (metric or "").lower()
    return any(h in name for h in _LOWER_IS_BETTER_HINTS)


def compare_runs(prev: dict, cur: dict) -> list[dict]:
    """逐 (维度, 指标) 比较两次跑批。

    返回 [{dimension, metric, prev, cur, delta, direction}]。
    direction ∈ {up, down, same}，语义统一为**好坏方向**而非数值方向：
      - 越大越好的指标（准确率等）：数值上升 = up（提升）
      - 越小越好的指标（时延等）  ：数值下降 = up（提升）
    「用例数」「平均步数」属规模/行为指标，仍会列出但不计入回归判定。
    """
    rows: list[dict] = []
    if not isinstance(prev, dict) or not isinstance(cur, dict):
        return rows

    for dim in sorted(set(prev) | set(cur)):
        p_dim = prev.get(dim) or {}
        c_dim = cur.get(dim) or {}
        if not isinstance(p_dim, dict) or not isinstance(c_dim, dict):
            continue
        for metric in sorted(set(p_dim) | set(c_dim)):
            pv = p_dim.get(metric)
            cv = c_dim.get(metric)
            if not isinstance(pv, (int, float)) or not isinstance(cv, (int, float)):
                continue
            diff = round(cv - pv, 4)
            # 数值变化方向
            numeric = "same"
            if diff > 1e-9:
                numeric = "up_num"
            elif diff < -1e-9:
                numeric = "down_num"

            # 转换为好坏方向
            if numeric == "same":
                direction = "same"
            elif _lower_is_better(metric):
                direction = "up" if numeric == "down_num" else "down"
            else:
                direction = "up" if numeric == "up_num" else "down"

            rows.append(
                {
                    "dimension": dim,
                    "metric": metric,
                    "prev": pv,
                    "cur": cv,
                    "delta": diff,
                    "direction": direction,
                    "lower_is_better": _lower_is_better(metric),
                }
            )
    return rows


def regression_summary(deltas: list[dict]) -> dict:
    """汇总回归情况；用例数变化不计为回归。"""
    ignored = {"用例数", "平均步数"}
    regressions = [
        d for d in deltas if d["direction"] == "down" and d["metric"] not in ignored
    ]
    improvements = [
        d for d in deltas if d["direction"] == "up" and d["metric"] not in ignored
    ]
    return {
        "regressions": regressions,
        "improvements": improvements,
        "regression_count": len(regressions),
        "improvement_count": len(improvements),
        "has_regression": bool(regressions),
    }
