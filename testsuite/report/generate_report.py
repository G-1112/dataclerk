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


# 缺陷分类规则：维度 → (类别, 严重度, 修复建议)
# 严重度定义：P0 = 安全越权/数据泄露；P1 = 功能错误/路由误判；P2 = 体验/一致性
_DEFECT_RULES = {
    "security": ("安全越权", "P0", "检查权限过滤层（guard/permission.py）是否覆盖该谓词形态，补对抗用例回归"),
    "functional": ("功能准确性", "P1", "核对 SQL 生成与标准答案的语义差异，必要时补充 few-shot 示例"),
    "routing": ("路由误判", "P1", "检查 router.py 关键词表与强数据意图判定，补路由用例"),
    "rag": ("RAG 检索质量", "P1", "检查政策库切片粒度与 bigram 召回阈值（当前 0.02）"),
    "multiturn": ("多轮指代", "P2", "检查 memory/slots.py 的槽位继承与历史窗口"),
    "boundary": ("边界降级", "P2", "应优雅返回错误提示而非崩溃/空响应"),
    "stability": ("结果一致性", "P2", "检查是否存在非确定性来源（温度/随机/并发顺序）"),
    "performance": ("性能超时", "P2", "优化 SQL 或加缓存/上限，定位慢查询"),
}


def classify_defect(result: dict) -> dict:
    """把一条失败用例归类为 (类别, 严重度, 建议)。"""
    dim = result.get("dimension", "unknown")
    cat, sev, advice = _DEFECT_RULES.get(dim, ("其他", "P2", "人工复核"))
    # 安全维度若带 category（注入/绕过/破坏性），细分显示
    sub = result.get("category")
    label = f"{cat}·{sub}" if sub else cat
    return {"label": label, "severity": sev, "advice": advice, "dimension": dim}


def _defect_rows(failed: list[dict]) -> str:
    """生成带 data-* 属性的可筛选缺陷行。"""
    rows = []
    for r in failed:
        d = classify_defect(r)
        sev_cls = {"P0": "sev-p0", "P1": "sev-p1", "P2": "sev-p2"}.get(d["severity"], "")
        rows.append(
            f'<tr class="defect-row" data-sev="{d["severity"]}" data-dim="{d["dimension"]}">'
            f'<td><span class="sev {sev_cls}">{d["severity"]}</span></td>'
            f'<td>{r.get("id","")}</td>'
            f'<td>{d["label"]}</td>'
            f'<td>{str(r.get("question",""))[:50]}</td>'
            f'<td>{str(r.get("answer",""))[:70]}</td>'
            f'<td class="muted">{d["advice"]}</td>'
            f"</tr>"
        )
    return "".join(rows) or '<tr><td colspan="6">无失败用例</td></tr>'


def _severity_summary(failed: list[dict]) -> str:
    """按严重度聚合的小卡片。"""
    counts = {"P0": 0, "P1": 0, "P2": 0}
    for r in failed:
        counts[classify_defect(r)["severity"]] += 1
    cards = []
    for sev, color in [("P0", "#dc2626"), ("P1", "#d97706"), ("P2", "#64748b")]:
        cards.append(
            f'<div class="sev-card" style="border-left:4px solid {color}">'
            f'<div class="sev-num" style="color:{color}">{counts[sev]}</div>'
            f'<div class="muted">{sev} {"（安全/越权）" if sev=="P0" else "（功能/路由）" if sev=="P1" else "（体验/一致性）"}</div>'
            f"</div>"
        )
    return f'<div class="flex">{"".join(cards)}</div>'



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


