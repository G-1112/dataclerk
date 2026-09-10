"""数据库连接与 schema 元数据。

schema 元数据是 get_schema 工具与标准答案生成共用的唯一事实来源。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# 单条查询最多返回行数（防止 SELECT * 全表物化导致内存/配额耗尽）
MAX_ROWS = int(os.getenv("DATACLERK_MAX_ROWS", "5000"))

DEFAULT_DB = Path(os.getenv("DATACLERK_DB", str(Path(__file__).resolve().parent.parent / "data" / "campus.db")))

# 当前生效的 DB 路径（可被 CampusAgent / 测试套件通过 set_db_path 覆盖）
_CURRENT_DB: Path = DEFAULT_DB


def set_db_path(path: str | Path) -> None:
    """覆盖全局 DB 路径（测试与 Agent 初始化时调用）。"""
    global _CURRENT_DB
    _CURRENT_DB = Path(path)

# 表 → (描述, [(列名, 中文说明), ...])
SCHEMA: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "students": (
        "学生基本信息表",
        [("student_id", "学号"), ("name", "姓名"), ("gender", "性别"),
         ("grade", "年级"), ("college", "学院"), ("major", "专业"),
         ("enroll_year", "入学年份"), ("class_id", "班级")],
    ),
    "teachers": (
        "教师表",
        [("teacher_id", "教师工号"), ("name", "姓名"), ("college", "所属学院"), ("title", "职称")],
    ),
    "courses": (
        "课程表",
        [("course_id", "课程号"), ("course_name", "课程名"), ("credit", "学分"),
         ("college", "开课学院"), ("teacher_id", "授课教师"), ("semester", "学期"), ("capacity", "容量")],
    ),
    "enrollments": (
        "选课成绩表",
        [("id", "记录号"), ("student_id", "学号"), ("course_id", "课程号"),
         ("semester", "学期"), ("score", "成绩(可空)"), ("status", "状态(enrolled/withdrawn/completed)")],
    ),
    "classrooms": (
        "教室表",
        [("room_id", "教室号"), ("building", "教学楼"), ("capacity", "容量"), ("type", "类型")],
    ),
    "course_schedule": (
        "排课表",
        [("id", "记录号"), ("course_id", "课程号"), ("room_id", "教室号"),
         ("semester", "学期"), ("day_of_week", "星期几"), ("start_slot", "开始节次"), ("end_slot", "结束节次")],
    ),
    "books": (
        "图书表",
        [("book_id", "书号"), ("title", "书名"), ("author", "作者"), ("category", "分类"), ("publisher", "出版社")],
    ),
    "library_borrow": (
        "图书借阅表",
        [("id", "记录号"), ("student_id", "学号"), ("book_id", "书号"),
         ("borrow_date", "借阅日期"), ("due_date", "应还日期"), ("return_date", "归还日期(可空=未还)")],
    ),
    "card_transactions": (
        "校园卡消费表",
        [("id", "记录号"), ("student_id", "学号"), ("amount", "金额"),
         ("merchant", "商户"), ("category", "类别"), ("trans_time", "消费时间")],
    ),
    "scholarships": (
        "奖学金表",
        [("id", "记录号"), ("student_id", "学号"), ("name", "奖项名"),
         ("amount", "金额"), ("academic_year", "学年")],
    ),
}


def get_db_path() -> Path:
    return Path(_CURRENT_DB)


def connect(db_path: str | Path | None = None, *, query_only: bool = True) -> sqlite3.Connection:
    """打开 SQLite 连接。

    - query_only=True（默认）：设置 PRAGMA query_only，从引擎层阻断任何写操作，
      即使权限层被绕过也无法 INSERT/UPDATE/DELETE/DDL（纵深防御）。
    - busy_timeout：并发访问时最多等待 5s 而非立即抛 "database is locked"（性能）。
    """
    path = Path(db_path) if db_path else get_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        if query_only:
            conn.execute("PRAGMA query_only = ON")
    except sqlite3.Error:
        # 极老版本 SQLite 不支持 query_only 时退化为仅 busy_timeout
        pass
    return conn


def describe_table(table: str) -> str:
    """生成单表结构说明（get_schema 工具用）。"""
    if table not in SCHEMA:
        return f"未知表：{table}"
    desc, cols = SCHEMA[table]
    col_str = ", ".join(f"{c}({zh})" for c, zh in cols)
    return f"{table} — {desc}：{col_str}"


def all_tables() -> list[str]:
    return list(SCHEMA.keys())
