"""结果集语义等价判定的测试（2026-10-28 新增）。

核心要求：**放宽必须有据、且只放宽到声明的级别**。
所以这里的断言分两类：
- 该救回来的要救（数值精度、多选列、重复行、显式声明的子集）；
- 不该救的绝不能救（行数不同、值不同、未声明就多列）——否则准确率就是靠改口径刷的。
"""

from __future__ import annotations

import pytest

from testsuite import equivalence as eq


# ---------------------------------------------------------------------------
# 严格级
# ---------------------------------------------------------------------------
def test_exact_ignores_row_order():
    r = eq.compare([[1, "a"], [2, "b"]], [[2, "b"], [1, "a"]])
    assert r["equivalent"] and r["level"] == "exact"


def test_exact_handles_none_and_empty_string():
    assert eq.compare([[None]], [[""]])["equivalent"]


def test_both_empty_is_equivalent():
    r = eq.compare([], [])
    assert r["equivalent"] and r["level"] == "exact"


def test_one_side_empty_is_not_equivalent():
    r = eq.compare([[1]], [])
    assert not r["equivalent"] and "一侧为空" in r["reason"]


def test_int_vs_float_string_is_same_fact():
    assert eq.compare([[943]], [[943.0]])["equivalent"]
    assert eq.compare([["943"]], [[943]])["equivalent"]


# ---------------------------------------------------------------------------
# 数值级
# ---------------------------------------------------------------------------
def test_numeric_tolerance_level():
    r = eq.compare([[100]], [[100.005]], spec={"numeric_tolerance": 0.01})
    assert r["equivalent"] and r["level"] == "numeric"


def test_numeric_beyond_tolerance_fails():
    r = eq.compare([[100]], [[100.5]], spec={"numeric_tolerance": 0.01})
    assert not r["equivalent"]
    assert "值不同" in r["reason"] or "行数" in r["reason"]


def test_numeric_tolerance_default_is_strict_enough():
    """默认容差 1e-6：0.8 与 0.9 绝不能被当成等价。"""
    assert not eq.compare([[0.8]], [[0.9]])["equivalent"]


# ---------------------------------------------------------------------------
# 列级（投影）
# ---------------------------------------------------------------------------
def test_projection_allows_extra_column_when_declared():
    exp = [["计算机学院", 943]]
    act = [["计算机学院", 943, "备注"]]
    r = eq.compare(exp, act, ["college", "cnt"], ["college", "cnt", "note"],
                   spec={"allow_extra_columns": True})
    assert r["equivalent"] and r["level"] == "projection"
    assert "多列" in r["reason"]


def test_projection_rejects_extra_column_by_default():
    exp = [["计算机学院", 943]]
    act = [["计算机学院", 943, "备注"]]
    r = eq.compare(exp, act, ["college", "cnt"], ["college", "cnt", "note"])
    assert not r["equivalent"]
    assert "多出列" in r["reason"]


def test_projection_allows_missing_column_when_declared():
    """少列在显式放行时算等价，但必须报 `column_subset` 而不是 `projection`：
    报告要能区分「多给了一列」（无害）与「少给了一列」（可能漏答关键信息）。"""
    exp = [["计算机学院", 943]]
    act = [["计算机学院"]]
    r = eq.compare(exp, act, ["college", "cnt"], ["college"],
                   spec={"allow_missing_columns": True})
    assert r["equivalent"] and r["level"] == "column_subset"
    assert "缺列" in r["reason"]


def test_column_subset_still_requires_rows_to_match():
    """少列不是免死金牌：留下来的列的值必须逐行一致。"""
    r = eq.compare([["计算机学院", 943]], [["软件学院"]], ["college", "cnt"], ["college"],
                   spec={"allow_missing_columns": True})
    assert not r["equivalent"]


def test_column_subset_does_not_mask_wrong_column_choice():
    """问「某生成绩」只答姓名——少列放行也不能救：期望列一个都没对上。"""
    exp = [["S00001", 88]]
    act = [["张三"]]
    r = eq.compare(exp, act, ["student_id", "score"], ["name"],
                   spec={"allow_missing_columns": True})
    assert not r["equivalent"]


def test_column_subset_is_off_by_default():
    """默认口径下少列必须判错——否则"漏答一列"会被静默算成正确。"""
    r = eq.compare([["计算机学院", 943]], [["计算机学院"]], ["college", "cnt"], ["college"])
    assert not r["equivalent"] and r["level"] == "none"


def test_projection_rejects_missing_column_by_default():
    r = eq.compare([["计算机学院", 943]], [["计算机学院"]], ["college", "cnt"], ["college"])
    assert not r["equivalent"] and "缺少列" in r["reason"]


