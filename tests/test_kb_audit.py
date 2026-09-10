"""RAG 知识库体检测试（FR-T10）。

对应赛道三路径二要求：「RAG知识库测试工具：自动检测知识库中的
冲突、冗余与错误，评估检索质量」。

测试分两层：
  - 单元层：用临时构造的小知识库，验证各检查器的判定与边界；
  - 集成层：对仓库真实知识库跑一遍，确保工具在真实数据上可用。
"""

from __future__ import annotations

import pytest

from testsuite import kb_audit as K


def _write(tmp_path, name: str, content: str):
    p = tmp_path / f"{name}.txt"
    p.write_text(content, encoding="utf-8")
    return p


def _load(tmp_path):
    from agent.tools.retrieve_policy import PolicyRetriever

    return PolicyRetriever(tmp_path).load()


# ------------------------------------------------------------------ 数值抽取
def test_extract_facts_finds_value_and_unit():
    facts = K.extract_facts("国家奖学金每生每年 8000 元。")
    assert len(facts) == 1
    assert facts[0]["value"] == 8000.0
    assert facts[0]["unit"] == "元"


def test_extract_facts_handles_thousands_separator():
    facts = K.extract_facts("学费 12,000 元。")
    assert facts[0]["value"] == 12000.0


def test_extract_facts_handles_decimal():
    facts = K.extract_facts("绩点要求 3.5 分。")
    assert facts[0]["value"] == 3.5


def test_extract_facts_empty_when_no_number():
    assert K.extract_facts("本规定自发布之日起施行。") == []


def test_extract_facts_captures_context():
    facts = K.extract_facts("每生每年 8000 元。")
    assert "每生每年" in facts[0]["context"]


# ------------------------------------------------------------------ 冗余检测
def test_detects_near_duplicate(tmp_path):
    _write(tmp_path, "A", "休学一般以一年为限，累计不得超过两年。")
    _write(tmp_path, "B", "学生休学一般以一年为限，累计不得超过两年。")
    dups = K.find_duplicates(_load(tmp_path))
    assert len(dups) == 1
    assert dups[0]["similarity"] >= K.DUP_COSINE


def test_no_duplicate_for_distinct_content(tmp_path):
    _write(tmp_path, "A", "图书馆每人可借 10 册图书。")
    _write(tmp_path, "B", "宿舍晚上 23 点关门。")
    assert K.find_duplicates(_load(tmp_path)) == []


def test_duplicates_sorted_by_similarity_desc(tmp_path):
    _write(tmp_path, "A", "考试作弊的，该科目成绩记零分。")
    _write(tmp_path, "B", "考试作弊的，该科目成绩记零分。")  # 完全相同
    _write(tmp_path, "C", "考试作弊的，该科目成绩记为 0 分。")  # 近似
    dups = K.find_duplicates(_load(tmp_path))
    assert dups[0]["similarity"] >= dups[-1]["similarity"]


# ------------------------------------------------------------------ 冲突检测
def test_detects_conflicting_values_same_context(tmp_path):
    _write(tmp_path, "A", "国家奖学金每生每年 8000 元。")
    _write(tmp_path, "B", "国家奖学金每生每年 5000 元。")
    conflicts = K.find_conflicts(_load(tmp_path))
    assert len(conflicts) == 1
    assert conflicts[0]["values"] == [5000.0, 8000.0]
    assert conflicts[0]["cross_doc"] is True


def test_same_document_conflict_marked_not_cross_doc(tmp_path):
    """同文档内的数值差异不得误标为跨文档（曾因用 doc_id 比较而恒为 True）。"""
    _write(tmp_path, "A", "国家奖学金每生每年 8000 元。\n国家励志奖学金每生每年 5000 元。")
    conflicts = K.find_conflicts(_load(tmp_path))
    assert conflicts, "应检出同文档内同上下文的数值差异"
    assert conflicts[0]["cross_doc"] is False


def test_no_conflict_when_values_identical(tmp_path):
    _write(tmp_path, "A", "每生每年 8000 元。")
    _write(tmp_path, "B", "每生每年 8000 元。")
    assert K.find_conflicts(_load(tmp_path)) == []


def test_no_conflict_for_different_units(tmp_path):
    _write(tmp_path, "A", "每生每年 8000 元。")
    _write(tmp_path, "B", "每年 5 次。")
    assert K.find_conflicts(_load(tmp_path)) == []


def test_conflicts_include_evidence_for_human_review(tmp_path):
    """疑似冲突必须附原文，供人工确认（启发式结论不能只给判定）。"""
    _write(tmp_path, "A", "每生每年 8000 元。")
    _write(tmp_path, "B", "每生每年 5000 元。")
    c = K.find_conflicts(_load(tmp_path))[0]
    assert len(c["evidence"]) >= 2
    for e in c["evidence"]:
        assert "text" in e and "doc" in e


