"""search_values 工具（FR-A04）：枚举某列的取值。

可测失效模式：不检索 → 硬猜值 → 空结果（如"计算机学院"实际存的是"计算机科学与技术学院"）。
"""

from __future__ import annotations

from ..db import connect, get_db_path


def search_values(column: str, keyword: str = "", limit: int = 10) -> list[str]:
    """在 schema 中查找包含 keyword 的列取值（用于确认枚举值的实际存储形式）。"""
    table = _column_to_table(column)
    if table is None:
        return []
    conn = connect()
    try:
        if keyword:
            rows = conn.execute(
                f"SELECT DISTINCT {column} FROM {table} WHERE {column} LIKE ? LIMIT ?",
                (f"%{keyword}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(f"SELECT DISTINCT {column} FROM {table} LIMIT ?", (limit,)).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def _column_to_table(column: str) -> str | None:
    """根据列名定位表。"""
    from ..db import SCHEMA

    if column == "student_id":
        return "students"
    for table, (_, cols) in SCHEMA.items():
        if any(c == column for c, _ in cols):
            return table
    return None
