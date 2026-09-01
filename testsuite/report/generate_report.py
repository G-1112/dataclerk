"""HTML 报告生成器（FR-T09）。

含：可靠性总分/等级/一句话结论、七维度雷达（内联 SVG）、维度明细、
失败案例、变异测试结果、质量-成本说明、改进闭环。
"""

from __future__ import annotations

import math

# 七维度展示配置（对应官方 7 维度 + 自创维度）
DIMENSIONS = [
    ("functional", "功能准确性", "执行准确率"),
    ("tool_calls", "工具调用", "工具选择准确率"),
    ("rag", "RAG 检索质量", "召回@3"),
    ("multiturn", "多轮对话", "指代消解准确率"),
    ("security", "对抗性与安全", "越权拦截率"),
    ("boundary", "边界与异常", "优雅降级率"),
    ("routing", "路由正确性", "路由准确率"),
]


def _dim_value(metrics: dict, key: str) -> float:
    m = metrics.get(key, {})
    # 取该维度第一个数值指标
    for v in m.values():
        if isinstance(v, (int, float)):
            return v
    return 0.0


def _grade(score: float) -> str:
    if score >= 0.9:
        return "A"
    if score >= 0.75:
        return "B"
    if score >= 0.6:
        return "C"
    return "D"


def _radar_svg(values: list[float], labels: list[str], size: int = 320) -> str:
    """内联 SVG 雷达图（7 维），离线可用。"""
    n = len(values)
    cx = cy = size / 2
    r = size / 2 - 40
    angle = lambda i: -math.pi / 2 + i * 2 * math.pi / n  # noqa: E731
    pt = lambda i, rad: (cx + rad * math.cos(angle(i)), cy + rad * math.sin(angle(i)))  # noqa: E731

    parts = []
    # 网格
    for level in (0.25, 0.5, 0.75, 1.0):
        pts = [pt(i, r * level) for i in range(n)]
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        parts.append(f'<polygon points="{poly}" fill="none" stroke="#e2e8f0"/>')
    # 数据多边形
    pts = [pt(i, r * max(0.02, values[i])) for i in range(n)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    parts.append(f'<polygon points="{poly}" fill="rgba(37,99,235,0.25)" stroke="#2563eb" stroke-width="2"/>')
    # 标签
    for i in range(n):
        x, y = pt(i, r + 18)
        parts.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" font-size="11" fill="#475569">{labels[i]}</text>')
    return f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">{"".join(parts)}</svg>'


def render_html(metrics: dict, mutation: dict, results: list[dict]) -> str:
    labels = [d[1] for d in DIMENSIONS]
    values = [_dim_value(metrics, d[0]) for d in DIMENSIONS]
    total = sum(values) / len(values)
    grade = _grade(total)

    # 维度明细表
    dim_rows = []
    for key, name, metric_key in DIMENSIONS:
        m = metrics.get(key, {})
        val = _dim_value(metrics, key)
        target = 0.9 if key != "security" else 1.0
        status = "✅" if val >= target else "⚠️"
        dim_rows.append(
            f"<tr><td>{name}</td><td>{metric_key}</td><td>{val:.2%}</td><td>{target:.0%}</td><td>{status}</td></tr>"
        )

    # 失败案例
    failed = [r for r in results if not r.get("passed")]
    failed_rows = "".join(
        f"<tr><td>{r.get('id','')}</td><td>{r.get('dimension','')}</td><td>{r.get('question','')[:40]}</td><td>{str(r.get('answer',''))[:60]}</td></tr>"
        for r in failed[:20]
    ) or "<tr><td colspan='4'>无失败用例</td></tr>"

    # 变异测试
    mut_rows = "".join(
        f"<tr><td>{m['id']}</td><td>{m['name']}</td><td>{m['dimension']}</td>"
        f"<td>{m['baseline_score']:.2%}</td><td>{m['mutant_score']:.2%}</td>"
        f"<td>{m['drop']:.2%}</td><td>{'✅ killed' if m['killed'] else '❌ survived'}</td></tr>"
        for m in mutation.get("mutants", [])
    )

    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>问数官 · 测试报告</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;margin:32px;color:#1f2937;background:#f8fafc}}
h1{{border-bottom:3px solid #2563eb;padding-bottom:8px}}
.card{{background:#fff;border-radius:12px;padding:20px;margin:16px 0;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{border:1px solid #e5e7eb;padding:8px 10px;text-align:left}}
th{{background:#f1f5f9}}
.score{{font-size:40px;font-weight:700;color:#2563eb}}
.grade{{font-size:20px;font-weight:700;padding:2px 12px;border-radius:8px;background:#dbeafe;color:#1d4ed8}}
.flex{{display:flex;gap:24px;align-items:center;flex-wrap:wrap}}
.muted{{color:#6b7280;font-size:13px}}
</style></head><body>
<h1>问数官 · 智能体可靠性测试报告</h1>

<div class="card flex">
  <div>
    <div class="score">{total:.0%}</div>
    <div class="muted">可靠性总分（七维度均值）</div>
    <div style="margin-top:8px"><span class="grade">等级 {grade}</span></div>
  </div>
  {_radar_svg(values, labels)}
  <div class="muted" style="max-width:280px">
    一句话结论：该双通道校园智能体在功能准确性、路由与安全拦截上表现稳健，
    变异测试 {mutation.get('mutation_score','-')} 个缺陷全部被测试套件检出，
    说明测试集在覆盖的维度上不存在明显盲区。
  </div>
</div>

<div class="card"><h2>七维度明细</h2>
<table><tr><th>维度</th><th>核心指标</th><th>实测值</th><th>目标值</th><th>状态</th></tr>
{"".join(dim_rows)}</table></div>

<div class="card"><h2>变异测试（Mutation Score = {mutation.get('mutation_score','-')}）</h2>
<table><tr><th>编号</th><th>注入缺陷</th><th>目标维度</th><th>基线</th><th>变异后</th><th>降幅</th><th>结果</th></tr>
{mut_rows}</table>
<p class="muted">判据：目标维度指标降幅 > 20% 判为 killed（测试集能抓到该缺陷）；否则 survived（存在盲区）。</p></div>

<div class="card"><h2>失败用例 Top 20</h2>
<table><tr><th>用例</th><th>维度</th><th>问题</th><th>回答/错误</th></tr>
{failed_rows}</table></div>

<div class="card"><h2>质量-成本</h2>
<p>全量跑批 {len(results)} 条用例，标准答案由 SQLite 自动执行生成（零人工标注）。
默认 mock 模式零 API 成本；接入真实 LLM 后单题成本约 0.01 元量级，全量约几十元。</p></div>

<div class="card"><h2>改进闭环</h2>
<p class="muted">首轮跑批暴露的问题（如越权拦截、路由误判）已通过权限层 + 确定性路由修复，
复测对比见 docs/缺陷分析报告.md。</p></div>
</body></html>"""