def test_projection_aligns_by_alias_and_column_order():
    """列名同义 + 列序不同，都应按列对齐后判等。"""
    exp = [["S00001", 88]]
    act = [[88, "S00001"]]
    r = eq.compare(exp, act, ["student_id", "score"], ["score", "stu_no"])
    assert r["equivalent"] and r["level"] == "projection"


def test_projection_fails_when_values_differ():
    r = eq.compare([["计算机学院", 943]], [["计算机学院", 944]],
                   ["college", "cnt"], ["college", "cnt"])
    assert not r["equivalent"]


# ---------------------------------------------------------------------------
# 集合级（去重）
# ---------------------------------------------------------------------------
def test_set_level_only_when_declared():
    exp = [["计算机学院"], ["计算机学院"], ["软件学院"]]
    act = [["计算机学院"], ["软件学院"]]
    assert not eq.compare(exp, act)["equivalent"]
    r = eq.compare(exp, act, spec={"ignore_duplicates": True})
    assert r["equivalent"] and r["level"] == "set"


def test_set_level_does_not_hide_missing_values():
    """去重只忽略重复，不允许少值。"""
    r = eq.compare([["A"], ["B"]], [["A"]], spec={"ignore_duplicates": True})
    assert not r["equivalent"]


# ---------------------------------------------------------------------------
# 子集级（最危险，必须显式开）
# ---------------------------------------------------------------------------
def test_subset_requires_explicit_opt_in():
    exp = [[1], [2], [3]]
    act = [[1], [2]]
    assert not eq.compare(exp, act)["equivalent"]
    r = eq.compare(exp, act, spec={"allow_missing_rows": True})
    assert r["equivalent"] and r["level"] == "subset"
    assert "⊆" in r["reason"]


def test_subset_rejects_values_outside_expected():
    """子集语义也不能容纳"期望里没有的行"。"""
    r = eq.compare([[1], [2]], [[1], [99]], spec={"allow_missing_rows": True})
    assert not r["equivalent"]


# ---------------------------------------------------------------------------
# 诊断信息
# ---------------------------------------------------------------------------
def test_diagnose_reports_row_count_mismatch():
    r = eq.compare([[1], [2]], [[1]])
    assert "行数不同" in r["reason"]


def test_diagnose_reports_column_count_mismatch():
    r = eq.compare([[1, 2], [3, 4]], [[1], [3]])
    assert "列数不同" in r["reason"]


def test_diagnose_includes_column_names_when_available():
    r = eq.compare([[1]], [[2]], ["student_id"], ["student_id"])
    assert "student_id" in r["reason"]


def test_spec_is_not_mutated():
    spec = {"numeric_tolerance": 0.01}
    eq.compare([[1]], [[2]], spec=spec)
    assert spec == {"numeric_tolerance": 0.01}
    assert eq.DEFAULT_SPEC["allow_missing_rows"] is False


# ---------------------------------------------------------------------------
# 与跑批链路的集成：主指标仍是严格口径，语义口径只做附加
# ---------------------------------------------------------------------------
def test_run_functional_carries_semantic_fields():
    from testsuite.adapters import LocalAgentAdapter
    from testsuite.run_suite import run_functional

    cases = [{
        "id": "T-001",
        "question": "计算机学院有多少学生？",
        "expected_sql": "SELECT COUNT(*) FROM students WHERE college = '计算机学院'",
    }]
    rows = run_functional(cases, LocalAgentAdapter())
    r = rows[0]
    assert r["passed"] == r["result_match"], "严格口径必须与 result_match 一致"
    assert "equivalent_semantic" in r and "equiv_level" in r
    assert r["equiv_level"] in ("exact", "numeric", "projection", "set", "subset", "none")


def test_metrics_keep_strict_as_primary():
    """语义等价不得替换主指标：严格判错的用例仍计入"严格判错"。"""
    from testsuite import metrics

    results = [
        {"result_match": True, "equivalent_semantic": True, "sql_executable": True},
        {"result_match": False, "equivalent_semantic": True, "sql_executable": True},
        {"result_match": False, "equivalent_semantic": False, "sql_executable": False},
    ]
    m = metrics.functional(results)
    assert m["执行准确率"] == pytest.approx(1 / 3, abs=1e-4)
    assert m["语义等价准确率"] == pytest.approx(2 / 3, abs=1e-4)
    assert m["严格判错但语义等价"] == 1
    assert m["SQL可执行率"] == pytest.approx(2 / 3, abs=1e-4)