# ------------------------------------------------------------------ 质量问题
def test_detects_empty_document(tmp_path):
    _write(tmp_path, "空", "   \n  \n")
    snippets = _load(tmp_path)
    docs = {"空": "   \n  \n"}
    kinds = [q["kind"] for q in K.find_quality_issues(snippets, docs)]
    assert "空文档" in kinds


def test_detects_too_short_snippet(tmp_path):
    _write(tmp_path, "A", "标题\n这是一条足够长的正常规定内容，用于占位。")
    snippets = _load(tmp_path)
    issues = K.find_quality_issues(snippets, {"A": "标题\n这是一条足够长的正常规定内容，用于占位。"})
    assert any(q["kind"] == "过短碎片" for q in issues)


def test_detects_long_line_without_punctuation(tmp_path):
    long_no_punct = "甲" * 80
    _write(tmp_path, "A", long_no_punct)
    snippets = _load(tmp_path)
    issues = K.find_quality_issues(snippets, {"A": long_no_punct})
    assert any(q["kind"] == "无标点长句" for q in issues)


def test_detects_duplicate_lines_in_document(tmp_path):
    content = "同一条规定内容重复出现。\n同一条规定内容重复出现。"
    _write(tmp_path, "A", content)
    snippets = _load(tmp_path)
    issues = K.find_quality_issues(snippets, {"A": content})
    assert any(q["kind"] == "文档内重复行" for q in issues)


# ------------------------------------------------------------------ 汇总
def test_audit_returns_expected_shape(tmp_path):
    _write(tmp_path, "A", "图书馆每人可借 10 册图书。")
    rep = K.audit(tmp_path)
    for key in ("docs", "snippets", "avg_len", "duplicates", "conflicts",
                "quality", "blockers", "warnings", "healthy", "thresholds"):
        assert key in rep, key
    assert rep["docs"] == 1


def test_healthy_requires_no_blockers(tmp_path):
    _write(tmp_path, "A", "内容正常且唯一的一条规定。")
    assert K.audit(tmp_path)["healthy"] is True


def test_duplicate_makes_kb_unhealthy(tmp_path):
    _write(tmp_path, "A", "完全一致的规定内容在此。")
    _write(tmp_path, "B", "完全一致的规定内容在此。")
    rep = K.audit(tmp_path)
    assert rep["healthy"] is False
    assert rep["blockers"] >= 1


def test_conflict_is_warning_not_blocker(tmp_path):
    """疑似冲突是启发式结论，应为 warning 而非 blocker。"""
    _write(tmp_path, "A", "每生每年 8000 元。")
    _write(tmp_path, "B", "每生每年 5000 元。")
    rep = K.audit(tmp_path)
    assert rep["blockers"] == 0
    assert rep["warnings"] >= 1
    assert rep["healthy"] is True


# ------------------------------------------------------------------ 检索健康度
def test_retrieval_health_flags_uncovered_query():
    """无对应内容的查询应被标为弱项（覆盖缺口）。

    注意 retrieval_health 走默认知识库，故用明显无关的查询来制造缺口。
    """
    h = K.retrieval_health(["量子纠缠退相干与超导约瑟夫森结的相位关系"])
    assert h["weak_count"] == 1
    assert h["probes"][0]["ok"] is False
    assert h["coverage"] == 0.0


def test_retrieval_health_marks_covered_query_ok():
    h = K.retrieval_health(["奖学金怎么申请"])
    assert h["probes"][0]["ok"] is True
    assert h["coverage"] == 1.0


def test_retrieval_health_handles_empty_probe_list():
    h = K.retrieval_health([])
    assert h["coverage"] == 0.0
    assert h["weak_count"] == 0


def test_retrieval_health_reports_top_doc():
    h = K.retrieval_health(["宿舍几点关门"])
    assert h["probes"][0]["top_doc"], "应给出命中的文档名"


def test_retrieval_health_shape_on_real_kb():
    """集成：真实知识库上的检索健康度应有合理结构。"""
    h = K.retrieval_health(["奖学金怎么申请", "宿舍几点关门"], top_k=3)
    assert len(h["probes"]) == 2
    assert 0.0 <= h["coverage"] <= 1.0
    assert h["threshold"] == K.PROBE_MIN_SCORE
    for p in h["probes"]:
        assert "top1_score" in p and "ok" in p


# ------------------------------------------------------------------ 真实知识库
def test_real_knowledge_base_audit_runs():
    """集成：真实知识库体检可跑通并给出结论。"""
    rep = K.audit()
    assert rep["docs"] >= 10, f"政策文档过少：{rep['docs']}"
    assert rep["snippets"] > 0
    # 已知真实缺陷：休学条款在两个文档间近重复（本条同时是回归守卫）
    assert any(
        {d["a"].split("#")[0], d["b"].split("#")[0]}
        == {"休学与复学规定", "学籍管理规定"}
        for d in rep["duplicates"]
    ), "曾检出的跨文档近重复片段消失了，请确认知识库是否被改动"
