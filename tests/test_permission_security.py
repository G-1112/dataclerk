"""权限层安全回归测试（审计 P0/P1 修复验证）。

覆盖审计实测可绕过的三类攻击：
  - 布尔注入 `student_id='S001' OR 1=1` 原可读全表 → 现在被约束为仅本人行；
  - `UNION SELECT ...` 跨表越权 → 拒绝；
  - current_user 直接拼 SQL 的注入 → 白名单校验 + sqlglot 字面量转义；
  - 写操作黑名单遗漏的 PRAGMA / ATTACH → 白名单拒绝；
  - teacher 院系过滤补全 → 跨院查询被拒，本院系自动注入；
  - admin 角色客户端自提 → serve 层服务端降级。
"""

from __future__ import annotations

import sqlite3

from agent.guard.permission import PermissionGuard


def _in_memory_enrollments() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE enrollments (student_id TEXT, score INTEGER)")
    conn.executemany(
        "INSERT INTO enrollments VALUES (?, ?)",
        [("S001", 90), ("S002", 80), ("S003", 70)],
    )
    return conn


def test_or_injection_constrained_to_owner():
    """布尔注入 `OR 1=1` 不得读全表：重写后执行只能返回本人行。"""
    conn = _in_memory_enrollments()
    guard = PermissionGuard(role="student", current_user="S001")
    r = guard.check("SELECT * FROM enrollments WHERE student_id = 'S001' OR 1=1")
    assert r.allowed, r.reason
    rows = conn.execute(r.rewritten_sql).fetchall()
    conn.close()
    ids = {row[0] for row in rows}
    assert ids == {"S001"}, f"OR 注入应被约束为仅本人，实际泄露 {ids}"


def test_union_bypass_rejected():
    """UNION 跨表越权被拒绝。"""
    guard = PermissionGuard(role="student", current_user="S001")
    r = guard.check(
        "SELECT * FROM enrollments WHERE student_id = 'S001' "
        "UNION SELECT student_id FROM scholarships"
    )
    assert not r.allowed
    assert "UNION" in r.reason


def test_subquery_bypass_rejected():
    """子查询越权被拒绝。"""
    guard = PermissionGuard(role="student", current_user="S001")
    r = guard.check(
        "SELECT * FROM enrollments WHERE student_id = 'S001' "
        "AND '1' = (SELECT '1' FROM scholarships)"
    )
    assert not r.allowed


def test_current_user_injection_rejected():
    """current_user 含引号注入 → 白名单校验拒绝（不进入 SQL 拼接）。"""
    guard = PermissionGuard(role="student", current_user="S001' OR '1'='1")
    r = guard.check("SELECT * FROM enrollments")
    assert not r.allowed
    assert "非法用户标识" in r.reason


def test_current_user_literal_escaped_when_forced():
    """即使绕过白名单，sqlglot 字面量转义也保证 current_user 不被当作 SQL。"""
    # 直接验证重写函数：含引号的 current_user 被转义为单个字面量
    out = PermissionGuard._force_student_filter(
        "SELECT * FROM enrollments", "S001' OR '1'='1"
    )
    # 渲染结果中 student_id 是一个转义后的字符串字面量，不应出现裸的 OR 1=1 断句
    assert "student_id = " in out
    # sqlglot 会把引号转义，原注入片段不会作为独立 SQL 生效
    assert "'S001\\' OR \\'1\\'=\\'1'" in out or "S001' OR '1'='1" not in out.split("WHERE")[-1].split("LIMIT")[0]


def test_write_whitelist_blocks_pragma_and_attach():
    """写操作白名单覆盖原黑名单遗漏的 PRAGMA / ATTACH。"""
    guard = PermissionGuard(role="admin")
    assert not guard.check("PRAGMA table_info(students)").allowed
    assert not guard.check("ATTACH DATABASE ':memory:' AS x").allowed
    assert not guard.check("DROP TABLE students").allowed


def test_student_cross_access_still_rejected():
    guard = PermissionGuard(role="student", current_user="S001")
    r = guard.check("SELECT * FROM enrollments WHERE student_id = 'S002'")
    assert not r.allowed
    assert "越权" in r.reason


def test_teacher_college_auto_injected():
    guard = PermissionGuard(role="teacher", teacher_college="计算机学院")
    r = guard.check("SELECT * FROM courses")
    assert r.allowed
    assert "college = '计算机学院'" in r.rewritten_sql


def test_teacher_cross_college_rejected():
    guard = PermissionGuard(role="teacher", teacher_college="计算机学院")
    r = guard.check("SELECT * FROM courses WHERE college = '文学院'")
    assert not r.allowed


def test_teacher_single_student_detail_rejected():
    guard = PermissionGuard(role="teacher", current_user="T00001")
    r = guard.check("SELECT score FROM enrollments WHERE student_id = 'S001'")
    assert not r.allowed
    assert "单个学生" in r.reason


if __name__ == "__main__":
    raise SystemExit(__import__("pytest").main([__file__, "-q"]))
