"""跨模型矩阵的测试（2026-10-28 新增）。

矩阵要回答的是「换个模型，结论还站得住吗」。这类对比最容易出的问题是：
缺 key 的模型被静默跳过却仍出现在"平均分"里、或把"所有模型都错"和"只有某家错"混为一谈。
所以断言集中在：跳过要写明原因、真难题与模型特有问题必须分开、换模型不能污染全局配置。
"""

from __future__ import annotations

import json
import os

import pytest

from testsuite import model_matrix as M


def _fake_cases(n: int = 3):
    return [{"id": f"F{i:02d}", "question": f"q{i}", "expected_sql": "SELECT 1"} for i in range(n)]


@pytest.fixture()
def fake_track(monkeypatch):
    """把 llm_track 的功能维度替换成按模型名返回预设失败集。"""
    plans = {"m1": [], "m2": [], "m3": []}

    def _run(cases):
        name = os.getenv("LLM_MODEL")
        failed = plans.get(name, [])
        details = [{"id": c["id"], "passed": c["id"] not in failed} for c in cases]
        passed = sum(1 for d in details if d["passed"])
        return {"passed": passed, "total": len(cases), "accuracy": passed / len(cases),
                "passed_semantic": passed, "accuracy_semantic": passed / len(cases),
                "details": details}

    from testsuite import llm_track as T

    monkeypatch.setattr(T, "_load_functional", lambda: _fake_cases())
    monkeypatch.setattr(T, "run_functional", _run)
    return plans


def test_missing_key_is_skipped_with_reason(fake_track, monkeypatch):
    monkeypatch.delenv("NOPE_KEY", raising=False)
    rep = M.run_matrix([{"name": "qwen", "base": "http://x", "model": "qwen", "key_env": "NOPE_KEY"}])
    row = rep["models"][0]
    assert row["status"] == "skipped" and "NOPE_KEY" in row["reason"]
    assert rep["hard_cases"] == [] and rep["all_models_identical"] is False


def test_hard_cases_vs_model_specific(fake_track, monkeypatch):
    monkeypatch.setenv("K1", "sk-1")
    monkeypatch.setenv("K2", "sk-2")
    fake_track["m1"] = ["F00"]          # m1 只在 F00 上错
    fake_track["m2"] = ["F00", "F01"]   # m2 在 F00/F01 上错

    rep = M.run_matrix([
        {"name": "m1", "base": "http://a", "model": "m1", "key_env": "K1"},
        {"name": "m2", "base": "http://b", "model": "m2", "key_env": "K2"},
    ])
    assert rep["hard_cases"] == ["F00"], "两个模型都错 → 真难题"
    assert rep["model_specific_failures"] == {"F01": ["m2"]}, "只有一家错 → 模型特有问题"
    assert rep["all_models_identical"] is False
    assert [r["passed"] for r in rep["models"]] == [2, 1]


def test_identical_models_flag(fake_track, monkeypatch):
    monkeypatch.setenv("K1", "sk-1")
    monkeypatch.setenv("K2", "sk-2")
    rep = M.run_matrix([
        {"name": "m1", "base": "http://a", "model": "m1", "key_env": "K1"},
        {"name": "m2", "base": "http://b", "model": "m2", "key_env": "K2"},
    ])
    assert rep["all_models_identical"] is True
    assert rep["hard_cases"] == []


def test_apply_model_rebuilds_client(monkeypatch):
    """换模型必须重建 agent.llm 的单例，否则第二次还是用第一个模型的 key/base。"""
    from agent import llm as agent_llm

    monkeypatch.setenv("K9", "sk-9")
    M._apply_model({"name": "x", "base": "http://base-x", "model": "model-x", "key_env": "K9"})
    assert agent_llm._llm.base_url == "http://base-x"
    assert agent_llm._llm.model == "model-x"
    assert agent_llm._llm.api_key == "sk-9"


def test_load_env_file_does_not_override(monkeypatch, tmp_path):
    env = tmp_path / ".env.llm"
    env.write_text("LLM_MODEL=from-file\nNEW_KEY=abc\n", encoding="utf-8")
    monkeypatch.setenv("LLM_MODEL", "from-env")
    monkeypatch.delenv("NEW_KEY", raising=False)
    M.load_env_file(env)
    assert os.getenv("LLM_MODEL") == "from-env", "不得覆盖已有环境变量"
    assert os.getenv("NEW_KEY") == "abc"


def test_md_renders_skipped_rows(fake_track, monkeypatch):
    md = M._md({
        "models": [
            {"name": "m1", "status": "ok", "passed": 3, "total": 3, "accuracy": 1.0,
             "passed_semantic": 3, "failed_ids": []},
            {"name": "qwen", "status": "skipped", "reason": "缺少环境变量 QWEN_API_KEY"},
        ],
        "hard_cases": [], "model_specific_failures": {}, "all_models_identical": True,
    })
    assert "| m1 | 3/3" in md and "skipped" in md


def test_main_writes_outputs(monkeypatch, tmp_path):
    cfg = tmp_path / "models.json"
    cfg.write_text(json.dumps([{"name": "m1", "model": "m1", "key_env": "K1"}]), encoding="utf-8")
    monkeypatch.setenv("K1", "sk-1")
    monkeypatch.setenv("LLM_API_KEY", "sk-1")
    monkeypatch.setattr(M, "run_matrix", lambda configs, limit=None: {
        "models": [{"name": "m1", "status": "ok", "model": "m1", "base": "",
                    "passed": 59, "total": 59, "accuracy": 1.0,
                    "passed_semantic": 59, "accuracy_semantic": 1.0, "failed_ids": []}],
        "case_total": 59, "hard_cases": [], "model_specific_failures": {},
        "all_models_identical": True,
    })
    monkeypatch.setattr("sys.argv", ["model_matrix", "--config", str(cfg),
                                     "--out-dir", str(tmp_path / "out")])
    assert M.main() == 0
    assert (tmp_path / "out" / "model_matrix.json").exists()
    assert "m1" in (tmp_path / "out" / "model_matrix.md").read_text(encoding="utf-8")


def test_main_missing_config(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", ["model_matrix", "--config", str(tmp_path / "none.json")])
    assert M.main() == 2
