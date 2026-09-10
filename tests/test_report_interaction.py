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
