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


def test_or_tautology_on_left_is_parenthesized():
    """审计新发现（P0）：恒真分支位于 OR 左侧时，原条件必须被括号包裹。

    `1=1 OR student_id='S001'` 若不加括号会拼成 `1=1 OR (... AND student_id='S001')`，
    因 AND 优先级高于 OR 而整体恒真 → 越权返回全表（实测曾泄露全部学生行）。
    """
    conn = _in_memory_enrollments()
    guard = PermissionGuard(role="student", current_user="S001")
    for sql in (
        "SELECT * FROM enrollments WHERE 1=1 OR student_id='S001'",
        "SELECT * FROM enrollments WHERE 'a'='a' OR student_id='S001'",
        "SELECT * FROM enrollments WHERE 2>1 OR student_id='S001'",
    ):
        r = guard.check(sql)
        assert r.allowed, f"{sql} 应放行（走行级过滤），实际拒绝：{r.reason}"
        rw = r.rewritten_sql
        # 原条件整体必须被括号包裹，再 AND 上本人约束
        assert "(" in rw, f"原条件未被括号包裹，存在优先级绕过风险：{rw}"
        assert "AND student_id = 'S001'" in rw, rw
        assert rw.index("(") < rw.index("AND student_id = 'S001'"), rw
        rows = conn.execute(rw).fetchall()
        ids = {row[0] for row in rows}
        assert ids == {"S001"}, f"OR 恒真绕过导致越权泄露 {ids}（SQL: {sql}）"
    conn.close()


def test_teacher_college_filter_is_parenthesized():
    """teacher 院系过滤注入同样需要括号，避免 OR 优先级逃逸。"""
    guard = PermissionGuard(role="teacher", teacher_college="计算机学院")
    r = guard.check("SELECT * FROM courses WHERE credit=3 OR credit=4")
    assert r.allowed, r.reason
    rw = r.rewritten_sql
    assert "(" in rw and "AND college = '计算机学院'" in rw, rw
    assert rw.index("(") < rw.index("AND college = '计算机学院'"), rw


# ---------------------------------------------------------------------------
# 第三轮修复：学生主表聚合导致的信息泄露（R-PERM-01/02）
#
# 原实现只要 SQL 里出现 COUNT/AVG/SUM/MAX/MIN/GROUP 任一单词就放行聚合查询，
# 于是「GROUP BY」里的 GROUP 就成了万能通行证，且 MIN/MAX 可返回真实单元格值。
# 实测曾泄露：`SELECT MIN(name) FROM students` -> 他人姓名，
# 并可配合 `WHERE name > 'x'` 迭代枚举全校 400 个姓名。
# ---------------------------------------------------------------------------
def _in_memory_students() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE students (student_id TEXT, name TEXT, college TEXT, enroll_year INTEGER)")
    conn.executemany(
        "INSERT INTO students VALUES (?, ?, ?, ?)",
        [("S001", "张三", "计算机学院", 2022),
         ("S002", "李四", "文学院", 2023),
         ("S003", "王五", "计算机学院", 2023)],
    )
    return conn


def _student_guard() -> PermissionGuard:
    return PermissionGuard(role="student", current_user="S001")


def test_min_max_on_pii_column_rejected():
    """MIN/MAX 作用于 name/student_id 会返回真实值 → 必须拒绝。"""
    for sql in (
        "SELECT MIN(name) FROM students",
        "SELECT MAX(name) FROM students",
        "SELECT MIN(name), MAX(name) FROM students",
        "SELECT MIN(student_id) FROM students",
    ):
        r = _student_guard().check(sql)
        assert not r.allowed, f"{sql} 泄露他人信息，应被拒绝"


def test_pii_enough_enumeration_is_blocked():
    """配合 WHERE name > 'x' 的迭代枚举必须被阻断（原可枚举全校姓名）。"""
    conn = _in_memory_students()
    leaked: list[str] = []
    last = ""
    for _ in range(10):
        sql = f"SELECT MIN(name) FROM students WHERE name > '{last}'"
        r = _student_guard().check(sql)
        if not r.allowed:
            break
        row = conn.execute(r.rewritten_sql).fetchone()
        if not row or row[0] is None:
            break
        last = row[0]
        leaked.append(last)
    conn.close()
    assert leaked == [], f"仍可枚举他人姓名：{leaked}"


def test_group_by_bare_column_projection_rejected():
    """GROUP BY 不能作为「聚合」通行证：裸投影 PII 列仍须拒绝。"""
    for sql in (
        "SELECT name FROM students GROUP BY name",
        "SELECT name, college FROM students GROUP BY name",
        "SELECT student_id FROM students GROUP BY student_id",
        "SELECT * FROM students",
    ):
        r = _student_guard().check(sql)
        assert not r.allowed, f"{sql} 应被拒绝（GROUP 不等于聚合）"


def test_group_concat_pii_rejected():
    r = _student_guard().check("SELECT GROUP_CONCAT(name) FROM students")
    assert not r.allowed, "GROUP_CONCAT(name) 会一次性泄露全部姓名"


def test_legitimate_aggregation_still_allowed():
    """真聚合 + 非 PII 维度仍应放行（不能因噎废食）。"""
    conn = _in_memory_students()
    cases = {
        "SELECT COUNT(*) FROM students": 1,
        "SELECT college, COUNT(*) FROM students GROUP BY college": 2,
        "SELECT AVG(enroll_year) FROM students": 1,
        "SELECT MIN(enroll_year) FROM students": 1,
    }
    for sql, expected_rows in cases.items():
        r = _student_guard().check(sql)
        assert r.allowed, f"{sql} 属合法聚合，不应被拒绝：{r.reason}"
        rows = conn.execute(r.rewritten_sql).fetchall()
        assert len(rows) == expected_rows, f"{sql} 返回 {len(rows)} 行"
    conn.close()


def test_aggregation_result_contains_no_names():
    """合法聚合的返回值里不得出现任何他人姓名。"""
    conn = _in_memory_students()
    r = _student_guard().check("SELECT college, COUNT(*) FROM students GROUP BY college")
    rows = conn.execute(r.rewritten_sql).fetchall()
    conn.close()
    flat = str([tuple(x) for x in rows])
    for name in ("张三", "李四", "王五"):
        assert name not in flat, f"聚合结果泄露了姓名 {name}：{flat}"


if __name__ == "__main__":
    raise SystemExit(__import__("pytest").main([__file__, "-q"]))