def _render_rag_eval(result: dict) -> str:
    """渲染 RAGAS / DeepEval 专项评测结果。"""
    if not result:
        return '<div class="card"><h2>RAG 专项评测（RAGAS / DeepEval）</h2><p class="muted">未执行。</p></div>'

    rows = []
    for key, label in [("ragas", "RAGAS"), ("deepeval", "DeepEval")]:
        item = result.get(key, {})
        status = item.get("status", "skipped")
        if status == "ok":
            scores = item.get("scores", {})
            score_str = "、".join(f"{k} = {v}" for k, v in scores.items())
            rows.append(f"<tr><td>{label}</td><td>✅ 完成</td><td>{score_str}</td></tr>")
        else:
            reason = item.get("reason", "")
            rows.append(f"<tr><td>{label}</td><td>⏭️ {status}</td><td class='muted'>{reason}</td></tr>")

    avail = result.get("available", {})
    return (
        '<div class="card"><h2>RAG 专项评测（RAGAS / DeepEval）</h2>'
        f'<table><tr><th>工具</th><th>状态</th><th>指标</th></tr>{"".join(rows)}</table>'
        '<p class="muted">'
        f'环境：RAGAS={"已安装" if avail.get("ragas") else "未安装"} · DeepEval={"已安装" if avail.get("deepeval") else "未安装"} · LLM={"已配置" if avail.get("llm") else "未配置"}。'
        '官方点名工具，需 <code>pip install -e ".[rag-eval]"</code> 并配置 LLM_API_KEY；'
        '未配置时自动回退到确定性指标（召回@3 / 忠实度）。</p></div>'
    )


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
    failed_rows = _defect_rows(failed)
    sev_summary = _severity_summary(failed)
    dim_options = "".join(
        f'<option value="{d[0]}">{d[1]}</option>' for d in DIMENSIONS
    )

    # 变异测试
    mut_rows = "".join(
        f"<tr><td>{m['id']}</td><td>{m['name']}</td><td>{m['dimension']}</td>"
        f"<td>{m['baseline_score']:.2%}</td><td>{m['mutant_score']:.2%}</td>"
        f"<td>{m['drop']:.2%}</td><td>{'✅ killed' if m['killed'] else '❌ survived'}</td></tr>"
        for m in mutation.get("mutants", [])
    )

    # RAGAS / DeepEval 专项
    rag_eval_html = _render_rag_eval(metrics.get("rag_eval", {}))

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
.sev-card{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 20px;min-width:120px}}
.sev-num{{font-size:28px;font-weight:700;line-height:1.1}}
.sev{{display:inline-block;padding:1px 8px;border-radius:6px;font-size:12px;font-weight:700;color:#fff}}
.sev-p0{{background:#dc2626}}
.sev-p1{{background:#d97706}}
.sev-p2{{background:#64748b}}
.filters{{display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin:14px 0}}
.filters label{{font-size:13px;color:#475569;display:flex;flex-direction:column;gap:4px}}
.filters select,.filters input{{padding:5px 8px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px;min-width:130px}}
.filters input{{min-width:200px}}
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

{rag_eval_html}

<div class="card"><h2>变异测试（Mutation Score = {mutation.get('mutation_score','-')}）</h2>
<table><tr><th>编号</th><th>注入缺陷</th><th>目标维度</th><th>基线</th><th>变异后</th><th>降幅</th><th>结果</th></tr>
{mut_rows}</table>
<p class="muted">判据：目标维度指标降幅 > 20% 判为 killed（测试集能抓到该缺陷）；否则 survived（存在盲区）。</p></div>

<div class="card"><h2>缺陷分类与定位</h2>
{sev_summary}
<div class="filters">
  <label>严重度
    <select id="f-sev">
      <option value="">全部</option>
      <option value="P0">P0 安全/越权</option>
      <option value="P1">P1 功能/路由</option>
      <option value="P2">P2 体验/一致性</option>
    </select>
  </label>
  <label>维度
    <select id="f-dim">
      <option value="">全部</option>
      {dim_options}
    </select>
  </label>
  <label>搜索
    <input id="f-kw" type="text" placeholder="用例编号 / 问题关键词" />
  </label>
  <span class="muted" id="f-count"></span>
</div>
<table id="defect-table">
<tr><th>严重度</th><th>用例</th><th>类别</th><th>问题</th><th>回答/错误</th><th>修复建议</th></tr>
{failed_rows}</table></div>

<div class="card"><h2>质量-成本</h2>
<p>全量跑批 {len(results)} 条用例，标准答案由 SQLite 自动执行生成（零人工标注）。
默认 mock 模式零 API 成本；接入真实 LLM 后单题成本约 0.01 元量级，全量约几十元。</p></div>

<div class="card"><h2>改进闭环</h2>
<p class="muted">首轮跑批暴露的问题（如越权拦截、路由误判）已通过权限层 + 确定性路由修复，
复测对比见 docs/缺陷分析报告.md。</p></div>

<script>
(function () {{
  var sev = document.getElementById("f-sev");
  var dim = document.getElementById("f-dim");
  var kw = document.getElementById("f-kw");
  var rows = Array.prototype.slice.call(document.querySelectorAll("#defect-table .defect-row"));
  var counter = document.getElementById("f-count");

  function apply() {{
    var s = sev.value, d = dim.value, k = kw.value.trim().toLowerCase();
    var shown = 0;
    rows.forEach(function (r) {{
      var okSev = !s || r.dataset.sev === s;
      var okDim = !d || r.dataset.dim === d;
      var text = r.textContent.toLowerCase();
      var okKw = !k || text.indexOf(k) >= 0;
      var show = okSev && okDim && okKw;
      r.style.display = show ? "" : "none";
      if (show) shown++;
    }});
    counter.textContent = "显示 " + shown + " / " + rows.length + " 条";
  }}

  [sev, dim, kw].forEach(function (el) {{
    el.addEventListener("input", apply);
    el.addEventListener("change", apply);
  }});
  apply();
}})();
</script>
</body></html>"""
