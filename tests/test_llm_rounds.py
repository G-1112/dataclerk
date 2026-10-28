"""真实 LLM 多轮稳定性模式的测试（2026-10-28 新增）。

「只跑一轮 89.8%」回答不了一个问题：这个数是稳态，还是某次运气好？
`--rounds N` 就是为这个问题加的。它不能靠真调 API 来测（慢、要钱、不可复现），
所以这里替换掉 `run_functional`，只验证**统计与判定逻辑**：

- 每轮独立记录通过数与失败用例；
- 均值/区间/标准差算对，且 `all_identical` 只在各轮完全相同时为 True；
- 跨轮不稳定题目（有的轮次对、有的错）必须被单独点出来；
- 产物是 `llm_rounds.json`，绝不能覆盖主基线 `metrics_llm.json`。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from testsuite import llm_track as T


def _fake_runner(plans: list[list[str]], total: int = 5):
    """按轮次返回预设的失败用例集合。"""
    calls = {"n": 0}

    def _run(cases):
        failed = plans[calls["n"]]
        calls["n"] += 1
        details = [
            {"id": f"F{i:02d}", "passed": f"F{i:02d}" not in failed} for i in range(total)
        ]
        passed = sum(1 for d in details if d["passed"])
        return {"passed": passed, "total": total, "accuracy": passed / total,
                "passed_semantic": passed, "details": details}

    return _run


def _args(rounds: int, out_dir=None, no_write: bool = False):
    return SimpleNamespace(db="data/campus.db", prompt="fixed", rounds=rounds,
                           no_write=no_write, out_dir=out_dir)


def test_rounds_report_mean_range_and_std(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "_load_functional", lambda: [])
    monkeypatch.setattr(T, "run_functional", _fake_runner([[], ["F01"], []]))
    T.run_rounds(_args(3, tmp_path))  # 3 轮：5/5、4/5、5/5

    rep = json.loads((tmp_path / "llm_rounds.json").read_text(encoding="utf-8"))
    assert rep["passed_list"] == [5, 4, 5]
    assert rep["min"] == pytest.approx(0.8) and rep["max"] == pytest.approx(1.0)
    assert rep["mean"] == pytest.approx(0.9333, abs=1e-4)
    assert rep["std"] == pytest.approx(0.0943, abs=1e-3), "总体标准差（pstdev）"
    assert rep["all_identical"] is False


def test_rounds_flags_instable_cases(monkeypatch, tmp_path):
    """F01 只在后两轮错 → 跨轮不稳定（要点名）；F00 每轮都错 → 稳定失败（不属"不稳定"）。"""
    monkeypatch.setattr(T, "_load_functional", lambda: [])
    monkeypatch.setattr(T, "run_functional",
                        _fake_runner([["F00"], ["F00", "F01"], ["F00", "F01"]]))
    T.run_rounds(_args(3, tmp_path))
    rep = json.loads((tmp_path / "llm_rounds.json").read_text(encoding="utf-8"))
    assert rep["case_flakiness"] == {"F01": 2}, rep["case_flakiness"]
    assert rep["failed_ids_union"] == ["F00", "F01"]


def test_all_identical_requires_same_count_every_round(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "_load_functional", lambda: [])
    monkeypatch.setattr(T, "run_functional", _fake_runner([["F01"], ["F02"]]))
    T.run_rounds(_args(2, tmp_path))  # 两轮都是 4/5，但错的是不同题
    rep = json.loads((tmp_path / "llm_rounds.json").read_text(encoding="utf-8"))
    assert rep["all_identical"] is True, "通过数相同即视为一致（本题判定只看通过数）"
    assert rep["case_flakiness"] == {"F01": 1, "F02": 1}


def test_rounds_does_not_touch_main_baseline(monkeypatch, tmp_path):
    """红线：多轮模式只写 llm_rounds.*，不得覆盖 metrics_llm.json。"""
    monkeypatch.setattr(T, "_load_functional", lambda: [])
    monkeypatch.setattr(T, "run_functional", _fake_runner([[], []]))
    main = T.ROOT / "data" / "report" / "metrics_llm.json"
    before = main.read_bytes() if main.exists() else None
    T.run_rounds(_args(2, tmp_path))
    after = main.read_bytes() if main.exists() else None
    assert before == after


def test_md_rounds_renders_table(monkeypatch):
    md = T._md_rounds({
        "llm_model": "deepseek-chat", "db": "data/campus.db", "prompt_variant": "fixed",
        "rounds": [
            {"round": 1, "passed": 55, "total": 59, "accuracy": 0.9322,
             "passed_semantic": 59, "failed_ids": ["D1-058"]},
            {"round": 2, "passed": 59, "total": 59, "accuracy": 1.0,
             "passed_semantic": 59, "failed_ids": []},
        ],
        "passed_list": [55, 59], "accuracy_list": [0.9322, 1.0],
        "min": 0.9322, "max": 1.0, "mean": 0.9661, "std": 0.0339,
        "all_identical": False, "failed_ids_union": ["D1-058"],
        "case_flakiness": {"D1-058": 1},
    })
    assert "| 1 | 55/59" in md and "| 2 | 59/59" in md
    assert "跨轮不稳定用例" in md and "D1-058" in md
