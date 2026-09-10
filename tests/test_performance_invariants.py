"""第三轮性能优化的正确性不变式测试。

性能优化最容易引入「快但错」的回归，因此这里把关键不变式固定下来：
  - 连接池：close() 归还而非关闭，但查询结果、query_only 语义不变；
  - 建索引：gen_db 生成的库对权限层恒定过滤列建立索引（查询计划为 SEARCH）；
  - AST 复用：改写只解析一次，但不得污染传入的 AST，且结果幂等；
  - LIMIT 走 AST：改为写进 AST 后仍必须真的出现在最终 SQL 中。
"""

from __future__ import annotations

import sqlite3

import sqlglot
import pytest

from agent import db as db_mod
from agent.guard.permission import PermissionGuard


# ------------------------------------------------------------------ 连接池
def test_connect_returns_reused_connection(tmp_path):
    p = tmp_path / "pool.db"
    sqlite3.connect(str(p)).close()
    c1 = db_mod.connect(str(p))
    c1.close()
    c2 = db_mod.connect(str(p))
    assert c1 is c2, "close() 后应归还连接池并被复用"


def test_reset_pool_actually_closes(tmp_path):
    p = tmp_path / "pool2.db"
    sqlite3.connect(str(p)).close()
    c1 = db_mod.connect(str(p))
    c1.close()
    db_mod.reset_pool()
    c2 = db_mod.connect(str(p))
    assert c1 is not c2, "reset_pool 后应得到新连接"


def test_pooled_connection_still_enforces_query_only(tmp_path):
    p = tmp_path / "ro2.db"
    sqlite3.connect(str(p)).close()
    conn = db_mod.connect(str(p))
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE t (a INT)")
    conn.close()
    # 复用后语义不变
    conn2 = db_mod.connect(str(p))
    with pytest.raises(sqlite3.OperationalError):
        conn2.execute("CREATE TABLE t2 (a INT)")
    conn2.close()


def test_pooled_connection_reads_fresh_data(tmp_path):
    """连接复用不得返回陈旧快照。"""
    p = tmp_path / "fresh.db"
    setup = sqlite3.connect(str(p))
    setup.execute("CREATE TABLE t (a INT)")
    setup.commit()
    setup.close()

    conn = db_mod.connect(str(p))
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
    conn.close()

    writer = sqlite3.connect(str(p))
    writer.execute("INSERT INTO t VALUES (1)")
    writer.commit()
    writer.close()

    conn2 = db_mod.connect(str(p))
    assert conn2.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1, "复用了陈旧数据"
    conn2.close()


def test_set_db_path_clears_pool(tmp_path):
    a = tmp_path / "a.db"
    b = tmp_path / "b.db"
    for f in (a, b):
        sqlite3.connect(str(f)).close()
    orig = db_mod.get_db_path()

    db_mod.set_db_path(a)
    ca = db_mod.connect()
    ca.close()
    db_mod.set_db_path(b)
    cb = db_mod.connect()
    assert ca is not cb, "切换 DB 路径后不应复用旧连接"
    cb.close()
    db_mod.set_db_path(orig)


# ------------------------------------------------------------------ 索引
# 测试用极小规模，保证快速（scale 语义见 data/gen_db.py main()）
_SMALL_SCALE = {
    "students": 40, "courses": 20, "enrollments": 120,
    "library_borrow": 60, "card_transactions": 80,
    "course_schedule": 40, "teachers": 10, "books": 30,
    "classrooms": 8, "scholarships": 20,
}


def _build_db(path) -> None:
    from data.gen_db import CampusDB  # noqa: PLC0415

    db = CampusDB(seed=42)
    db.build(_SMALL_SCALE)
    db.to_sqlite(path)


def test_generated_db_has_indexes_on_filter_columns(tmp_path):
    """权限层恒定过滤 student_id / college，这两列必须有索引。"""
    out = tmp_path / "idx.db"
    _build_db(out)

    conn = sqlite3.connect(str(out))
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
    )}
    conn.close()
    assert any("enrollments" in n and "student" in n for n in names), names
    assert any("students" in n and "college" in n for n in names), names


def test_index_makes_student_filter_use_search(tmp_path):
    out = tmp_path / "idx2.db"
    _build_db(out)
    conn = sqlite3.connect(str(out))
    plan = " ".join(
        r[-1] for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM enrollments WHERE student_id='S00001'"
        )
    )
    conn.close()
    assert "SEARCH" in plan and "SCAN" not in plan, plan


