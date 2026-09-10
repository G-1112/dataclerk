"""跑批对比（回归检测）测试（第四轮交互增强）。

测试套件的价值不止于「这次多少分」，更在于「比上次退步了吗」。
本模块校验 metrics.compare_runs / regression_summary 的判定语义，
以及报告中的「与上次对比」区块渲染。
"""

from __future__ import annotations

from testsuite import metrics as M
from testsuite.report.generate_report import DIMENSIONS, render_html


def _metrics(**dims) -> dict:
    return dims


# ------------------------------------------------------------------ 基础比较
def test_detects_improvement():
    prev = {"functional": {"执行准确率": 0.90}}
    cur = {"functional": {"执行准确率": 0.95}}
    rows = M.compare_runs(prev, cur)
    assert len(rows) == 1
    assert rows[0]["direction"] == "up"
    assert rows[0]["delta"] == 0.05


def test_detects_regression():
    prev = {"security": {"越权拦截率": 1.0}}
    cur = {"security": {"越权拦截率": 0.85}}
    rows = M.compare_runs(prev, cur)
    assert rows[0]["direction"] == "down"
    assert rows[0]["delta"] == -0.15


def test_same_is_not_flagged():
    prev = {"rag": {"召回@3": 0.8}}
    cur = {"rag": {"召回@3": 0.8}}
    assert M.compare_runs(prev, cur)[0]["direction"] == "same"


def test_handles_missing_dimension_on_either_side():
    """新增/删除的维度无从比较，应被跳过而不是被判成回归或提升。"""
    prev = {"functional": {"执行准确率": 0.9}}
    cur = {"functional": {"执行准确率": 0.9}, "newdim": {"x": 0.5}}
    rows = M.compare_runs(prev, cur)
    # 只出现单侧的维度不产生比较行（没有基线可比）
    assert not any(r["dimension"] == "newdim" for r in rows)
    assert any(r["dimension"] == "functional" for r in rows)
    # 所有产出的行都同时具备 prev 与 cur
    assert all(r["prev"] is not None and r["cur"] is not None for r in rows)


def test_dimension_removed_in_current_run_is_skipped():
    """上一轮有、本轮没有的维度同样不参与比较。"""
    prev = {"olddim": {"x": 0.9}, "functional": {"执行准确率": 0.9}}
    cur = {"functional": {"执行准确率": 0.9}}
    rows = M.compare_runs(prev, cur)
    assert not any(r["dimension"] == "olddim" for r in rows)


def test_ignores_non_numeric_metrics():
    prev = {"functional": {"备注": "旧"}}
    cur = {"functional": {"备注": "新"}}
    assert M.compare_runs(prev, cur) == []


def test_handles_none_input_safely():
    assert M.compare_runs(None, {"a": {"b": 1}}) == []
    assert M.compare_runs({"a": {"b": 1}}, None) == []


def test_handles_non_dict_dimension_safely():
    """某维度值不是 dict（脏数据）时跳过而非抛异常。"""
    prev = {"functional": "坏数据"}
    cur = {"functional": {"执行准确率": 0.9}}
    assert isinstance(M.compare_runs(prev, cur), list)


# ------------------------------------------------------------------ 回归汇总
def test_regression_summary_flags_regression():
    rows = M.compare_runs(
        {"security": {"越权拦截率": 1.0}}, {"security": {"越权拦截率": 0.8}}
    )
    s = M.regression_summary(rows)
    assert s["has_regression"] is True
    assert s["regression_count"] == 1


def test_case_count_change_is_not_regression():
    """「用例数」变化属规模调整，不应判为回归。"""
    rows = M.compare_runs(
        {"security": {"用例数": 60, "越权拦截率": 1.0}},
        {"security": {"用例数": 56, "越权拦截率": 1.0}},
    )
    s = M.regression_summary(rows)
    assert s["has_regression"] is False, s["regressions"]


def test_average_steps_change_is_not_regression():
    rows = M.compare_runs(
        {"tool_calls": {"平均步数": 3.0}}, {"tool_calls": {"平均步数": 2.0}}
    )
    assert M.regression_summary(rows)["has_regression"] is False


def test_no_change_means_no_regression():
    rows = M.compare_runs({"a": {"x": 1.0}}, {"a": {"x": 1.0}})
    s = M.regression_summary(rows)
    assert s["has_regression"] is False
    assert s["improvement_count"] == 0


# ------------------------------------------------- 越小越好的指标（时延类）
# 第四轮实测发现的语义 bug：首版把所有「数值下降」都判为回归，
# 于是把「P95 时延 95.8ms → 5.5ms」这种优化误报成回归。
def test_latency_decrease_is_improvement_not_regression():
    prev = {"performance": {"P95时延ms": 95.8, "平均时延ms": 64.0}}
    cur = {"performance": {"P95时延ms": 5.5, "平均时延ms": 4.3}}
    rows = M.compare_runs(prev, cur)
    assert all(r["direction"] == "up" for r in rows), rows
    assert all(r["lower_is_better"] for r in rows)
    s = M.regression_summary(rows)
    assert s["has_regression"] is False, s["regressions"]
    assert s["improvement_count"] == 2


def test_latency_increase_is_regression():
    prev = {"performance": {"P95时延ms": 5.5}}
    cur = {"performance": {"P95时延ms": 20.0}}
    rows = M.compare_runs(prev, cur)
    assert rows[0]["direction"] == "down"
    assert M.regression_summary(rows)["has_regression"] is True


def test_accuracy_increase_is_still_improvement():
    """越大越好的指标语义不受影响。"""
    prev = {"security": {"越权拦截率": 0.8}}
    cur = {"security": {"越权拦截率": 1.0}}
    rows = M.compare_runs(prev, cur)
    assert rows[0]["direction"] == "up"
    assert rows[0]["lower_is_better"] is False


def test_lower_is_better_detection_covers_common_names():
    for name in ("P95时延ms", "平均时延ms", "耗时", "latency_p95", "响应延迟"):
        assert M._lower_is_better(name), name
    for name in ("执行准确率", "越权拦截率", "召回@3", "忠实度"):
        assert not M._lower_is_better(name), name


# ------------------------------------------------------------------ 报告渲染
def _mt(value=0.9) -> dict:
    return {d[0]: {"指标": value} for d in DIMENSIONS}


def _mut() -> dict:
    return {"mutation_score": "7/7", "mutants": []}


def test_report_shows_comparison_when_prev_provided():
    prev = {"security": {"指标": 1.0}}
    cur = _mt(0.9)
    html = render_html(cur, _mut(), [], prev)
    assert "与上次对比" in html
    assert "回归" in html


def test_report_prompts_first_run_when_no_prev():
    html = render_html(_mt(), _mut(), [], None)
    assert "与上次对比" in html
    assert "首次跑批" in html


def test_report_notes_scale_metrics_excluded():
    html = render_html(_mt(), _mut(), [], {"functional": {"指标": 0.5}})
    assert "不计入回归判定" in html


def test_render_html_prev_metrics_is_optional_keyword():
    """保持向后兼容：不传 prev_metrics 也要能渲染。"""
    html = render_html(_mt(), _mut(), [])
    assert html.rstrip().endswith("</html>")
