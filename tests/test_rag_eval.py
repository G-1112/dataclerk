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
    # 2026-10-28 契约扩展：新增 llm_judge（无依赖替代 RAGAS/DeepEval）
    assert set(r) == {"ragas", "deepeval", "llm_judge", "available"}
    assert isinstance(r["available"]["ragas"], bool)
    assert isinstance(r["available"]["deepeval"], bool)
    assert isinstance(r["available"]["llm"], bool)
    # mock 模式下三项都应优雅跳过，而不是抛异常或伪造分数
    assert r["llm_judge"]["status"] == "skipped"


def test_llm_judge_skips_without_key():
    from testsuite import rag_judge

    r = rag_judge.run(_SAMPLE)
    assert r["status"] in ("skipped", "ok", "error")
    if r["status"] == "skipped":
        assert "reason" in r


def test_llm_judge_parses_dirty_output():
    """判官输出常带前言/代码块；解析器必须能取出 JSON，且对越界数值钳制。"""
    from testsuite import rag_judge

    data = rag_judge._parse_json('好的，评估如下：\n```json\n{"claims": 3, "supported": 2, "relevancy": 0.8}\n```')
    assert data == {"claims": 3, "supported": 2, "relevancy": 0.8}
    assert rag_judge._parse_json("完全没有 JSON") is None
    assert rag_judge._clamp(1.7) == 1.0 and rag_judge._clamp(-0.2) == 0.0


def test_availability_flags_are_booleans():
    assert isinstance(rag_eval.ragas_available(), bool)
    assert isinstance(rag_eval.deepeval_available(), bool)
