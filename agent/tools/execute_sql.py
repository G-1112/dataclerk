"""execute_sql 工具（FR-A04）：只读执行，带权限过滤、行数上限与执行超时。

可测失效模式：SQL 错误 / 越权 / 危险语句 / 该画图不画图。

性能与健壮性（第二轮）：
- 行数上限 MAX_ROWS：用 fetchmany 而非 fetchall，避免 SELECT * 全表物化；
  命中上限时置 truncated=True，供上层提示"结果被截断"。
- 执行超时 SQL_TIMEOUT_MS：通过 conn.set_progress_handler 在超过预算后中断，
  防止笛卡尔积 / 递归 CTE 等病态查询长期占用 worker。
"""

from __future__ import annotations

import time

from ..db import MAX_ROWS, connect
from ..guard.permission import PermissionGuard

# 单条 SQL 最长执行时间（毫秒），命中即中断返回错误
SQL_TIMEOUT_MS = 3000


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
    """执行只读 SQL，返回 {ok, columns, rows, error, rewritten, truncated}。"""
    if role == "teacher":
        teacher_college = _resolve_teacher_college(current_user)
        # 第十二轮修复（实证 P0）：原实现在院系解析失败（工号不存在 / DB 异常）
        # 时以 teacher_college=None 继续构造 guard，院系过滤条件被整体跳过
        # （fail-open），配合伪造工号即可全校漫游私有表。失败必须关闭（fail-close）。
        if teacher_college is None:
            return {
                "ok": False,
                "error": "无法确认教师所属院系，已按最小权限拒绝查询",
                "columns": [],
                "rows": [],
                "truncated": False,
            }
    else:
        teacher_college = None
    guard = PermissionGuard(role=role, current_user=current_user, teacher_college=teacher_college)
    result = guard.check(sql)
    if not result.allowed:
        return {"ok": False, "error": result.reason, "columns": [], "rows": [], "truncated": False}

    conn = connect()
    deadline = time.monotonic() + SQL_TIMEOUT_MS / 1000.0

    def _abort_long_query() -> int:
        """progress handler 每 ~N 个 VM 指令回调一次；超预算返回非 0 中断查询。"""
        return 1 if time.monotonic() > deadline else 0

    try:
        conn.set_progress_handler(_abort_long_query, 10000)
        cur = conn.execute(result.rewritten_sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchmany(MAX_ROWS + 1)]
        truncated = len(rows) > MAX_ROWS
        if truncated:
            rows = rows[:MAX_ROWS]
        return {
            "ok": True,
            "columns": columns,
            "rows": rows,
            "rewritten": result.rewritten_sql,
            "truncated": truncated,
        }
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "interrupted" in msg.lower():
            msg = f"查询超时（>{SQL_TIMEOUT_MS}ms），请缩小查询范围"
        return {"ok": False, "error": f"SQL 执行错误：{msg}", "columns": [], "rows": [], "truncated": False}
    finally:
        try:
            conn.set_progress_handler(None, 0)
        except Exception:
            pass
        conn.close()
