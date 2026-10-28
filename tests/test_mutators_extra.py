"""新增变异体 M8–M10 与配置化改造的测试（2026-10-28）。

这三类变异体是「静默失效」——接口照常返回、不抛异常，只能靠**断言结果是否正确**
才能发现。它们的注入实现必须精确，否则变异体退化成"语法错误"，就失去了拟真意义
（测出来的只是"能不能编译"，而不是"能不能发现错"）。
"""

from __future__ import annotations

from agent import config
from agent.graph import CampusAgent, _drop_where, _hallucinate_columns
from testsuite.mutators import MUTATORS, apply


# ---------------------------------------------------------------------------
# 注册表与配置
# ---------------------------------------------------------------------------
def test_ten_mutants_registered():
    assert len(MUTATORS) == 10
    assert set(config.MUTANTS) == set(MUTATORS), "config.MUTANTS 与 mutators 注册表必须一致"


def test_new_mutants_metadata_complete():
    for mid in ("M8", "M9", "M10"):
        meta = MUTATORS[mid]
        assert meta["name"] and meta["target"] and meta["dimension"]
    assert MUTATORS["M8"]["dimension"] == "functional"
    assert MUTATORS["M9"]["dimension"] == "functional"
    assert MUTATORS["M10"]["dimension"] == "multiturn"


def test_config_constants_are_sane():
    assert 1 <= config.MAX_STEPS <= 32
    assert config.MAX_SLOT_KEYS >= 10
    assert 0 < config.MUTATION_KILL_DROP < 1


def test_env_override_of_max_steps(monkeypatch):
    """配置必须支持环境变量覆盖（演示/压测时改参不改码）。"""
    import importlib

    monkeypatch.setenv("DATACLERK_MAX_STEPS", "3")
    mod = importlib.reload(config)
    try:
        assert mod.MAX_STEPS == 3
    finally:
        monkeypatch.delenv("DATACLERK_MAX_STEPS", raising=False)
        importlib.reload(config)


# ---------------------------------------------------------------------------
# M8：列名幻觉
# ---------------------------------------------------------------------------
def test_hallucinate_columns_replaces_in_single_pass():
    sql = "SELECT AVG(score) FROM enrollments e JOIN students s ON e.student_id = s.student_id WHERE s.college = '计算机学院'"
    out = _hallucinate_columns(sql)

    # 真实列名应全部被替换掉
    for real in ("score", "student_id", "college"):
        assert real not in out, f"{real} 未被替换"
    # 且不能出现"替换级联"（score→grade_point 后再被 grade→enroll_year 二次污染）
    assert "enroll_year_point" not in out
    assert "grade_point" in out


def test_hallucinate_columns_is_word_bounded():
    """不能误伤同前缀标识符（如 student_id_x 不该被部分替换）。"""
    out = _hallucinate_columns("SELECT student_id_x FROM t")
    assert "student_id_x" in out


# ---------------------------------------------------------------------------
# M9：WHERE 被丢弃
# ---------------------------------------------------------------------------
def test_drop_where_keeps_group_by():
    sql = "SELECT college, COUNT(*) FROM students WHERE college = '计算机学院' GROUP BY college"
    out = _drop_where(sql)
    assert "WHERE" not in out.upper()
    assert "GROUP BY" in out.upper(), "GROUP BY 不该被一起删掉（否则变异体变成另一种缺陷）"


def test_drop_where_keeps_order_by_and_semicolon():
    out = _drop_where("SELECT * FROM students WHERE grade = 2024 ORDER BY student_id;")
    assert "WHERE" not in out.upper()
    assert "ORDER BY" in out.upper()


def test_drop_where_noop_without_where():
    sql = "SELECT COUNT(*) FROM students"
    assert _drop_where(sql).strip() == sql


# ---------------------------------------------------------------------------
# 注入后的基本契约
# ---------------------------------------------------------------------------
def test_agent_with_new_mutation_still_answers():
    """注入变异体后仍应返回结构化结果（不崩），失败交由断言判定。"""
    for mid in ("M8", "M9", "M10"):
        agent = apply(CampusAgent(), mid)
        r = agent.chat("计算机学院有多少学生？", role="admin")
        assert set(r) >= {"answer", "channel", "trace_id"}
        assert isinstance(r["answer"], str) and r["answer"]


def test_m10_ignores_slots():
    """M10 的语义是"存了不用"：约束不应生效，因此答案与无约束时一致。"""
    agent = apply(CampusAgent(), "M10")
    agent.chat("只看软件学院", role="admin", session_id="s1")
    r = agent.chat("有多少学生？", role="admin", session_id="s1")
    # 若约束生效，答案会是软件学院人数；M10 下应回落到"无约束"的模糊/全量结果
    assert "软件学院" not in r["answer"] or r["channel"] == "clarify"
