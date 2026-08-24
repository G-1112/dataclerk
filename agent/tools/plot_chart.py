"""plot_chart 工具（FR-A04）：生成图表规格。

可测失效模式：该画图时不画图（如"各学院人数分布"应生成柱状图而非纯文字）。
"""

from __future__ import annotations


def plot_chart(data: list[tuple], chart_type: str = "bar", title: str = "") -> dict:
    """把 (标签, 数值) 列表转成图表规格。"""
    labels = [d[0] for d in data]
    values = [d[1] for d in data]
    return {
        "type": chart_type,
        "title": title,
        "labels": labels,
        "values": values,
    }


def data_to_chart(columns: list[str], rows: list[list], title: str = "") -> dict:
    """把 execute_sql 的结果转成图表（两列：标签 + 数值）。"""
    if not rows or len(columns) < 2:
        return {"type": "table", "title": title, "columns": columns, "rows": rows}
    labels = [r[0] for r in rows]
    values = [r[1] for r in rows]
    return {"type": "bar", "title": title, "labels": labels, "values": values}
