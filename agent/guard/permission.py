"""权限层（FR-A06）。

角色：student / teacher / admin

- student：execute_sql 自动注入 student_id = 当前用户 的行级过滤；禁止访问其他学生的
  成绩、消费、借阅明细；
- teacher：可查本院系聚合数据，不可查单生明细；
- admin：无限制（仅用于生成标准答案，且必须经服务端显式开启，见 serve.py）。

同时拒绝写操作（DELETE/DROP/UPDATE/INSERT/PRAGMA/ATTACH 等）与无 LIMIT 的大查询。

安全修复要点（对应审计 P0/P1）：
  1. 写操作改为**白名单**：仅允许 SELECT / WITH（只读），其余语句一律拒绝，
     覆盖原黑名单遗漏的 PRAGMA / ATTACH；
  2. 学生行级过滤用 **sqlglot 重写谓词**：无论原 SQL 写了什么，都强制 AND
     student_id = current_user，并拒绝 UNION / 子查询，杜绝
     `student_id='S001' OR 1=1`、`UNION SELECT ...` 等越权绕过；
  3. current_user 经白名单校验 + sqlglot 字面量安全转义，杜绝 SQL 注入；
  4. teacher 院系过滤补全（_COLLEGE_RE 真正生效），跨院查询被拒。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

# 含 student_id 的"学生私有"表：student 角色只能查自己的行
STUDENT_PRIVATE_TABLES = {"enrollments", "library_borrow", "card_transactions", "scholarships"}
# 学生主表：可聚合，但禁止查他人个人信息明细
STUDENT_MASTER_TABLE = "students"
# 含 college 列的表（teacher 院系过滤可注入）
COLLEGE_TABLES = {"students", "courses"}

# 全部表（白名单，用于识别 SQL 涉及的表）
ALL_TABLES = {
    "students", "teachers", "courses", "enrollments", "classrooms",
    "course_schedule", "books", "library_borrow", "card_transactions", "scholarships",
}

# 写操作关键字（兜底正则；主路径用 sqlglot 语句类型白名单）
_WRITE_RE = re.compile(
    r"\b(DELETE|UPDATE|INSERT|DROP|TRUNCATE|ALTER|CREATE|REPLACE|GRANT|PRAGMA|ATTACH)\b", re.I
)
_TABLE_RE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+([a-zA-Z_][\w]*)", re.I)
_STUDENT_ID_RE = re.compile(r"\bstudent_id\s*=\s*['\"]?([A-Za-z0-9_]+)['\"]?", re.I)
_COLLEGE_RE = re.compile(r"\bcollege\s*=\s*['\"]([^'\"]+)['\"]", re.I)
# current_user / user_id 白名单：仅允许字母数字下划线，杜绝注入
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass
class PermissionResult:
    allowed: bool
    reason: str = ""
    rewritten_sql: str = ""


def _is_safe_id(value: str) -> bool:
    return bool(_SAFE_ID_RE.match(value or ""))


def _tables_regex(sql: str) -> set[str]:
    return {t for t in _TABLE_RE.findall(sql) if t in ALL_TABLES}


def _tables_sqlglot(sql: str) -> set[str]:
    try:
        parsed = sqlglot.parse_one(sql, read="sqlite")
    except Exception:
        return _tables_regex(sql)
    return {t.name for t in parsed.find_all(exp.Table) if t.name in ALL_TABLES}


def extract_tables(sql: str) -> set[str]:
    try:
        return _tables_sqlglot(sql)
    except Exception:
        return _tables_regex(sql)


def is_write(sql: str) -> bool:
    """写操作白名单：仅允许只读查询（SELECT / WITH / UNION 只读组合）。

    其余语句（DELETE/UPDATE/INSERT/DROP/ALTER/CREATE/PRAGMA/ATTACH 等）一律视为写操作拒绝。
    """
    try:
        parsed = sqlglot.parse_one(sql, read="sqlite")
    except Exception:
        # 解析失败：保守拒绝写操作（正则兜底仍可检出明确写关键字）
        return _WRITE_RE.search(sql) is not None
    # 顶层为 SELECT / WITH(CTE) / UNION(只读组合) 视为只读
    return not isinstance(parsed, (exp.Select, exp.With, exp.Union))


class PermissionGuard:
    """按角色对 SQL 做权限校验与行级过滤。"""

    def __init__(self, role: str = "student", current_user: str = "", teacher_college: str | None = None) -> None:
        self.role = role
        self.current_user = current_user
        self.teacher_college = teacher_college  # 服务端解析，绝不来自客户端 SQL

    def check(self, sql: str) -> PermissionResult:
        sql = (sql or "").strip().rstrip(";")
        if not sql:
            return PermissionResult(False, "空 SQL")

        # 1) 写操作白名单：仅允许只读查询
        if is_write(sql):
            return PermissionResult(False, "拒绝写操作（仅允许 SELECT / WITH 只读查询）")

        # 2) 未知表检测（用原始表名，避免被白名单过滤后漏报）
        raw_tables = {t for t in _TABLE_RE.findall(sql)}
        unknown = raw_tables - ALL_TABLES
        if unknown:
            return PermissionResult(False, f"不存在的表：{', '.join(sorted(unknown))}")

        tables = extract_tables(sql)
        if not tables:
            return PermissionResult(False, "未识别到受支持的表")

        # 3) admin 无限制（仅服务端显式开启，见 serve.py）
        if self.role == "admin":
            return PermissionResult(True, "", sql)

        # 4) student 角色的 current_user 必须合法（防注入 + 服务端身份）
        if self.role == "student" and not _is_safe_id(self.current_user):
            return PermissionResult(False, "非法用户标识")

        # 5) 大查询需 LIMIT
        if not re.search(r"\bLIMIT\b", sql, re.I) and "count" not in sql.lower():
            sql += " LIMIT 100"

        private = tables & STUDENT_PRIVATE_TABLES

        # 6) 学生私有表行级过滤（强制重写谓词，防 OR/UNION 绕过）
        if self.role == "student" and private:
            r = self._apply_student_filter(sql)
            if r is not None:
                return r

        # 7) student 角色查 students 主表：仅允许聚合，不允许查他人个人信息
        if self.role == "student" and STUDENT_MASTER_TABLE in tables:
            if not re.search(r"\b(COUNT|AVG|SUM|MAX|MIN|GROUP)\b", sql, re.I):
                return PermissionResult(False, "学生无权查询他人个人信息明细")

        # 8) teacher 角色：拒绝单生明细 + 本院系过滤
        if self.role == "teacher":
            if private:
                m = _STUDENT_ID_RE.search(sql)
                if m and m.group(1) != self.current_user:
                    return PermissionResult(False, "教师不可查询单个学生的明细")
                # 私有表无 college 列，要求 SQL 已限定本院系（经 JOIN 等），否则拒绝
                if self.teacher_college and not _COLLEGE_RE.search(sql):
                    return PermissionResult(False, "教师查询须限定本院系（college 条件缺失）")
            if self.teacher_college and (STUDENT_MASTER_TABLE in tables or "courses" in tables):
                injected = self._inject_college(sql, self.teacher_college)
                if injected is None:
                    return PermissionResult(False, "教师查询须限定本院系")
                sql = injected

        return PermissionResult(True, "", sql)

    # ------------------------------------------------------------------
    def _apply_student_filter(self, sql: str) -> PermissionResult | None:
        """为学生私有表强制重写行级过滤。返回 None 表示无需额外处理。

        安全策略：
          - 显式写出他人 student_id → 直接拒绝（越权）；
          - 含 UNION / 子查询 → 拒绝（防跨表 / 子查询越权，AND 无法覆盖 UNION 第二段）；
          - 其余情况用 sqlglot 在 WHERE 顶层 AND student_id = current_user，
            即便原 SQL 含 `OR 1=1` 也被约束为仅当前用户行。
        """
        m = _STUDENT_ID_RE.search(sql)
        if m and m.group(1) != self.current_user:
            return PermissionResult(False, f"越权：无权访问其他学生（{m.group(1)}）的数据")
        if self._has_union_or_subquery(sql):
            return PermissionResult(False, "拒绝：检测到 UNION / 子查询，疑似越权绕过")
        try:
            new_sql = self._force_student_filter(sql, self.current_user)
        except Exception:
            return PermissionResult(False, "无法安全重写行级过滤")
        if new_sql is None:
            return PermissionResult(False, "拒绝：无法安全应用行级过滤")
        return PermissionResult(True, "已注入行级过滤", new_sql)

    @staticmethod
    def _has_union_or_subquery(sql: str) -> bool:
        try:
            parsed = sqlglot.parse_one(sql, read="sqlite")
        except Exception:
            return True  # 解析失败保守认为有风险
        return parsed.find(exp.Union) is not None or parsed.find(exp.Subquery) is not None

    @staticmethod
    def _force_student_filter(sql: str, current_user: str) -> str | None:
        """用 sqlglot 在 WHERE 顶层 AND student_id = current_user（安全字面量转义）。"""
        parsed = sqlglot.parse_one(sql, read="sqlite")
        cond = exp.EQ(this=exp.column("student_id"), expression=exp.Literal.string(current_user))
        where = parsed.args.get("where")
        if where is None:
            parsed.set("where", exp.Where(this=cond))
        else:
            # 顶层 AND 强制过滤：原条件必须用括号包裹！
            # 否则 `WHERE 1=1 OR student_id='我'` 会被拼成
            # `WHERE 1=1 OR student_id='我' AND student_id='我'`，
            # 因 AND 优先级高于 OR 而等价于 `TRUE OR (...)` → 恒真 → 越权返回全表。
            # （审计实测：`1=1 OR student_id='S00001'` 曾泄露全部学生行）
            where.set("this", exp.And(this=exp.Paren(this=where.this), expression=cond))
        return parsed.sql(dialect="sqlite")

    def _inject_college(self, sql: str, college: str) -> str | None:
        """teacher 院系过滤：已含本院系则放行；含他院则拒绝；否则注入本院系。"""
        m = _COLLEGE_RE.search(sql)
        if m and m.group(1) != college:
            return None  # 跨院查询拒绝
        if m and m.group(1) == college:
            return sql  # 已限定本院系
        try:
            parsed = sqlglot.parse_one(sql, read="sqlite")
            cond = exp.EQ(this=exp.column("college"), expression=exp.Literal.string(college))
            where = parsed.args.get("where")
            if where is None:
                parsed.set("where", exp.Where(this=cond))
            else:
                # 同 _force_student_filter：原条件必须加括号，避免 OR 优先级导致过滤失效
                where.set("this", exp.And(this=exp.Paren(this=where.this), expression=cond))
            return parsed.sql(dialect="sqlite")
        except Exception:
            return None
