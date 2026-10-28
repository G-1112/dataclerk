"""判官 vs 人工 工具的测试（2026-10-28 新增）。

这条链路的产物是**提交材料里最容易被追问的那张表**（判官与人工的 κ），
所以测试重点不是"能跑"，而是：
- 未标注 / 判官无结论的行绝不能进分母（否则 κ 会被凑数抬高）；
- 人工标签全同类时 κ 返回 None（不硬编 1.0）；
- 导出表必须带 BOM 且 `human_faithful` 留空（否则 Excel 打开乱码、或把判官分当人工分）。
"""

from __future__ import annotations

import csv
import json

import pytest

from testsuite import judge_human_check as H


def _write_sheet(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=H.FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({**{k: "" for k in H.FIELDS}, **r})


def test_build_questions_capped_and_unique():
    qs = H.build_questions(20)
    assert len(qs) == 20
    assert len(set(qs)) == 20, "抽检题目不应重复"


def test_export_unavailable_without_llm(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.llm.llm_enabled", lambda: False)
    rep = H.export_sheet(3, tmp_path / "s.csv")
    assert rep["status"] == "unavailable"
    assert "LLM_API_KEY" in rep["reason"]


def test_export_writes_unlabeled_sheet(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.llm.llm_enabled", lambda: True)
    monkeypatch.setattr("agent.tools.retrieve_policy.retrieve_policy",
                        lambda q, top_k=3: ["《办法》规定：奖学金按学年评定。"])
    monkeypatch.setattr("agent.llm.synthesize_answer",
                        lambda q, snippets: "按学年综合测评排名评定。")
    monkeypatch.setattr("testsuite.rag_judge.judge_one",
                        lambda q, a, c: {"faithfulness": 0.8, "relevancy": 0.9})

    out = tmp_path / "sheet.csv"
    rep = H.export_sheet(4, out)
    assert rep["status"] == "ok" and rep["rows"] == 4

    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV 必须带 BOM，否则 Excel 打开中文乱码"
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig", newline="")))
    assert len(rows) == 4
    assert all(r["human_faithful"] == "" for r in rows), "人工列必须留空，不能拿判官分冒充"
    assert rows[0]["judge_faithfulness"] == "0.8"


# ── 盲标模式：标注表里不能出现判官分（否则 κ = "判官跟自己一致"） ──────────

def _stub_export(monkeypatch):
    monkeypatch.setattr("agent.llm.llm_enabled", lambda: True)
    monkeypatch.setattr("agent.tools.retrieve_policy.retrieve_policy",
                        lambda q, top_k=3: ["《办法》规定：奖学金按学年评定。"])
    monkeypatch.setattr("agent.llm.synthesize_answer",
                        lambda q, snippets: "按学年综合测评排名评定。")
    monkeypatch.setattr("testsuite.rag_judge.judge_one",
                        lambda q, a, c: {"faithfulness": 0.8, "relevancy": 0.9})


def test_export_blind_moves_judge_scores_to_sidecar(monkeypatch, tmp_path):
    _stub_export(monkeypatch)
    out = tmp_path / "blind.csv"
    rep = H.export_sheet(3, out, blind=True)
    assert rep["status"] == "ok" and rep["blind"] is True

    with out.open(encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        assert r.fieldnames == H.BLIND_FIELDS, "盲标表不得含 judge_* 列"
        rows = list(r)
    assert all("judge_faithfulness" not in row or not row["judge_faithfulness"] for row in rows)

    side = tmp_path / "blind.judge.csv"
    assert side.exists(), "判官分必须有独立落点，不能直接丢"
    with side.open(encoding="utf-8-sig", newline="") as f:
        sch = list(csv.DictReader(f))
    assert len(sch) == 3 and all(x["judge_faithfulness"] == "0.8" for x in sch)


def test_blind_sheet_scores_via_sidecar(tmp_path):
    """盲标表 + judge.csv 合并后，κ 仍能算出来。"""
    rows = [
        {"id": "H-01", "human_faithful": "1"},
        {"id": "H-02", "human_faithful": "0"},
        {"id": "H-03", "human_faithful": "1"},
    ]
    sheet = tmp_path / "b.csv"
    with sheet.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=H.BLIND_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({**{k: "" for k in H.BLIND_FIELDS}, **r})
    with (tmp_path / "b.judge.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=H.JUDGE_FIELDS)
        w.writeheader()
        w.writerows([{"id": "H-01", "judge_faithfulness": "1.0", "judge_relevancy": "0.9"},
                     {"id": "H-02", "judge_faithfulness": "0.2", "judge_relevancy": "0.9"},
                     {"id": "H-03", "judge_faithfulness": "0.9", "judge_relevancy": "0.9"}])
    rep = H.score_sheet(sheet)
    assert rep["status"] == "ok" and rep["labeled"] == 3
    assert rep["accuracy"] == pytest.approx(1.0), "三条应全对：判官分来自 sidecar"


def test_blind_sheet_without_sidecar_counts_as_missing(tmp_path):
    """没有 sidecar 就没判官分——记为"判官无结论"，绝不默认判官为忠实。"""
    sheet = tmp_path / "b.csv"
    with sheet.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=H.BLIND_FIELDS)
        w.writeheader()
        w.writerow({**{k: "" for k in H.BLIND_FIELDS}, "id": "H-01", "human_faithful": "1"})
    rep = H.score_sheet(sheet)
    assert rep["status"] == "empty" and rep["unlabeled"] == 1


def test_score_metrics_and_confusion(tmp_path):
    rows = [
        # 4 TP
        *[{"id": f"T{i}", "judge_faithfulness": "1.0", "human_faithful": "1"} for i in range(4)],
        # 4 TN
        *[{"id": f"N{i}", "judge_faithfulness": "0.0", "human_faithful": "0"} for i in range(4)],
        # 1 FP（判官说忠实，人工说不）
        {"id": "FP", "judge_faithfulness": "0.9", "human_faithful": "0"},
        # 1 FN
        {"id": "FN", "judge_faithfulness": "0.1", "human_faithful": "1"},
    ]
    sheet = tmp_path / "s.csv"
    _write_sheet(sheet, rows)
    rep = H.score_sheet(sheet)
    assert rep["status"] == "ok" and rep["labeled"] == 10
    assert rep["accuracy"] == pytest.approx(0.8)
    assert rep["precision"] == pytest.approx(0.8)
    assert rep["recall"] == pytest.approx(0.8)
    assert rep["confusion"] == {"tp": 4, "fp": 1, "fn": 1, "tn": 4}
    assert [d["id"] for d in rep["disagreements"]] == ["FP", "FN"]


def test_score_skips_unlabeled_and_missing_judge(tmp_path):
    rows = [
        {"id": "A", "judge_faithfulness": "1.0", "human_faithful": "1"},
        {"id": "B", "judge_faithfulness": "1.0", "human_faithful": ""},      # 未标注
        {"id": "C", "judge_faithfulness": "", "human_faithful": "1"},        # 判官无结论
        {"id": "D", "judge_faithfulness": "nan", "human_faithful": "0"},     # 判官解析失败
    ]
    sheet = tmp_path / "s.csv"
    _write_sheet(sheet, rows)
    rep = H.score_sheet(sheet)
    assert rep["labeled"] == 1 and rep["unlabeled"] == 3
    assert "样本仅 1 条" in " ".join(rep["notes"])


def test_score_chinese_labels_and_single_class_kappa(tmp_path):
    rows = [{"id": f"X{i}", "judge_faithfulness": "1.0", "human_faithful": "是"} for i in range(5)]
    sheet = tmp_path / "s.csv"
    _write_sheet(sheet, rows)
    rep = H.score_sheet(sheet)
    assert rep["labeled"] == 5
    assert rep["kappa"] is None, "人工全标 1 时 κ 无定义"
    assert any("κ 无定义" in n for n in rep["notes"])


def test_score_empty_sheet_reports_clearly(tmp_path):
    sheet = tmp_path / "s.csv"
    _write_sheet(sheet, [{"id": "A", "judge_faithfulness": "1.0", "human_faithful": ""}])
    rep = H.score_sheet(sheet)
    assert rep["status"] == "empty" and rep["unlabeled"] == 1


def test_missing_sheet_returns_error(tmp_path):
    rep = H.score_sheet(tmp_path / "not-exist.csv")
    assert rep["status"] == "error" and "找不到" in rep["reason"]


@pytest.mark.parametrize("raw,expect", [("1", 1), ("是", 1), ("Y", 1), ("0", 0),
                                       ("否", 0), ("n", 0), ("", None), ("也许", None)])
def test_norm_label(raw, expect):
    assert H._norm_label(raw) == expect


# ---------------------------------------------------------------------------
# 人造编造样本（测判官"抓得住"的能力）
# ---------------------------------------------------------------------------
def _mock_llm(monkeypatch, judge_score=1.0):
    monkeypatch.setattr("agent.llm.llm_enabled", lambda: True)
    monkeypatch.setattr("agent.tools.retrieve_policy.retrieve_policy",
                        lambda q, top_k=3: ["《办法》规定：奖学金按学年评定。"])
    monkeypatch.setattr("agent.llm.synthesize_answer", lambda q, s: "按学年评定。")
    monkeypatch.setattr("testsuite.rag_judge.judge_one",
                        lambda q, a, c: {"faithfulness": judge_score, "relevancy": 0.9})


def test_export_fabricates_without_leaking_marker(monkeypatch, tmp_path):
    _mock_llm(monkeypatch)
    out = tmp_path / "sheet.csv"
    rep = H.export_sheet(10, out, fabricate=3)
    assert rep["fabricated"] == 3

    rows = list(csv.DictReader(out.open(encoding="utf-8-sig", newline="")))
    injected_rows = [r for r in rows if "另外，该办法" in r["answer"]]
    assert len(injected_rows) == 3
    assert "injected" not in rows[0], "CSV 里不能有标记列，否则人工标注会被暗示"

    meta = json.loads((tmp_path / "sheet.meta.json").read_text(encoding="utf-8"))
    assert len(meta["injected"]) == 3
    assert set(meta["injected"]) == {r["id"] for r in injected_rows}


def test_score_reports_injected_detection(tmp_path):
    rows = [
        {"id": "H-01", "judge_faithfulness": "1.0", "human_faithful": "1"},
        {"id": "H-02", "judge_faithfulness": "1.0", "human_faithful": "0"},  # 人造，判官漏抓
        {"id": "H-03", "judge_faithfulness": "0.2", "human_faithful": "0"},  # 人造，判官抓到
        {"id": "H-04", "judge_faithfulness": "1.0", "human_faithful": "1"},
    ]
    sheet = tmp_path / "s.csv"
    _write_sheet(sheet, rows)
    (tmp_path / "s.meta.json").write_text(
        json.dumps({"injected": {"H-02": "编造A", "H-03": "编造B"}}, ensure_ascii=False),
        encoding="utf-8")
    rep = H.score_sheet(sheet)
    assert rep["injected"] == {"total": 2, "judge_flagged": 1, "human_flagged": 2}
    flagged = [d for d in rep["disagreements"] if d["injected"]]
    assert [d["id"] for d in flagged] == ["H-02"]


# ── 抓编造：不需要人工标注也能测（第十六轮补） ────────────────────────────

def _meta(path, ids):
    path.write_text(json.dumps({"injected": {i: f"编造{i}" for i in ids}}, ensure_ascii=False),
                    encoding="utf-8")


def test_injected_works_without_human_labels(tmp_path):
    """核心诉求：`human_faithful` 全空时 score 无分母，但抓编造有真值（我们自己注入的）。"""
    rows = [
        {"id": "H-01", "judge_faithfulness": "0.2"},   # 人造，抓到
        {"id": "H-02", "judge_faithfulness": "0.6"},   # 人造，按 0.5 漏、按 0.67 抓
        {"id": "H-03", "judge_faithfulness": "1.0"},   # 正常
    ]
    sheet = tmp_path / "s.csv"
    _write_sheet(sheet, rows)
    _meta(tmp_path / "s.meta.json", ["H-01", "H-02"])
    assert H.score_sheet(sheet)["status"] == "empty"  # 仍然没分母
    rep = H.injected_report(sheet, thresholds=(0.5, 0.67))
    assert rep["status"] == "ok"
    assert rep["injected_total"] == 2 and rep["others_total"] == 1
    assert rep["injected_scores"] == [0.2, 0.6]
    assert [r["injected_flagged"] for r in rep["sweep"]] == [1, 2]
    assert [r["detect_rate"] for r in rep["sweep"]] == [0.5, 1.0]
    assert rep["min_full_detect_threshold"] == 0.67


def test_injected_reports_cost_side(tmp_path):
    """抬阈值要报代价：非注入样本被判到阈值以下多少条（不等于误判，真值未知）。"""
    rows = [
        {"id": "H-01", "judge_faithfulness": "0.1"},
        {"id": "H-02", "judge_faithfulness": "0.5"},   # 非注入但分数不高
        {"id": "H-03", "judge_faithfulness": "0.8"},
    ]
    sheet = tmp_path / "s.csv"
    _write_sheet(sheet, rows)
    _meta(tmp_path / "s.meta.json", ["H-01"])
    rep = H.injected_report(sheet, thresholds=(0.5, 0.9))
    assert [r["others_below_threshold"] for r in rep["sweep"]] == [0, 2]
    assert any("真值未知" in n for n in rep["notes"])


def test_injected_without_meta_is_explicit(tmp_path):
    """没注入就别报 0%：直接说这张表测不了抓编造。"""
    sheet = tmp_path / "s.csv"
    _write_sheet(sheet, [{"id": "H-01", "judge_faithfulness": "0.2"}])
    rep = H.injected_report(sheet)
    assert rep["status"] == "no_injected" and "fabricate" in rep["reason"]


def test_injected_skips_rows_without_judge_score(tmp_path):
    rows = [
        {"id": "H-01", "judge_faithfulness": ""},      # 判官无结论 → 不进分母
        {"id": "H-02", "judge_faithfulness": "0.2"},
    ]
    sheet = tmp_path / "s.csv"
    _write_sheet(sheet, rows)
    _meta(tmp_path / "s.meta.json", ["H-01", "H-02"])
    rep = H.injected_report(sheet, thresholds=(0.5,))
    assert rep["injected_total"] == 1, "判官没给分的样本不能计入分母"


def test_score_empty_result_points_to_injected_cmd(tmp_path):
    """无标注时给出可执行的下一步，而不是只报空。"""
    sheet = tmp_path / "s.csv"
    _write_sheet(sheet, [{"id": "H-01", "judge_faithfulness": "1.0"}])
    rep = H.score_sheet(sheet)
    assert rep["status"] == "empty" and "injected" in rep["hint"]