def test_gen_db_is_idempotent(tmp_path):
    """重复生成不得报 table already exists。"""
    out = tmp_path / "again.db"
    _build_db(out)
    _build_db(out)  # 第二次不应抛错
    conn = sqlite3.connect(str(out))
    assert conn.execute("SELECT COUNT(*) FROM students").fetchone()[0] > 0
    conn.close()


# --------------------------------------------------------------- AST 复用
def test_force_student_filter_does_not_pollute_input_ast():
    """AST 复用要求改写不能污染传入的树（否则第二次调用结果错误）。"""
    sql = "SELECT * FROM enrollments WHERE student_id='S00001'"
    tree = sqlglot.parse_one(sql, read="sqlite")
    before = tree.sql(dialect="sqlite")

    g = PermissionGuard(role="student", current_user="S00001")
    out1 = g._force_student_filter(sql, "S00001", tree)
    assert tree.sql(dialect="sqlite") == before, "传入 AST 被就地修改"

    out2 = g._force_student_filter(sql, "S00001", tree)
    assert out1 == out2, "改写不幂等"


def test_limit_is_applied_via_ast():
    """LIMIT 改为写入 AST 后，仍必须出现在最终 SQL 中。"""
    g = PermissionGuard(role="student", current_user="S00001")
    r = g.check("SELECT * FROM card_transactions")
    assert r.allowed, r.reason
    assert "LIMIT 100" in r.rewritten_sql, r.rewritten_sql


def test_or_bypass_still_parenthesized_after_refactor():
    """性能重构后，OR 优先级绕过的括号防护必须仍然有效。"""
    g = PermissionGuard(role="student", current_user="S00001")
    r = g.check("SELECT * FROM enrollments WHERE 1=1 OR student_id='S00001'")
    assert r.allowed, r.reason
    rw = r.rewritten_sql
    assert rw.index("(") < rw.index("AND student_id = 'S00001'"), rw
    assert "LIMIT 100" in rw


def test_guard_result_is_deterministic_across_calls():
    """同一 SQL 多次校验结果必须一致（AST 不得被跨调用污染）。"""
    g = PermissionGuard(role="student", current_user="S00001")
    sql = "SELECT * FROM library_borrow WHERE student_id='S00001' OR 1=1"
    results = {g.check(sql).rewritten_sql for _ in range(5)}
    assert len(results) == 1, f"多次校验结果不一致：{results}"


# --------------------------------------------------------------- Trace I/O
def test_tracer_caches_resolved_dir():
    """Tracer 必须缓存 resolve 结果，不得每次读写都调用（Windows 下约 0.14ms/次）。"""
    from agent.trace import Tracer

    t = Tracer()
    assert hasattr(t, "_dir_resolved"), "未缓存解析后的目录"
    # 缓存值与实时 resolve 一致
    assert t._dir_resolved == t.dir.resolve()


def test_tracer_record_does_not_resolve_per_call(monkeypatch):
    """record 期间不得再调用 Path.resolve（通过计数验证）。"""
    from pathlib import Path

    from agent.trace import Tracer

    t = Tracer()
    tid = t.start()
    calls = {"n": 0}
    orig = Path.resolve

    def counting_resolve(self, *a, **kw):
        calls["n"] += 1
        return orig(self, *a, **kw)

    monkeypatch.setattr(Path, "resolve", counting_resolve)
    t.record(tid, 1, "think", "tool", {}, "obs")
    t.get(tid)
    assert calls["n"] == 0, f"record/get 中仍调用了 resolve {calls['n']} 次"
    (t.dir / f"{tid}.jsonl").unlink(missing_ok=True)


def test_trace_write_is_reasonably_fast():
    """单条 trace 写入应在 5ms 内（实测约 0.95ms，阈值留 5 倍余量）。"""
    import time

    from agent.trace import Tracer

    t = Tracer()
    tid = t.start()
    t.record(tid, 1, "warm", "tool", {}, "obs")  # 预热
    n = 50
    t0 = time.perf_counter()
    for i in range(n):
        t.record(tid, i + 2, "think", "tool", {}, "obs")
    per = (time.perf_counter() - t0) / n
    (t.dir / f"{tid}.jsonl").unlink(missing_ok=True)
    assert per < 0.005, f"单条 trace 写入 {per * 1000:.2f}ms，疑似回归"
