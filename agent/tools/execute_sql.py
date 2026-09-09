"""execute_sql 工具（FR-A04）：只读执行，带权限过滤与行数上限。

可测失效模式：SQL 错误 / 越权 / 危险语句 / 该画图不画图。
"""

from __future__ import annotations

from ..db import connect
from ..guard.permission import PermissionGuard


def _resolve_teacher_college(current_user: str) -> str | None:
    """服务端解析教师所属院系（绝不来自客户端 SQL）。无 DB 时返回 None。"""
    try:
        conn = connect()
        row = conn.execute(
            "SELECT college FROM teachers WHERE teacher_id = ?", (current_user,)
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def execute_sql(sql: str, role: str = "admin", current_user: str = "") -> dict:
    """执行只读 SQL，返回 {ok, columns, rows, error, rewritten}。"""
    teacher_college = _resolve_teacher_college(current_user) if role == "teacher" else None
    guard = PermissionGuard(role=role, current_user=current_user, teacher_college=teacher_college)
    result = guard.check(sql)
    if not result.allowed:
        return {"ok": False, "error": result.reason, "columns": [], "rows": []}

    conn = connect()
    try:
        cur = conn.execute(result.rewritten_sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchall()]
        return {"ok": True, "columns": columns, "rows": rows, "rewritten": result.rewritten_sql}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"SQL 执行错误：{e}", "columns": [], "rows": []}
    finally:
        conn.close()
