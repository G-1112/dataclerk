"""越权前置拦截 + 行为可视化测试（第四轮新增）。

背景（实测发现）：学生在问「S00002 这学期选了什么课」时，
路由把它判为 clarify 并回复「请补充具体条件」——
既不拒绝也不触发权限层，等于把越权请求当成「问得不清楚」。
安全上无泄露，但口径不一致。现改为路由前置拦截，明确拒绝。
"""

from __future__ import annotations

import json

import pytest

from agent.router import detect_foreign_student_id, route_question


# ------------------------------------------------------------------ 越权前置拦截
def test_detect_foreign_id_finds_other_student():
    assert detect_foreign_student_id("查询 S00002 的成绩", "S00001") == ["S00002"]


def test_detect_foreign_id_ignores_self():
    assert detect_foreign_student_id("查询 S00001 的成绩", "S00001") == []


def test_detect_foreign_id_case_insensitive():
    assert detect_foreign_student_id("查询 s00002 的成绩", "S00001") == ["S00002"]


def test_detect_foreign_id_multiple():
    ids = detect_foreign_student_id("S00002 和 S00003 的成绩", "S00001")
    assert ids == ["S00002", "S00003"]


def test_detect_foreign_id_no_id():
    assert detect_foreign_student_id("计算机学院有多少学生", "S00001") == []


def test_detect_foreign_id_empty_inputs():
    assert detect_foreign_student_id("", "S00001") == []
    assert detect_foreign_student_id("S00002 的成绩", "") == ["S00002"]


def test_route_denies_student_asking_other():
    r = route_question("S00002 这学期选了什么课", role="student", current_user="S00001")
    assert r["channel"] == "deny"
    assert "越权" in r["reason"]
    assert r.get("foreign_ids") == ["S00002"]


def test_route_denies_before_clarify():
    """此前这类问题会落到 clarify；必须优先拒绝。"""
    r = route_question("我要看 S00002 的奖学金情况", role="student", current_user="S00001")
    assert r["channel"] != "clarify"
    assert r["channel"] == "deny"


def test_route_allows_student_asking_self():
    r = route_question("查询 S00001 的成绩", role="student", current_user="S00001")
    assert r["channel"] != "deny"


def test_route_allows_teacher_querying_student():
    """教师查询学生数据属正常业务，不得拦截。"""
    r = route_question("查询 S00002 的成绩", role="teacher", current_user="T00001")
    assert r["channel"] != "deny"


def test_route_allows_admin_querying_student():
    r = route_question("查询 S00002 的成绩", role="admin", current_user="")
    assert r["channel"] != "deny"


def test_route_without_role_is_backward_compatible():
    """不传 role 时保持原有行为（既有调用点不受影响）。"""
    r = route_question("计算机学院有多少学生")
    assert r["channel"] == "sql"


def test_deny_channel_does_not_break_other_routes():
    assert route_question("奖学金怎么申请", role="student", current_user="S00001")["channel"] == "rag"
    assert route_question("怎么样", role="student", current_user="S00001")["channel"] == "clarify"


# ------------------------------------------------------------------ 行为可视化
from testsuite.report import trace_view as TV  # noqa: E402


def _steps():
    return [
        {"step": 1, "thought": "先看表结构", "tool": "get_schema", "args": {},
         "observation_summary": "students(...)", "latency_ms": 1},
        {"step": 2, "thought": "生成并执行 SQL", "tool": "execute_sql",
         "args": {"sql": "SELECT 1"}, "observation_summary": "返回 3 行", "latency_ms": 12},
    ]


def test_flag_step_marks_empty_observation():
    assert "观察为空" in TV.flag_step({"tool": "x", "observation_summary": "", "latency_ms": 0})


def test_flag_step_marks_high_latency():
    assert "高延迟" in TV.flag_step(
        {"tool": "x", "observation_summary": "ok", "latency_ms": TV.SLOW_MS}
    )


