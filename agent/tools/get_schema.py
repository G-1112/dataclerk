"""get_schema 工具（FR-A04）：返回表结构与字段说明。

可测失效模式：不查 schema → 表名瞎猜 → SQL 报错。
"""

from __future__ import annotations

from ..db import all_tables, describe_table


def get_schema(tables: list[str] | None = None) -> str:
    """返回指定表（或全部表）的结构说明。"""
    targets = tables if tables else all_tables()
    lines = []
    for t in targets:
        lines.append(describe_table(t))
    return "\n".join(lines)
