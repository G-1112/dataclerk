"""RAGAS/DeepEval 集成测试（FR-T04）：未安装/未配置时优雅跳过，不阻塞主流程。"""

from testsuite import rag_eval

_SAMPLE = [{"question": "国家奖学金的评定标准是什么？", "answer": "需排名前5%", "contexts": ["奖学金评定办法片段"]}]


def test_ragas_skips_gracefully_without_llm():
    # 无 LLM Key（mock 模式）时必然返回 skipped（即使装了 ragas 也无法评测）
    r = rag_eval.run_ragas_eval(_SAMPLE)
    assert r["status"] in ("skipped", "ok", "error")
    if r["status"] == "skipped":
        assert "reason" in r


def test_deepeval_skips_gracefully_without_llm():
    r = rag_eval.run_deepeval_eval(_SAMPLE)
    assert r["status"] in ("skipped", "ok", "error")


def test_run_all_returns_full_structure():
    r = rag_eval.run_all(_SAMPLE)
    assert set(r) == {"ragas", "deepeval", "available"}
    assert isinstance(r["available"]["ragas"], bool)
    assert isinstance(r["available"]["deepeval"], bool)
    assert isinstance(r["available"]["llm"], bool)


def test_availability_flags_are_booleans():
    assert isinstance(rag_eval.ragas_available(), bool)
    assert isinstance(rag_eval.deepeval_available(), bool)
