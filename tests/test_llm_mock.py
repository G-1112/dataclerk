"""确定性 mock SQL 生成器测试（零 Key 可复现的基线）。"""

from agent.llm import MockSqlGenerator, generate_sql

g = MockSqlGenerator()


def test_avg_score():
    sql = g.generate("计算机学院的平均绩点是多少？")
    assert "AVG" in sql
    assert "计算机学院" in sql
    assert "enrollments" in sql and "students" in sql


def test_count_students():
    sql = g.generate("软件学院有多少学生？")
    assert "COUNT" in sql
    assert "软件学院" in sql


def test_semester_extraction():
    sql = g.generate("2024年春季经济管理学院的平均成绩是多少？")
    assert "2024-SPRING" in sql


def test_list_courses():
    sql = g.generate("计算机学院有哪些课程？")
    assert "courses" in sql
    assert "计算机学院" in sql


def test_group_by_college():
    sql = g.generate("各学院的学生人数分布是怎样的？")
    assert "GROUP BY" in sql


def test_generate_sql_fallback_to_mock_without_key():
    # 无 API Key 时 generate_sql 走 mock
    sql = generate_sql("计算机学院有多少学生？", schema_desc="")
    assert "COUNT" in sql


# ---------------------------------------------------------------------------
# 第三轮修复：条件抽取对「数字与量词间有空格」的鲁棒性
# 原 bug：正则 r"(20\d{2})级" 不含空格，遇到「理学院 2024 级」时年级条件被
# 静默丢弃，SQL 退化为只按学院聚合（实测 51 而非 9），属于静默错误答案。
# ---------------------------------------------------------------------------
def test_grade_extraction_tolerates_space():
    sql_spaced = g.generate("理学院 2024 级有多少学生？")
    sql_tight = g.generate("理学院2024级有多少学生？")
    assert "grade = 2024" in sql_spaced or "grade=2024" in sql_spaced, sql_spaced
    assert "grade = 2024" in sql_tight or "grade=2024" in sql_tight, sql_tight


def test_semester_extraction_tolerates_space():
    for q in ("2024 年春季平均成绩", "2024年春季平均成绩"):
        sql = g.generate(q)
        assert "2024-SPRING" in sql, f"{q} -> {sql}"


def test_multi_condition_query_keeps_all_conditions():
    """多条件问题不得丢条件：学院 + 年级都要出现在 WHERE 中。"""
    sql = g.generate("理学院 2024 级有多少学生？")
    assert "college = '理学院'" in sql, sql
    assert "grade = 2024" in sql, sql
    assert " AND " in sql, sql
