"""测试报告缺陷分类与交互筛选（第二轮交互增强）。

覆盖：
- 各维度失败用例被正确归类到 (类别, 严重度, 建议)；
- 安全维度的子类别（注入/绕过/破坏性）拼进标签；
- HTML 报告包含可筛选的缺陷行与筛选控件；
- 报告可重复渲染且无未转义的花括号残留。
"""

from __future__ import annotations

from testsuite.report.generate_report import (
    DIMENSIONS,
    classify_defect,
    render_html,
)


def _metrics() -> dict:
    return {d[0]: {"score": 0.9} for d in DIMENSIONS}


def _mutation() -> dict:
    return {"mutation_score": "7/7", "mutants": []}


def test_security_defect_is_p0():
    d = classify_defect({"dimension": "security", "category": "绕过"})
    assert d["severity"] == "P0"
    assert "绕过" in d["label"]
    assert d["advice"]


def test_routing_defect_is_p1():
    assert classify_defect({"dimension": "routing"})["severity"] == "P1"


def test_boundary_defect_is_p2():
    assert classify_defect({"dimension": "boundary"})["severity"] == "P2"


def test_unknown_dimension_falls_back():
    d = classify_defect({"dimension": "nope"})
    assert d["label"] == "其他"
    assert d["severity"] == "P2"


def test_report_contains_filter_controls_and_rows():
    results = [
        {"id": "D4-009", "dimension": "security", "category": "绕过", "passed": False,
         "question": "查全部学生", "answer": "越权返回"},
        {"id": "R-003", "dimension": "routing", "passed": False,
         "question": "转专业流程", "answer": "走了SQL"},
        {"id": "F-001", "dimension": "functional", "passed": True},
    ]
    html = render_html(_metrics(), _mutation(), results)
    # 2 条失败行 + 1 处 JS 选择器（#defect-table .defect-row）
    assert html.count('class="defect-row"') == 2
    assert 'id="f-sev"' in html and 'id="f-dim"' in html and 'id="f-kw"' in html
    assert "sev-p0" in html and "sev-p1" in html
    # 通过的用例不应出现为缺陷行
    defect_section = html.split('id="defect-table"')[1]
    assert "F-001" not in defect_section


def test_report_has_no_unrendered_braces():
    html = render_html(_metrics(), _mutation(), [])
    # f-string 转义错误会残留 {{
    assert "{{" not in html
    assert "}}" not in html


def test_report_renders_with_no_defects():
    html = render_html(_metrics(), _mutation(), [{"id": "F-001", "dimension": "functional", "passed": True}])
    assert "无失败用例" in html


# ---------------------------------------------------------------------------
# 第三轮交互增强：复现命令 + 分项通过率
# ---------------------------------------------------------------------------
def test_report_includes_repro_command_for_failed_case():
    """失败用例必须带上可直接粘贴运行的复现命令。"""
    results = [{"id": "D4-009", "dimension": "security", "passed": False,
                "question": "UNION 越权", "answer": "拒绝：检测到 UNION"}]
    html = render_html(_metrics(), _mutation(), results)
    assert "run_suite --case D4-009" in html
    assert 'class="cmd"' in html, "复现命令缺少可点击样式"


def test_report_includes_clipboard_script():
    html = render_html(_metrics(), _mutation(), [])
    assert "clipboard" in html
    assert "execCommand" in html, "缺少离线兜底复制实现"


def test_report_includes_dimension_pass_table():
    results = [
        {"id": "F-001", "dimension": "functional", "passed": True},
        {"id": "F-002", "dimension": "functional", "passed": True},
        {"id": "S-001", "dimension": "security", "passed": False},
    ]
    html = render_html(_metrics(), _mutation(), results)
    assert "分项通过率" in html
    # functional 2/2 = 100%，security 0/1 = 0%
    assert "2/2" in html and "0/1" in html


def test_dimension_pass_table_sorted_by_weakest_first():
    """分项表按通过率升序，最弱维度排最前，便于优先关注。"""
    from testsuite.report.generate_report import _dimension_pass_table

    results = [
        {"id": "a", "dimension": "routing", "passed": True},
        {"id": "b", "dimension": "routing", "passed": True},
        {"id": "c", "dimension": "routing", "passed": True},
        {"id": "d", "dimension": "security", "passed": False},
    ]
    table = _dimension_pass_table(results)
    # security(0%) 必须出现在 routing(100%) 之前
    assert table.index("安全") < table.index("路由") or table.index("security") < table.index("routing")


def test_repro_command_matches_cli():
    """报告中的复现命令必须是真实可用的 CLI 形式。"""
    from testsuite.run_suite import repro_command

    cmd = repro_command("D4-010")
    assert cmd == "python -m testsuite.run_suite --case D4-010"


def test_case_index_covers_all_categories():
    """单用例复现依赖 case_index 覆盖各类别。"""
    from testsuite.run_suite import case_index

    idx = case_index()
    kinds = {kind for kind, _case in idx.values()}
    for expected in ("functional", "security", "routing", "rag", "multiturn", "boundary", "stability"):
        assert expected in kinds, f"case_index 缺少类别 {expected}"
    # 官方对抗用例必须可被单独复现
    assert "D4-009" in idx and "D7-010" in idx


def test_run_single_case_unknown_id_returns_none():
    from agent.graph import CampusAgent
    from testsuite.run_suite import run_single_case

    assert run_single_case("NOT-A-REAL-ID", CampusAgent()) is None

