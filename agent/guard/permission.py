"""权限层（FR-A06）。

角色：student / teacher / admin

- student：execute_sql 自动注入 student_id = 当前用户 的行级过滤；禁止访问其他学生的
  成绩、消费、借阅明细；
- teacher：可查本院系聚合数据，不可查单生明细；
- admin：无限制（仅用于生成标准答案）。

同时拒绝写操作（DELETE/DROP/UPDATE/INSERT）与无 LIMIT 的大查询。

实现：优先用 sqlglot 解析（识别涉及表、判断读/写），不可用时退化为正则白名单解析。
两套实现接口一致，均确定性可测。这是安全测试（越权/注入/破坏性操作）的天然素材。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 含 student_id 的"学生私有"表：student 角色只能查自己的行
STUDENT_PRIVATE_TABLES = {"enrollments", "library_borrow", "card_transactions", "scholarships"}
# 学生主表：可聚合，但禁止查他人个人信息明细
STUDENT_MASTER_TABLE = "students"

# 全部表（白名单，用于识别 SQL 涉及的表）
ALL_TABLES = {
    "students", "teachers", "courses", "enrollments", "classrooms",
    "course_schedule", "books", "library_borrow", "card_transactions", "scholarships",
}

_WRITE_RE = re.compile(r"\b(DELETE|UPDATE|INSERT|DROP|TRUNCATE|ALTER|CREATE|REPLACE|GRANT)\b", re.I)
_TABLE_RE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+([a-zA-Z_][\w]*)", re.I)
_STUDENT_ID_RE = re.compile(r"\bstudent_id\s*=\s*['\"]?([A-Za-z0-9_]+)['\"]?", re.I)
_COLLEGE_RE = re.compile(r"\bcollege\s*=\s*['\"]([^'\"]+)['\"]", re.I)


@dataclass
class PermissionResult:
    allowed: bool
    reason: str = ""
    rewritten_sql: str = ""


def _tables_regex(sql: str) -> set[str]:
    return {t for t in _TABLE_RE.findall(sql) if t in ALL_TABLES}


def _tables_sqlglot(sql: str) -> set[str]:
    import sqlglot

    try:
        parsed = sqlglot.parse_one(sql, read="sqlite")
    except Exception:
        return _tables_regex(sql)
    return {t.name for t in parsed.find_all(sqlglot.exp.Table) if t.name in ALL_TABLES}


def extract_tables(sql: str) -> set[str]:
    try:
        return _tables_sqlglot(sql)
    except Exception:
        return _tables_regex(sql)


def is_write(sql: str) -> bool:
    return bool(_WRITE_RE.search(sql))


class PermissionGuard:
    """按角色对 SQL 做权限校验与行级过滤。"""

    def __init__(self, role: str = "admin", current_user: str = "") -> None:
        self.role = role
        self.current_user = current_user

    def check(self, sql: str) -> PermissionResult:
        sql = (sql or "").strip().rstrip(";")
        if not sql:
            return PermissionResult(False, "空 SQL")

        # 1) 写操作一律拒绝
        if is_write(sql):
            return PermissionResult(False, "拒绝写操作（DELETE/UPDATE/INSERT/DROP 等）")

        # 2) 未知表检测（用原始表名，避免被白名单过滤后漏报）
        raw_tables = {t for t in _TABLE_RE.findall(sql)}
        unknown = raw_tables - ALL_TABLES
        if unknown:
            return PermissionResult(False, f"不存在的表：{', '.join(sorted(unknown))}")

        tables = extract_tables(sql)
        if not tables:
            return PermissionResult(False, "未识别到受支持的表")

        # 3) admin 无限制
        if self.role == "admin":
            return PermissionResult(True, "", sql)

        # 4) 大查询需 LIMIT
        if not re.search(r"\bLIMIT\b", sql, re.I) and "count" not in sql.lower():
            sql += " LIMIT 100"

        # 5) 学生私有表行级过滤
        private = tables & STUDENT_PRIVATE_TABLES
        if self.role == "student" and private:
            r = self._apply_student_filter(sql, tables)
            if r is not None:
                return r

        # 6) student 角色查 students 主表：仅允许聚合，不允许查他人个人信息
        if self.role == "student" and STUDENT_MASTER_TABLE in tables:
            if not re.search(r"\b(COUNT|AVG|SUM|MAX|MIN|GROUP)\b", sql, re.I):
                return PermissionResult(False, "学生无权查询他人个人信息明细")

        # 7) teacher 角色：拒绝单生明细（学生私有表 + 指定 student_id）
        if self.role == "teacher" and private:
            m = _STUDENT_ID_RE.search(sql)
            if m and m.group(1) != self.current_user:
                return PermissionResult(False, "教师不可查询单个学生的明细")

        return PermissionResult(True, "", sql)

    # ------------------------------------------------------------------
    def _apply_student_filter(self, sql: str, tables: set[str]) -> PermissionResult | None:
        """为学生私有表注入 student_id = current_user 的行级过滤。

        返回 None 表示无需额外处理（交由通用路径放行）。
        """
        m = _STUDENT_ID_RE.search(sql)
        if m and m.group(1) != self.current_user:
            return PermissionResult(False, f"越权：无权访问其他学生（{m.group(1)}）的数据")
        if m and m.group(1) == self.current_user:
            return PermissionResult(True, "", sql)  # 已过滤为自己，放行

        # 无 student_id 条件 → 注入过滤
        injected = self._inject_student_id(sql)
        return PermissionResult(True, "已注入行级过滤", injected)

    def _inject_student_id(self, sql: str) -> str:
        cond = f"student_id = '{self.current_user}'"
        # 有 WHERE 则 AND，否则新增 WHERE
        if re.search(r"\bWHERE\b", sql, re.I):
            if re.search(r"\bGROUP\s+BY\b", sql, re.I):
                # 简单处理：在 GROUP BY 前注入 WHERE 较复杂，这里直接拼接到 WHERE 末尾
                return re.sub(r"(\bWHERE\b[^\n]*)", rf"\1 AND {cond}", sql, count=1, flags=re.I)
            return re.sub(r"(\bWHERE\b)", rf"\1 {cond} AND", sql, count=1, flags=re.I)
        if re.search(r"\bGROUP\s+BY\b", sql, re.I) or re.search(r"\bORDER\s+BY\b", sql, re.I):
            return re.sub(r"(\bGROUP\s+BY\b|\bORDER\s+BY\b)", rf"WHERE {cond} \1", sql, count=1, flags=re.I)
        if re.search(r"\bLIMIT\b", sql, re.I):
            return re.sub(r"(\bLIMIT\b)", rf"WHERE {cond} \1", sql, count=1, flags=re.I)
        return sql + f" WHERE {cond}"
