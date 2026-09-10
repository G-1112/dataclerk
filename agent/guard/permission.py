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
# 学号形态字面量（S + 至少 3 位数字），用于捕获 IN (...) / 比较等非 `=` 形式的他人引用。
# 数据中仅 student_id 使用 S 前缀（teacher=T / course=C / room=R / book=B），故无误伤。
_STUDENT_ID_LITERAL_RE = re.compile(r"\bS\d{3,}\b", re.I)
_COLLEGE_RE = re.compile(r"\bcollege\s*=\s*['\"]([^'\"]+)['\"]", re.I)
# current_user / user_id 白名单：仅允许字母数字下划线，杜绝注入
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")

# students 主表中的个人信息列：任何直接投影都会泄露他人隐私
_PII_COLUMNS = {"name", "student_id"}
# 真聚合函数（可安全返回统计量）
_AGG_TYPES = ("Count", "Sum", "Avg", "Min", "Max")
# 会返回「某个真实单元格值」的函数：
# 作用于文本型 PII 列时等价于直接读出他人姓名（可用 WHERE 迭代枚举）
_VALUE_LEAK_TYPES = ("Min", "Max", "GroupConcat")


@dataclass
class PermissionResult:
    allowed: bool
    reason: str = ""
    rewritten_sql: str = ""


def _is_safe_id(value: str) -> bool:
    return bool(_SAFE_ID_RE.match(value or ""))


def _parse_sql(sql: str) -> exp.Expression | None:
    """解析 SQL；失败返回 None（调用方走正则兜底或保守拒绝）。"""
    try:
        return sqlglot.parse_one(sql, read="sqlite")
    except Exception:
        return None


def _as_parsed(sql_or_tree: str | exp.Expression | None) -> exp.Expression | None:
    """兼容入参为原始 SQL 或已解析的 AST，避免重复解析（性能）。"""
    if isinstance(sql_or_tree, exp.Expression):
        return sql_or_tree
    if isinstance(sql_or_tree, str):
        return _parse_sql(sql_or_tree)
    return None


def _tables_regex(sql: str) -> set[str]:
    return {t for t in _TABLE_RE.findall(sql) if t in ALL_TABLES}


def _tables_sqlglot(sql: str | exp.Expression) -> set[str]:
    parsed = _as_parsed(sql)
    if parsed is None:
        return _tables_regex(sql if isinstance(sql, str) else "")
    return {t.name for t in parsed.find_all(exp.Table) if t.name in ALL_TABLES}


def extract_tables(sql: str | exp.Expression) -> set[str]:
    try:
        return _tables_sqlglot(sql)
    except Exception:
        return _tables_regex(sql if isinstance(sql, str) else "")


