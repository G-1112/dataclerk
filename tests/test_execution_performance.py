"""执行层性能与健壮性测试（第二轮优化）。

覆盖：
- 大结果集被 MAX_ROWS 截断（fetchmany，不物化全表）；
- 小结果集不误标截断；
- 病态查询（笛卡尔积）在超时预算内被中断；
- 连接默认 query_only，写语句被引擎层拒绝（纵深防御）。

注意：execute_sql 会先过 PermissionGuard（只认 SCHEMA 中的真实表），
因此夹具必须建真实表名（如 card_transactions），否则会被判"不存在的表"。
"""

from __future__ import annotations

import sqlite3

import pytest

from agent import db as db_mod
from agent.tools import execute_sql as es


@pytest.fixture()
def big_db(tmp_path, monkeypatch):
    """基于真实表 card_transactions 构造 50000 行，验证截断行为。"""
    path = tmp_path / "big.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE card_transactions (id INTEGER, student_id TEXT, amount REAL, "
        "merchant TEXT, category TEXT, trans_time TEXT)"
    )
    conn.executemany(
        "INSERT INTO card_transactions VALUES (?, ?, ?, ?, ?, ?)",
        ((i, f"S{i:06d}", 1.0, "食堂", "餐饮", "2025-01-01") for i in range(50000)),
    )
    conn.commit()
    conn.close()

    orig = db_mod.get_db_path()
    db_mod.set_db_path(path)
    yield path
    db_mod.set_db_path(orig)


def test_large_result_is_truncated_not_fatal(big_db):
    r = es.execute_sql("SELECT * FROM card_transactions", role="admin")
    assert r["ok"], r.get("error")
    assert r["truncated"] is True
    assert len(r["rows"]) == db_mod.MAX_ROWS


def test_small_result_not_marked_truncated(big_db):
    r = es.execute_sql("SELECT * FROM card_transactions LIMIT 10", role="admin")
    assert r["ok"]
    assert r["truncated"] is False
    assert len(r["rows"]) == 10


def test_pathological_query_is_interrupted(big_db, monkeypatch):
    """三方自连接产生 ~1.25e14 行，必须在超时预算内被中断。"""
    monkeypatch.setattr(es, "SQL_TIMEOUT_MS", 200)
    r = es.execute_sql(
        "SELECT COUNT(*) FROM card_transactions a, card_transactions b, card_transactions c",
        role="admin",
    )
    assert not r["ok"], "病态查询不应静默返回成功"
    assert "超时" in r["error"] or "interrupted" in r["error"].lower()


def test_connection_is_query_only(tmp_path):
    p = tmp_path / "ro.db"
    sqlite3.connect(str(p)).close()
    conn = db_mod.connect(str(p))
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE x (a INT)")
    conn.close()


def test_timeout_budget_is_reasonable():
    assert 500 <= es.SQL_TIMEOUT_MS <= 10000
