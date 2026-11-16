"""用例语义守卫：期望 SQL 必须真的在回答「问题问的那件事」。

第十五轮实证：`_scholarship_kinds` 把「有多少**种**奖学金」写成了 `COUNT(*)`，
得到 372/382（人次），而真实 LLM 给的 `COUNT(DISTINCT name)=5` 才符合题意——
**判错的是基准，不是模型**。这类错误的危险之处在于它长得像"模型不行"，
如果不单独测，就会一直以"准确率 89.8%"的形式被引用下去。

所以这里锁三件事：
1. 期望 SQL 的语义（DISTINCT）不能退化；
2. mock 生成器的口径必须与期望一致（否则 mock 基线会红，但红的原因容易被误读成模型问题）；
3. 两种口径的数字确实不同——证明这个区分不是文字游戏。
"""

from __future__ import annotations

from agent.db import connect, set_db_path
from agent.llm import MockSqlGenerator
from testsuite.cases import _scholarship_kinds


def _scalar(sql: str):
    conn = connect()
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def test_scholarship_kinds_uses_distinct():
    sql = _scholarship_kinds("计算机学院")
    assert "DISTINCT" in sql.upper(), "「有多少种」必须去重，COUNT(*) 会答成人次"


def test_headcount_and_kind_count_really_differ():
    """两种口径在真实数据上必须不同，否则这条守卫没有意义。"""
    set_db_path("data/campus.db")
    kinds = _scalar(_scholarship_kinds("计算机学院"))
    head = _scalar(
        "SELECT COUNT(*) FROM scholarships sh JOIN students s "
        "ON sh.student_id = s.student_id WHERE s.college = '计算机学院'"
    )
    assert kinds < head, f"种类 {kinds} 应远小于人次 {head}"


def test_mock_sql_matches_expected_semantics():
    """mock 生成器与期望必须同口径：给同一问题要给出同一个数。"""
    set_db_path("data/campus.db")
    q = "计算机学院的奖学金有多少种？"
    mock_sql = MockSqlGenerator().generate(q, role="admin", current_user="")
    assert "DISTINCT" in mock_sql.upper(), mock_sql
    set_db_path("data/campus.db")
    assert _scalar(mock_sql) == _scalar(_scholarship_kinds("计算机学院")), (
        f"mock 与期望口径不一致：{mock_sql}"
    )


# ---------------------------------------------------------------------------
# 成绩口径（第十六轮）：退课记录不得计入「平均/最高/最低分」
# ---------------------------------------------------------------------------
def test_score_aggregates_exclude_withdrawn_in_expected():
    from testsuite.cases import _avg, _group_by_college_avg, _maxmin

    for sql in (_avg("计算机学院"), _avg("计算机学院", "2024-SPRING"),
                _maxmin("计算机学院", "MAX"), _maxmin("软件学院", "MIN"),
                _group_by_college_avg()):
        assert "status = 'completed'" in sql, sql


def test_score_aggregates_exclude_withdrawn_in_mock():
    from agent.llm import MockSqlGenerator

    m = MockSqlGenerator()
    for q, fn in (("计算机学院的平均绩点是多少？", "平均"),
                  ("计算机学院的最高分是多少？", "最高"),
                  ("各学院的平均绩点排名", "分组")):
        sql = m.generate(q, role="admin", current_user="")
        assert "status = 'completed'" in sql, f"{fn}：{sql}"


def test_withdrawn_scores_really_change_the_average():
    """若退课记录不影响均值，这条口径就没有存在意义——用真实数据证伪。"""
    set_db_path("data/campus.db")
    all_rows = _scalar(
        "SELECT AVG(e.score) FROM enrollments e JOIN students s "
        "ON e.student_id = s.student_id WHERE s.college = '经济管理学院'"
    )
    only_done = _scalar(
        "SELECT AVG(e.score) FROM enrollments e JOIN students s "
        "ON e.student_id = s.student_id WHERE s.college = '经济管理学院' "
        "AND e.status = 'completed'"
    )
    assert abs(all_rows - only_done) > 0.05, (all_rows, only_done)


def test_prompt_tells_model_the_status_rule():
    """口径只写在期望里没用：必须进提示词，否则模型每轮自行决定，稳定性无从谈起。"""
    from agent.llm import _SQL_SYSTEM

    assert "status" in _SQL_SYSTEM and "completed" in _SQL_SYSTEM
    assert "withdrawn" in _SQL_SYSTEM


def test_group_by_queries_are_ordered():
    """分组统计必须显式排序：SQLite 的 GROUP BY 行序未定义，否则稳定性维度会假红。"""
    from agent.llm import MockSqlGenerator

    m = MockSqlGenerator()
    for q in ("各学院的平均绩点排名", "各学院的学生人数分布是怎样的？"):
        sql = m.generate(q, role="admin", current_user="")
        assert "ORDER BY" in sql.upper(), f"{q} → {sql}"