def is_write(sql: str | exp.Expression) -> bool:
    """写操作白名单：仅允许只读查询（SELECT / WITH / UNION 只读组合）。

    其余语句（DELETE/UPDATE/INSERT/DROP/ALTER/CREATE/PRAGMA/ATTACH 等）一律视为写操作拒绝。
    入参可为原始 SQL 或已解析 AST（同一查询在 check() 内只解析一次）。
    """
    parsed = _as_parsed(sql)
    if parsed is None:
        # 解析失败：保守拒绝写操作（正则兜底仍可检出明确写关键字）
        raw = sql if isinstance(sql, str) else ""
        return _WRITE_RE.search(raw) is not None
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

        # 性能（第三轮）：整条 SQL 只解析一次，后续各校验复用同一 AST。
        # 原实现 is_write / extract_tables / _has_union_or_subquery / _force_student_filter
        # 各自 parse_one，单次校验要解析 4–5 次（实测占 execute_sql 总耗时约 90%）。
        parsed = _parse_sql(sql)

        # 1) 写操作白名单：仅允许只读查询
        if is_write(parsed if parsed is not None else sql):
            return PermissionResult(False, "拒绝写操作（仅允许 SELECT / WITH 只读查询）")

        # 2) 未知表检测（用原始表名，避免被白名单过滤后漏报）
        raw_tables = {t for t in _TABLE_RE.findall(sql)}
        unknown = raw_tables - ALL_TABLES
        if unknown:
            return PermissionResult(False, f"不存在的表：{', '.join(sorted(unknown))}")

        tables = extract_tables(parsed if parsed is not None else sql)
        if not tables:
            return PermissionResult(False, "未识别到受支持的表")

        # 3) admin 无限制（仅服务端显式开启，见 serve.py）
        if self.role == "admin":
            return PermissionResult(True, "", sql)

        # 4) student 角色的 current_user 必须合法（防注入 + 服务端身份）
        if self.role == "student" and not _is_safe_id(self.current_user):
            return PermissionResult(False, "非法用户标识")

        # 5) 大查询需 LIMIT
        #    性能：直接把 LIMIT 写进 AST 而不是拼接字符串，避免因文本变更
        #    导致已解析的 AST 失效、下游不得不重新解析。
        if not re.search(r"\bLIMIT\b", sql, re.I) and "count" not in sql.lower():
            if parsed is not None:
                parsed.set("limit", exp.Limit(expression=exp.Literal.number(100)))
                sql = parsed.sql(dialect="sqlite")
            else:
                sql += " LIMIT 100"

        private = tables & STUDENT_PRIVATE_TABLES

        # 6) 学生私有表行级过滤（强制重写谓词，防 OR/UNION 绕过）
        if self.role == "student" and private:
            r = self._apply_student_filter(sql, parsed)
            if r is not None:
                return r

        # 7) student 角色查 students 主表：仅允许**真聚合**，且不得暴露个人信息列
        #    （第三轮修复 R-PERM-01/02：原实现只要 SQL 里出现 COUNT/AVG/SUM/MAX/MIN/GROUP
        #     任一单词就放行，导致
        #       `SELECT name FROM students GROUP BY name`（GROUP 命中）泄露全部姓名；
        #       `SELECT MIN(name) FROM students` 可配合 WHERE name > 'x' 迭代枚举全校姓名。）
        if self.role == "student" and STUDENT_MASTER_TABLE in tables:
            ok, why = self._student_master_allowed(parsed if parsed is not None else sql)
            if not ok:
                return PermissionResult(False, why)

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
    @staticmethod
    def _node_types(names: tuple[str, ...]) -> tuple[type[exp.Expression], ...]:
        """把类名元组解析为 sqlglot 表达式类（缺类时跳过，兼容版本差异）。"""
        return tuple(t for t in (getattr(exp, n, None) for n in names) if isinstance(t, type))

    def _student_master_allowed(self, sql: str | exp.Expression) -> tuple[bool, str]:
        """学生查 students 主表：必须使用真聚合，且不得暴露个人信息列。

        返回 (是否允许, 拒绝原因)。入参可为原始 SQL 或已解析 AST。
        """
        parsed = _as_parsed(sql)
        if parsed is None:
            return False, "无法解析查询，拒绝访问学生主表"

        select = parsed if isinstance(parsed, exp.Select) else parsed.find(exp.Select)
        if select is None:
            return False, "学生无权查询他人个人信息明细"

        agg_types = self._node_types(_AGG_TYPES)
        leak_types = self._node_types(_VALUE_LEAK_TYPES)

        has_agg = False
        for projection in (select.expressions or []):
            # 该投影里是否含真聚合函数
            is_agg = bool(agg_types) and any(
                projection.find(t) is not None for t in agg_types
            )
            if is_agg:
                has_agg = True
                # MIN/MAX/GROUP_CONCAT 作用在 PII 文本列上会返回真实值 → 拒绝
                for t in leak_types:
                    for fn in projection.find_all(t):
                        for col in fn.find_all(exp.Column):
                            if col.name.lower() in _PII_COLUMNS:
                                return False, (
                                    f"越权：聚合函数不可用于个人信息列（{col.name}），"
                                    "该写法可泄露他人信息"
                                )
            else:
                # 非聚合投影中直接出现 PII 列 → 泄露
                for col in projection.find_all(exp.Column):
                    if col.name.lower() in _PII_COLUMNS:
                        return False, "学生无权查询他人个人信息明细"

        if not has_agg:
            return False, "学生查询学生主表须使用聚合函数（COUNT/AVG/SUM 等）"
        return True, ""

    # ------------------------------------------------------------------
    def _apply_student_filter(
        self, sql: str, parsed: exp.Expression | None = None
    ) -> PermissionResult | None:
        """为学生私有表强制重写行级过滤。返回 None 表示无需额外处理。

        安全策略：
          - 显式写出他人 student_id → 直接拒绝（越权）；
          - 含 UNION / 子查询 → 拒绝（防跨表 / 子查询越权，AND 无法覆盖 UNION 第二段）；
          - 其余情况用 sqlglot 在 WHERE 顶层 AND student_id = current_user，
            即便原 SQL 含 `OR 1=1` 也被约束为仅当前用户行。

        第三轮加固（R-PERM-03）：原实现用 search() 只看**第一个** student_id 字面量，
        于是 `student_id='我' OR student_id='他人'` 会因首个匹配恰好是自己而被放行
        （虽然行级过滤仍把结果约束为本人，无真实泄露，但显式引用他人应当直接拒绝）。
        改为 findall()：只要出现任何一个非本人 ID 即拒绝。

        性能：可传入已解析 AST，避免重复解析。
        """
        refs = _STUDENT_ID_RE.findall(sql)
        others = [x for x in refs if x != self.current_user]
        # 补充：`student_id IN ('我','他人')` 等非 `=` 形式不会被上面的正则捕获，
        # 改为扫描全部学号形态字面量（S + 5 位数字），出现他人即拒绝。
        if not others:
            others = [x for x in _STUDENT_ID_LITERAL_RE.findall(sql) if x != self.current_user]
        if others:
            return PermissionResult(False, f"越权：无权访问其他学生（{others[0]}）的数据")
        if self._has_union_or_subquery(parsed if parsed is not None else sql):
            return PermissionResult(False, "拒绝：检测到 UNION / 子查询，疑似越权绕过")
        try:
            new_sql = self._force_student_filter(sql, self.current_user, parsed)
        except Exception:
            return PermissionResult(False, "无法安全重写行级过滤")
        if new_sql is None:
            return PermissionResult(False, "拒绝：无法安全应用行级过滤")
        return PermissionResult(True, "已注入行级过滤", new_sql)

    @staticmethod
    def _has_union_or_subquery(sql: str | exp.Expression) -> bool:
        parsed = _as_parsed(sql)
        if parsed is None:
            return True  # 解析失败保守认为有风险
        return parsed.find(exp.Union) is not None or parsed.find(exp.Subquery) is not None

    @staticmethod
    def _force_student_filter(
        sql: str, current_user: str, parsed: exp.Expression | None = None
    ) -> str | None:
        """用 sqlglot 在 WHERE 顶层 AND student_id = current_user（安全字面量转义）。"""
        # 需要改写 AST，必须先复制，避免污染 check() 中复用的 parse 结果
        tree = (parsed.copy() if parsed is not None else sqlglot.parse_one(sql, read="sqlite"))
        cond = exp.EQ(this=exp.column("student_id"), expression=exp.Literal.string(current_user))
        where = tree.args.get("where")
        if where is None:
            tree.set("where", exp.Where(this=cond))
        else:
            # 顶层 AND 强制过滤：原条件必须用括号包裹！
            # 否则 `WHERE 1=1 OR student_id='我'` 会被拼成
            # `WHERE 1=1 OR student_id='我' AND student_id='我'`，
            # 因 AND 优先级高于 OR 而等价于 `TRUE OR (...)` → 恒真 → 越权返回全表。
            # （审计实测：`1=1 OR student_id='S00001'` 曾泄露全部学生行）
            where = where.copy()
            where.set("this", exp.And(this=exp.Paren(this=where.this), expression=cond))
            tree.set("where", where)
        return tree.sql(dialect="sqlite")

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
