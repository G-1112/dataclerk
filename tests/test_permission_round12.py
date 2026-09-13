"""第十二轮安全/正确性修复的回归测试。

覆盖本轮审查实证并修复的问题：
  P0-1  students 主表 UNION 绕过泄露姓名（权限层只校验第一个 SELECT 分支）
  P0-2  客户端伪造 user_id/role（新增 HMAC 身份签名 + 库级默认角色收紧）
  P0-3  teacher 院系解析失败 fail-open（改为 fail-close）
  P1-1  teacher 单生明细被 USING / GROUP BY 形态绕过（正则 → AST 判定）
  P1-2  「约束误继承率」回归方向判反
  P1-3  安全语义判定的 PII 盲区（姓名单元格）
  P2-3  trace_view.load_trace 路径穿越
  P2-4  合法 CTE 查询被未知表检测误杀
"""

from __future__ import annotations

import pytest

from agent.guard.permission import PermissionGuard

# ----------------------------------------------------------------------
# P0-1：students 主表 UNION / 子查询必须 fail-close
# ----------------------------------------------------------------------


def test_students_master_union_name_leak_rejected():
    """实证攻击：`COUNT(*)` 当通行证，UNION 第二分支裸读全部姓名。"""
    guard = PermissionGuard(role="student", current_user="S00001")
    r = guard.check("SELECT COUNT(*) FROM students UNION SELECT name FROM students")
    assert not r.allowed


def test_students_master_union_all_rejected():
    guard = PermissionGuard(role="student", current_user="S00001")
    r = guard.check("SELECT COUNT(*) FROM students UNION ALL SELECT student_id FROM students")
    assert not r.allowed


def test_students_master_single_select_aggregate_still_allowed():
    """正常聚合查询不受 fail-close 影响。"""
    guard = PermissionGuard(role="student", current_user="S00001")
    r = guard.check("SELECT college, COUNT(*) FROM students GROUP BY college")
    assert r.allowed, r.reason


# ----------------------------------------------------------------------
# P1-1：teacher 单生明细 AST 判定（替代正则）
# ----------------------------------------------------------------------


def test_teacher_using_join_detail_rejected():
    """USING 形态曾绕过 `student_id\\s*=` 正则（实证可拿到本院逐生成绩）。"""
    guard = PermissionGuard(role="teacher", current_user="T00001", teacher_college="计算机学院")
    r = guard.check(
        "SELECT e.student_id, e.score FROM enrollments e "
        "JOIN students s USING(student_id) WHERE s.college = '计算机学院' LIMIT 100"
    )
    assert not r.allowed
    assert "单个学生" in r.reason


def test_teacher_group_by_student_id_rejected():
    """GROUP BY student_id 等价于逐生明细（实证曾放行）。"""
    guard = PermissionGuard(role="teacher", current_user="T00001", teacher_college="计算机学院")
    r = guard.check(
        "SELECT e.student_id, AVG(e.score) FROM enrollments e "
        "JOIN students s ON e.student_id = s.student_id "
        "WHERE s.college = '计算机学院' GROUP BY e.student_id"
    )
    assert not r.allowed


def test_teacher_select_star_private_rejected():
    guard = PermissionGuard(role="teacher", current_user="T00001", teacher_college="计算机学院")
    r = guard.check("SELECT * FROM enrollments")
    assert not r.allowed


def test_teacher_normal_aggregate_join_allowed():
    """原正则会把 JOIN ON 的 `e.student_id = s.student_id` 误判为引用他人而整查拒绝
    （实证误伤）；AST 判定后正常院系聚合应放行。"""
    guard = PermissionGuard(role="teacher", current_user="T00001", teacher_college="计算机学院")
    r = guard.check(
        "SELECT s.college, AVG(e.score) FROM enrollments e "
        "JOIN students s ON e.student_id = s.student_id "
        "WHERE s.college = '计算机学院' GROUP BY s.college"
    )
    assert r.allowed, r.reason


