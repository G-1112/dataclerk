"""口径复算脚本的测试（2026-10-28 新增）。

`testsuite/replay_llm_details.py` 的作用是"不重跑大模型，只重放已落盘的 SQL"。
这类工具最危险的失效方式是**悄悄算出好看的数**：重放出来的严格判定与原跑批不同，
却仍被当成新结论引用。所以这里重点测三件事：

1. 自校验必须能抓出不一致（`strict_mismatch`）；
2. 少列放宽只影响放宽口径，不许污染严格口径；
3. 坏 SQL / 缺 SQL 的用例必须显式标"跳过"，不能算进分母。
"""

from __future__ import annotations

import pytest

from testsuite import replay_llm_details as R

_CASES = [
    {"id": "F1", "expected_sql": "EXP_OK"},
    {"id": "F2", "expected_sql": "EXP_TEACHER"},
]

_EXPECTED = {
    "EXP_OK": ([["计算机学院", 943]], ["college", "cnt"]),
    "EXP_TEACHER": ([["张三", "教授"], ["李四", "讲师"]], ["name", "title"]),
}
_ACTUAL = {
    "SEL_OK": ([["计算机学院", 943]], ["college", "cnt"]),
    "SEL_TEACHER_NAME_ONLY": ([["张三"], ["李四"]], ["name"]),
}


@pytest.fixture()
def fake_io(monkeypatch):
    monkeypatch.setattr(R, "functional_cases", lambda: _CASES)
    monkeypatch.setattr(R, "_expected_rows_full", lambda sql: _EXPECTED[sql])
    monkeypatch.setattr(R, "_exec", lambda sql: _ACTUAL[sql])


def _details(*items):
    return {"functional_details": list(items)}


def test_replay_relaxed_gain_only_for_missing_column(fake_io):
    rep = R.replay(_details(
        {"id": "F1", "question": "计算机学院有多少学生", "passed": True, "sql_llm": "SEL_OK"},
        {"id": "F2", "question": "有哪些老师", "passed": False, "sql_llm": "SEL_TEACHER_NAME_ONLY"},
    ))
    s = rep["summary"]
    assert s["total"] == 2
    assert s["strict_passed"] == 1, "严格口径不得被放宽污染"
    assert s["relaxed_passed"] == 2
    assert s["relaxed_gain"] == 1
    assert s["strict_mismatch"] == []
    relaxed = [c for c in rep["cases"] if c.get("replay_ok") and c["relaxed"] and not c["strict"]]
    assert [c["id"] for c in relaxed] == ["F2"]
    assert relaxed[0]["relaxed_level"] == "column_subset"


def test_replay_flags_strict_mismatch(fake_io):
    """落盘说通过、重放却判错 → 必须报不一致（数据库或期望已变）。"""
    rep = R.replay(_details(
        {"id": "F1", "question": "q", "passed": False, "sql_llm": "SEL_OK"},
    ))
    assert rep["summary"]["strict_mismatch"] == ["F1"]


def test_replay_skips_case_without_sql(fake_io):
    rep = R.replay(_details(
        {"id": "F1", "question": "q", "passed": True, "sql_llm": ""},
        {"id": "F2", "question": "q", "passed": True, "sql_llm": "SEL_TEACHER_NAME_ONLY"},
    ))
    assert rep["summary"]["total"] == 1, "缺 SQL 的用例不能进分母"
    assert rep["cases"][0]["replay_ok"] is False
    assert "跳过" in rep["cases"][0]["note"]


def test_replay_marks_execution_failure(fake_io, monkeypatch):
    def boom(sql):
        raise RuntimeError("no such table: nope")

    monkeypatch.setattr(R, "_exec", boom)
    rep = R.replay(_details(
        {"id": "F1", "question": "q", "passed": False, "sql_llm": "SEL_OK"},
    ))
    assert rep["summary"]["total"] == 0
    assert "重放失败" in rep["cases"][0]["note"]


def test_replay_unknown_case_id_is_skipped(fake_io):
    rep = R.replay(_details(
        {"id": "NOT-IN-SUITE", "question": "q", "passed": False, "sql_llm": "SEL_OK"},
    ))
    assert rep["summary"]["total"] == 0


def test_replay_cli_exit_code_reflects_mismatch(fake_io, tmp_path, monkeypatch):
    """CLI 退出码必须能当门禁用：不一致 → 非 0。"""
    import json

    details = tmp_path / "d.json"
    details.write_text(json.dumps(_details(
        {"id": "F1", "question": "q", "passed": False, "sql_llm": "SEL_OK"}
    ), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "o.json"
    monkeypatch.setattr("sys.argv", ["replay", "--details", str(details),
                                     "--db", "data/campus_small.db", "--out", str(out)])
    monkeypatch.setattr("agent.db.set_db_path", lambda p: None)
    assert R.main() == 1
    assert out.exists(), "即便判定不一致也要留下产物，便于排查"
