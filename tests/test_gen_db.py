"""合成数据库生成测试（FR-A01：确定性 + 边界埋入）。"""

import sqlite3

from data.gen_db import CampusDB


def _build_small():
    db = CampusDB(seed=42)
    db.build({"students": 100, "courses": 50, "enrollments": 500,
              "library_borrow": 200, "card_transactions": 300,
              "course_schedule": 100, "teachers": 20, "books": 100,
              "classrooms": 10, "scholarships": 50})
    return db


def test_deterministic():
    a = _build_small()
    b = _build_small()
    assert a.students == b.students
    assert a.enrollments == b.enrollments


def test_edge_cases_embedded(tmp_path):
    db = _build_small()
    path = tmp_path / "campus.db"
    db.to_sqlite(path)
    conn = sqlite3.connect(str(path))
    # 成绩缺失（NULL）存在
    null_score = conn.execute("SELECT COUNT(*) FROM enrollments WHERE score IS NULL").fetchone()[0]
    assert null_score > 0
    # 退选记录存在
    withdrawn = conn.execute("SELECT COUNT(*) FROM enrollments WHERE status='withdrawn'").fetchone()[0]
    assert withdrawn > 0
    # 逾期未还（return_date IS NULL）存在
    unreturned = conn.execute("SELECT COUNT(*) FROM library_borrow WHERE return_date IS NULL").fetchone()[0]
    assert unreturned > 0
    conn.close()


def test_schema_ten_tables(tmp_path):
    db = _build_small()
    path = tmp_path / "campus.db"
    db.to_sqlite(path)
    conn = sqlite3.connect(str(path))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {"students", "teachers", "courses", "enrollments", "classrooms",
                "course_schedule", "books", "library_borrow", "card_transactions", "scholarships"}
    assert expected <= tables
    conn.close()