def test_teacher_count_student_id_allowed():
    """聚合函数内部的 student_id 引用合法（COUNT(student_id)）。"""
    guard = PermissionGuard(role="teacher", current_user="T00001", teacher_college="计算机学院")
    r = guard.check(
        "SELECT COUNT(student_id) FROM enrollments e JOIN students s ON e.student_id = s.student_id "
        "WHERE s.college = '计算机学院'"
    )
    assert r.allowed, r.reason


# ----------------------------------------------------------------------
# P2-4：CTE 不再被未知表检测误杀
# ----------------------------------------------------------------------


def test_cte_not_treated_as_unknown_table():
    guard = PermissionGuard(role="admin")
    r = guard.check("WITH t AS (SELECT * FROM enrollments) SELECT * FROM t")
    assert r.allowed, r.reason


def test_cte_unknown_table_still_rejected():
    """CTE 修复不能把真正的未知表放进来。"""
    guard = PermissionGuard(role="admin")
    r = guard.check("WITH t AS (SELECT * FROM enrollments) SELECT * FROM evil_table")
    assert not r.allowed
    assert "不存在的表" in r.reason


# ----------------------------------------------------------------------
# P0-3：teacher 院系解析失败 → fail-close（需真实 DB 路径，用 tmp 库）
# ----------------------------------------------------------------------


@pytest.fixture
def tmp_db_path(tmp_path):
    """建一个最小校园库并把全局 DB 指过去（用 db 模块的真实接口）。"""
    import sqlite3

    from agent import db as db_mod

    db_file = tmp_path / "t.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE teachers (teacher_id TEXT PRIMARY KEY, name TEXT, college TEXT, title TEXT)")
    conn.execute("INSERT INTO teachers VALUES ('T00001', '教师1', '计算机学院', '教授')")
    conn.execute("CREATE TABLE students (student_id TEXT, name TEXT, college TEXT)")
    conn.execute("INSERT INTO students VALUES ('S00001', '张三', '计算机学院')")
    conn.execute("INSERT INTO students VALUES ('S00002', '李四', '外国语学院')")
    conn.execute("CREATE TABLE enrollments (student_id TEXT, course_id TEXT, score INTEGER)")
    conn.execute("INSERT INTO enrollments VALUES ('S00001', 'C001', 90)")
    conn.commit()
    conn.close()

    db_mod.set_db_path(str(db_file))
    yield str(db_file)
    db_mod.set_db_path(db_mod.DEFAULT_DB)


def test_teacher_college_resolution_failure_fails_closed(tmp_db_path):
    """工号不存在时院系解析为 None，修复前会以无约束 guard 放行（fail-open）。"""
    from agent.tools.execute_sql import execute_sql

    res = execute_sql("SELECT student_id, score FROM enrollments LIMIT 5", role="teacher", current_user="T99999")
    assert not res["ok"]
    assert "院系" in res["error"]


def test_teacher_valid_college_still_works(tmp_db_path):
    from agent.tools.execute_sql import execute_sql

    res = execute_sql("SELECT COUNT(*) FROM students", role="teacher", current_user="T00001")
    assert res["ok"], res.get("error")


# ----------------------------------------------------------------------
# P0-2：身份签名 + 库级默认角色
# ----------------------------------------------------------------------


def test_library_default_role_is_student():
    import inspect

    from agent.graph import CampusAgent

    sig = inspect.signature(CampusAgent.chat)
    assert sig.parameters["role"].default == "student"