def test_flag_step_marks_unexpected_tool():
    flags = TV.flag_step(
        {"tool": "rag_search", "observation_summary": "ok", "latency_ms": 0},
        expected_tools={"execute_sql"},
    )
    assert "意外工具" in flags


def test_flag_step_clean_step_has_no_flags():
    assert TV.flag_step(
        {"tool": "execute_sql", "observation_summary": "返回 3 行", "latency_ms": 5},
        expected_tools={"execute_sql"},
    ) == []


def test_analyze_trace_summarizes():
    info = TV.analyze_trace(_steps(), channel="sql")
    assert info["steps"] == 2
    assert info["total_latency_ms"] == 13
    assert info["tools"] == ["get_schema", "execute_sql"]
    assert info["flagged"] == []


def test_analyze_trace_flags_unexpected_tool_via_channel():
    steps = [{"step": 1, "thought": "x", "tool": "retrieve_policy",
              "args": {}, "observation_summary": "ok", "latency_ms": 0}]
    info = TV.analyze_trace(steps, channel="sql")
    assert info["flagged"], "sql 通道里出现 rag 工具应被标为可疑"


def test_analyze_trace_empty():
    info = TV.analyze_trace([])
    assert info["steps"] == 0
    assert info["flagged"] == []


def test_render_timeline_contains_thought_and_tool():
    html = TV.render_timeline_html(_steps(), title="t")
    assert "先看表结构" in html
    assert "execute_sql" in html
    assert "12 ms" in html


def test_render_timeline_escapes_html():
    steps = [{"step": 1, "thought": "<script>alert(1)</script>", "tool": "x",
              "args": {}, "observation_summary": "<img src=x>", "latency_ms": 0}]
    html = TV.render_timeline_html(steps)
    assert "<script>" not in html, "未转义，存在 XSS 风险"
    assert "&lt;script&gt;" in html


def test_render_timeline_handles_empty():
    assert "无步骤" in TV.render_timeline_html([])


def test_load_trace_roundtrip(tmp_path):
    tid = "abc123"
    p = tmp_path / f"{tid}.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for s in _steps():
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    steps = TV.load_trace(tid, tmp_path)
    assert len(steps) == 2
    assert steps[0]["thought"] == "先看表结构"


def test_load_trace_missing_returns_empty(tmp_path):
    assert TV.load_trace("nope", tmp_path) == []


def test_load_trace_sorts_by_step(tmp_path):
    p = tmp_path / "x.jsonl"
    with p.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"step": 2, "tool": "b", "observation_summary": "ok"}) + "\n")
        f.write(json.dumps({"step": 1, "tool": "a", "observation_summary": "ok"}) + "\n")
    steps = TV.load_trace("x", tmp_path)
    assert [s["step"] for s in steps] == [1, 2]


def test_load_trace_skips_corrupt_lines(tmp_path):
    p = tmp_path / "y.jsonl"
    p.write_text('{"step":1,"tool":"a","observation_summary":"ok"}\n不是JSON\n', encoding="utf-8")
    assert len(TV.load_trace("y", tmp_path)) == 1


def test_render_audit_html_lists_cases(tmp_path):
    p = tmp_path / "c1.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for s in _steps():
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    cases = [{"id": "c1", "trace_id": "c1", "passed": True, "question": "q", "dimension": "sql"}]
    html = TV.render_audit_html(cases, trace_dir=tmp_path)
    assert "思考-行动链路审计" in html
    assert "execute_sql" in html


def test_render_audit_html_without_traces():
    html = TV.render_audit_html([], trace_dir=None)
    assert "思考-行动链路审计" in html


def test_real_traces_are_loadable():
    """集成：仓库中真实 trace 可被读取与标注。"""
    ids = TV.list_traces(limit=3)
    if not ids:
        pytest.skip("暂无 trace 记录")
    for tid in ids:
        steps = TV.load_trace(tid)
        assert steps, f"{tid} 读取为空"
        TV.analyze_trace(steps)
