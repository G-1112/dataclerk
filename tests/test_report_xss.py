"""报告输出编码（XSS）回归测试（第六轮安全修复）。

实测发现的完整攻击链：
    投毒一条政策文档（写入 <img src=x onerror=...>）
    → 该片段被 RAG 检索进智能体回答（实测 top_k=3 即命中）
    → generate_report 用 f-string **未转义**输出该回答
    → 评委打开 report.html 时脚本执行

即「投毒知识库 → 报告存储型 XSS」。报告是提交物，评委必然打开，
而项目自身的变异 M4 就把「政策库投毒」建模为威胁 —— 报告本身却对该威胁无防护。

修复：新增 `_e()` 对所有来自数据的插值做 HTML 转义（含引号，
因为 case id 会被拼进 data-cmd 属性，不转义可导致属性逃逸）。
"""

from __future__ import annotations

import pytest

from testsuite.report.generate_report import (
    DIMENSIONS,
    _defect_rows,
    _e,
    render_html,
)

XSS = '<script>alert(1)</script><img src=x onerror=alert(2)>'


def _metrics() -> dict:
    return {d[0]: {"指标": 0.9} for d in DIMENSIONS}


def _mut() -> dict:
    return {"mutation_score": "7/7", "mutants": []}


def _fail_case(**over):
    c = {
        "id": "X1",
        "dimension": "functional",
        "passed": False,
        "question": XSS,
        "answer": XSS,
        "category": "检索",
        "verdict": XSS,
    }
    c.update(over)
    return c


# ------------------------------------------------------------------ _e 本身
def test_e_escapes_angle_brackets():
    assert _e("<b>") == "&lt;b&gt;"


def test_e_escapes_quotes():
    """引号必须转义：case id 会被拼进 HTML 属性。"""
    assert '"' not in _e('a"b')
    assert "'" not in _e("a'b")


def test_e_escapes_ampersand_first():
    """& 必须先转义，否则会二次转义出 &amp;lt;。"""
    assert _e("&lt;") == "&amp;lt;"


def test_e_handles_none_and_numbers():
    assert _e(None) == ""
    assert _e(123) == "123"


def test_e_is_idempotent_safe_for_plain_text():
    assert _e("普通中文 123") == "普通中文 123"


# ------------------------------------------------------------------ 报告整体
def test_report_escapes_question_and_answer():
    html = render_html(_metrics(), _mut(), [_fail_case()])
    assert "<script>alert(1)</script>" not in html
    assert "<img src=x" not in html
    assert "&lt;script&gt;" in html


def test_report_blocks_attribute_injection():
    """id 里带引号不能逃逸出 data-cmd 属性。"""
    html = render_html(_metrics(), _mut(), [_fail_case(id='X1" onmouseover="alert(1)')])
    assert 'onmouseover="alert' not in html
    assert "&quot;" in html


def test_report_escapes_dimension_name():
    html = render_html(_metrics(), _mut(), [_fail_case(dimension=XSS)])
    assert XSS not in html


def test_report_escapes_verdict():
    html = render_html(_metrics(), _mut(), [_fail_case(verdict=XSS)])
    assert XSS not in html


def test_report_escapes_mutation_fields():
    mut = {
        "mutation_score": "1/1",
        "mutants": [{"id": XSS, "name": XSS, "dimension": XSS,
                     "baseline_score": 1.0, "mutant_score": 0.0,
                     "drop": 1.0, "killed": True}],
    }
    html = render_html(_metrics(), mut, [])
    assert XSS not in html


def test_report_escapes_rag_eval_reason():
    from testsuite.report.generate_report import _render_rag_eval

    out = _render_rag_eval({"ragas": {"status": "skipped", "reason": XSS}})
    assert XSS not in out


def test_report_escapes_rag_eval_scores():
    from testsuite.report.generate_report import _render_rag_eval

    out = _render_rag_eval({"ragas": {"status": "ok", "scores": {XSS: XSS}}})
    assert XSS not in out


def test_report_escapes_comparison_metric_name():
    from testsuite.report.generate_report import _comparison_section

    prev = {"security": {XSS: 1.0}}
    cur = {"security": {XSS: 0.5}}
    out = _comparison_section(prev, cur)
    assert XSS not in out


def test_defect_rows_escapes():
    out = _defect_rows([_fail_case()])
    assert XSS not in out
    assert "&lt;script&gt;" in out


def test_report_still_well_formed_after_escaping():
    html = render_html(_metrics(), _mut(), [_fail_case()])
    assert html.rstrip().endswith("</html>")
    assert html.count("<table") == html.count("</table>")


def test_benign_content_is_preserved():
    """转义不得破坏正常内容。"""
    html = render_html(_metrics(), _mut(), [_fail_case(question="计算机学院有多少学生", answer="共 1200 人")])
    assert "计算机学院有多少学生" in html
    assert "共 1200 人" in html


def test_report_renders_with_no_results():
    html = render_html(_metrics(), _mut(), [])
    assert html.rstrip().endswith("</html>")