def test_http_requires_token_when_secret_set(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATACLERK_AUTH_SECRET", "test-secret")
    from agent.serve import app, expected_token

    client = TestClient(app)
    body = {"question": "计算机学院有多少学生？", "role": "student", "user_id": "S00001", "session_id": "s1"}
    r = client.post("/chat", json=body)
    assert r.status_code == 401

    body["token"] = expected_token("S00001", "student", "s1", "test-secret")
    r = client.post("/chat", json=body)
    assert r.status_code == 200


def test_http_wrong_user_token_rejected(monkeypatch):
    """拿自己的 token 冒充他人 → 401（token 与 user_id 绑定）。"""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATACLERK_AUTH_SECRET", "test-secret")
    from agent.serve import app, expected_token

    client = TestClient(app)
    body = {
        "question": "S00002 的成绩",
        "role": "student",
        "user_id": "S00002",  # 自称受害者
        "session_id": "s1",
        "token": expected_token("S00001", "student", "s1", "test-secret"),  # 但只有攻击者自己的签名
    }
    r = client.post("/chat", json=body)
    assert r.status_code == 401


def test_health_reports_auth_enabled(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.delenv("DATACLERK_AUTH_SECRET", raising=False)
    from agent.serve import app

    client = TestClient(app)
    assert client.get("/health").json()["auth_enabled"] is False


# ----------------------------------------------------------------------
# P2-2：会话槽位按 (session_id, user_id) 隔离
# ----------------------------------------------------------------------


def test_slots_isolated_by_user():
    from agent.graph import CampusAgent

    agent = CampusAgent()
    s1 = agent._slots_for("default", "S00001")
    s2 = agent._slots_for("default", "S00002")
    assert s1 is not s2
    # 同 (session, user) 命中同一槽位
    assert agent._slots_for("default", "S00001") is s1


def test_slots_lru_cap():
    from agent.graph import CampusAgent

    agent = CampusAgent()
    agent._max_slot_keys = 5
    for i in range(10):
        agent._slots_for(f"s{i}", "u")
    assert len(agent._slots) == 5


# ----------------------------------------------------------------------
# P1-2：回归方向（误继承率属越小越好）
# ----------------------------------------------------------------------


def test_misinherit_rate_is_lower_better():
    from testsuite.metrics import _lower_is_better, compare_runs, regression_summary

    assert _lower_is_better("约束误继承率")
    deltas = compare_runs(
        {"multiturn": {"约束误继承率": 0.0}},
        {"multiturn": {"约束误继承率": 0.3}},
    )
    summary = regression_summary(deltas)
    assert summary["has_regression"], "误继承率 0→0.3 必须判为回归而非提升"


# ----------------------------------------------------------------------
# P1-3：语义判定的 PII（姓名）盲区
# ----------------------------------------------------------------------


def test_semantic_verdict_flags_name_leak(tmp_db_path, monkeypatch):
    from testsuite import run_suite

    monkeypatch.setattr(run_suite, "_KNOWN_NAMES", None)
    monkeypatch.setattr(run_suite, "_OWN_NAME_CACHE", {})

    # 放行且返回他人姓名 → 越权泄露（原实现只找学号形态，会判「已中和」）
    passed, verdict, _ = run_suite._semantic_verdict(
        {"ok": True, "rows": [[3], ["李四"]]}, "S00001"
    )
    assert not passed
    assert verdict == "越权泄露"

    # 本人姓名不算泄露
    passed, verdict, _ = run_suite._semantic_verdict(
        {"ok": True, "rows": [["张三"]]}, "S00001"
    )
    assert passed


# ----------------------------------------------------------------------
# P2-3：trace_view 路径穿越
# ----------------------------------------------------------------------


def test_trace_view_rejects_traversal():
    from testsuite.report.trace_view import load_trace

    assert load_trace("../../etc/passwd") == []
    assert load_trace("..\\..\\windows\\win.ini") == []


# ----------------------------------------------------------------------
# 新增对抗用例存在性（防回归被误删）
# ----------------------------------------------------------------------


def test_round12_adversarial_cases_present():
    from testsuite.cases import security_cases

    ids = {c["id"] for c in security_cases()}
    assert {"D4-012", "D4-013", "D4-014", "D4-015", "D4-016"} <= ids


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
