"""权限层测试（FR-A06，安全测试的天然素材）。"""

from agent.guard.permission import PermissionGuard


def test_reject_write_operations():
    guard = PermissionGuard(role="admin")
    for sql in ["DELETE FROM enrollments", "DROP TABLE students",
                "UPDATE students SET name='x'", "INSERT INTO students VALUES (1)"]:
        r = guard.check(sql)
        assert not r.allowed
        assert "拒绝" in r.reason or "写操作" in r.reason


def test_student_own_data_allowed():
    guard = PermissionGuard(role="student", current_user="S00001")
    r = guard.check("SELECT * FROM enrollments WHERE student_id = 'S00001'")
    assert r.allowed


def test_student_cross_access_rejected():
    guard = PermissionGuard(role="student", current_user="S00001")
    r = guard.check("SELECT * FROM enrollments WHERE student_id = 'S00002'")
    assert not r.allowed
    assert "越权" in r.reason


def test_student_auto_inject_row_filter():
    guard = PermissionGuard(role="student", current_user="S00001")
    r = guard.check("SELECT score FROM enrollments")
    assert r.allowed
    assert "student_id = 'S00001'" in r.rewritten_sql


def test_student_cannot_query_others_profile():
    guard = PermissionGuard(role="student", current_user="S00001")
    r = guard.check("SELECT name, gender FROM students")
    assert not r.allowed  # 禁止查他人个人信息明细


def test_student_can_aggregate():
    guard = PermissionGuard(role="student", current_user="S00001")
    r = guard.check("SELECT COUNT(*) FROM students")
    assert r.allowed


def test_unknown_table_rejected():
    guard = PermissionGuard(role="admin")
    r = guard.check("SELECT * FROM nonexistent_table")
    assert not r.allowed
    assert "不存在的表" in r.reason


def test_teacher_cannot_query_single_student_detail():
    guard = PermissionGuard(role="teacher")
    r = guard.check("SELECT score FROM enrollments WHERE student_id = 'S00001'")
    assert not r.allowed
    assert "单个学生" in r.reason


def test_large_query_gets_limit():
    guard = PermissionGuard(role="teacher")
    r = guard.check("SELECT * FROM courses")
    assert r.allowed
    assert "LIMIT 100" in r.rewritten_sql
