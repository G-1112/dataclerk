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
