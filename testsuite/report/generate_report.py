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


def _e(value) -> str:
    """HTML 转义（第六轮安全修复）。

    为什么必须转义：本报告由 f-string 拼接而成，其中 question / answer 等字段
    来自**被测智能体的输出**，而智能体输出又会包含知识库（RAG）与数据库的内容。
    实测完整链路：
        投毒一条政策文档写入 <img src=x onerror=...>
        → 该片段被检索进智能体回答
        → 报告未转义地输出该回答
        → 评委打开 report.html 时脚本执行
    即「投毒知识库 → 报告存储型 XSS」。报告是提交物，评委一定会打开，故必须转义。

    注意：同时转义引号，因为 case id 还会被拼进 HTML 属性
    （data-cmd="..."），不转义可导致属性逃逸注入。
    """
    s = "" if value is None else str(value)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


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


def _repro_command(case_id: str) -> str:
    """生成失败用例的一键复现命令（与 run_suite --case 对应）。"""
    return f"python -m testsuite.run_suite --case {case_id}"


def _defect_rows(failed: list[dict]) -> str:
    """生成带 data-* 属性的可筛选缺陷行，并给出复现命令。"""
    rows = []
    for r in failed:
        d = classify_defect(r)
        sev_cls = {"P0": "sev-p0", "P1": "sev-p1", "P2": "sev-p2"}.get(d["severity"], "")
        cid = r.get("id", "")
        # 多变体生成的用例 ID（如 multiturn 的 xxx-问题）取原始前缀
        base_id = cid.split("-")[0] + "-" + cid.split("-")[1] if cid.count("-") >= 1 else cid
        cmd = _repro_command(base_id)
        rows.append(
            f'<tr class="defect-row" data-sev="{_e(d["severity"])}" data-dim="{_e(d["dimension"])}">'
            f'<td><span class="sev {sev_cls}">{_e(d["severity"])}</span></td>'
            f'<td>{_e(cid)}</td>'
            f'<td>{_e(d["label"])}</td>'
            f'<td>{_e(str(r.get("question",""))[:50])}</td>'
            f'<td>{_e(str(r.get("answer",""))[:70])}</td>'
            f'<td class="muted">{_e(d["advice"])}</td>'
            f'<td><code class="cmd" title="点击复制" data-cmd="{_e(cmd)}">{_e(cmd)}</code></td>'
            f"</tr>"
        )
    return "".join(rows) or '<tr><td colspan="7">无失败用例</td></tr>'


def _dimension_pass_table(results: list[dict]) -> str:
    """按维度统计用例通过率（分项一览），让「哪些维度弱」一眼可见。"""
    agg: dict[str, list[int]] = {}
    for r in results:
        dim = r.get("dimension") or "unknown"
        bucket = agg.setdefault(dim, [0, 0])
        bucket[1] += 1
        if r.get("passed"):
            bucket[0] += 1

    label_map = {k: v[1] for k, v in zip([d[0] for d in DIMENSIONS], DIMENSIONS)}
    rows = []
    for dim, (ok, total) in sorted(agg.items(), key=lambda kv: (kv[1][0] / kv[1][1]) if kv[1][1] else 0):
        rate = ok / total if total else 0
        bar_w = int(rate * 100)
        color = "#16a34a" if rate >= 0.95 else ("#d97706" if rate >= 0.8 else "#dc2626")
        rows.append(
            f"<tr><td>{_e(label_map.get(dim, dim))}</td><td>{ok}/{total}</td><td>{rate:.1%}</td>"
            f'<td><div class="bar"><div class="bar-fill" style="width:{bar_w}%;background:{color}"></div></div></td></tr>'
        )
    return "".join(rows) or "<tr><td colspan='4'>无用例</td></tr>"



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
            score_str = "、".join(f"{_e(k)} = {_e(v)}" for k, v in scores.items())
            rows.append(f"<tr><td>{label}</td><td>✅ 完成</td><td>{score_str}</td></tr>")
        else:
            reason = item.get("reason", "")
            rows.append(f"<tr><td>{label}</td><td>⏭️ {_e(status)}</td><td class='muted'>{_e(reason)}</td></tr>")

    avail = result.get("available", {})
    return (
        '<div class="card"><h2>RAG 专项评测（RAGAS / DeepEval）</h2>'
        f'<table><tr><th>工具</th><th>状态</th><th>指标</th></tr>{"".join(rows)}</table>'
        '<p class="muted">'
        f'环境：RAGAS={"已安装" if avail.get("ragas") else "未安装"} · DeepEval={"已安装" if avail.get("deepeval") else "未安装"} · LLM={"已配置" if avail.get("llm") else "未配置"}。'
        '官方点名工具，需 <code>pip install -e ".[rag-eval]"</code> 并配置 LLM_API_KEY；'
        '未配置时自动回退到确定性指标（召回@3 / 忠实度）。</p></div>'
    )


def _comparison_section(prev_metrics: dict | None, metrics: dict) -> str:
    """与上一次跑批对比（第四轮）：标出回归与提升。

    测试套件的价值不止于「这次多少分」，更在于「比上次退步了吗」。
    """
    if not prev_metrics:
        return (
            '<div class="card"><h2>与上次对比</h2>'
            '<p class="muted">首次跑批，暂无可对比的历史指标。'
            '再次运行 <code>python -m testsuite.run_suite</code> 即会自动与本次结果对比。</p></div>'
        )

    from .. import metrics as metrics_mod

    deltas = metrics_mod.compare_runs(prev_metrics, metrics)
    summary = metrics_mod.regression_summary(deltas)
    changed = [d for d in deltas if d["direction"] != "same"]

    if not changed:
        body = '<p class="muted">各项指标与上次完全一致，无回归也无提升。</p>'
    else:
        rows = []
        # 回归优先展示
        ordered = sorted(changed, key=lambda d: 0 if d["direction"] == "down" else 1)
        for d in ordered:
            if d["direction"] == "down":
                mark, cls = "↓ 回归", "sev-p0"
            else:
                mark, cls = "↑ 提升", "sev-p2"
            metric_name = d["metric"]
            if d.get("lower_is_better"):
                metric_name += "（越小越好）"
            rows.append(
                f"<tr><td>{_e(d['dimension'])}</td><td>{_e(metric_name)}</td>"
                f"<td>{_e(d['prev'])}</td><td>{_e(d['cur'])}</td>"
                f"<td>{d['delta']:+.4f}</td>"
                f'<td><span class="sev {cls}">{mark}</span></td></tr>'
            )
        body = (
            '<table><tr><th>维度</th><th>指标</th><th>上次</th><th>本次</th><th>变化</th><th>判定</th></tr>'
            + "".join(rows)
            + "</table>"
        )

    verdict = (
        f'<p class="muted">提升 {summary["improvement_count"]} 项，'
        f'回归 {summary["regression_count"]} 项。'
        + (
            "<b style='color:#dc2626'>检测到回归，建议排查后再提交。</b>"
            if summary["has_regression"]
            else "未检测到回归。"
        )
        + "</p>"
        + '<p class="muted">注：判定按「好坏方向」而非数值方向 —— 时延类指标（越小越好）'
        '数值下降记为提升；「用例数」「平均步数」属规模/行为指标，不计入回归判定。</p>'
    )
    return f'<div class="card"><h2>与上次对比</h2>{body}{verdict}</div>'


def _trace_section(results: list[dict], failed: list[dict]) -> str:
    """思考-行动链路审计区块（第四轮新增）。

    对应赛道三路径二「智能体行为可视化与审计平台：记录并可视化智能体的
    思考-行动链路，辅助缺陷定位」。优先展示失败用例（最需要定位），
    失败不足时用通过用例补足，保证报告里总能看到链路样本。
    """
    from . import trace_view as tv

    picks = list(failed)
    if len(picks) < 3:
        picks += [r for r in results if r.get("passed")][: 5 - len(picks)]
    return tv.render_audit_html(picks, limit=5)


# 思考-行动链路时间线样式（与 trace_view.TRACE_VIEW_CSS 同一套）。
# 注意：本报告模板是 f-string，但**被替换进去的值不会被再次解析花括号**，
# 因此这里必须原样返回 CSS；若在此处转义成 {{ }} 反而会在输出里留下双花括号
# （实测踩坑：曾误以为替换值也会走 f-string 转义）。
def _trace_css_for_report() -> str:
    from .trace_view import TRACE_VIEW_CSS

    return TRACE_VIEW_CSS


def render_html(
    metrics: dict, mutation: dict, results: list[dict], prev_metrics: dict | None = None
) -> str:
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
    dim_pass_rows = _dimension_pass_table(results)
    dim_options = "".join(
        f'<option value="{d[0]}">{d[1]}</option>' for d in DIMENSIONS
    )

    # 变异测试
    mut_rows = "".join(
        f"<tr><td>{_e(m['id'])}</td><td>{_e(m['name'])}</td><td>{_e(m['dimension'])}</td>"
        f"<td>{m['baseline_score']:.2%}</td><td>{m['mutant_score']:.2%}</td>"
        f"<td>{m['drop']:.2%}</td><td>{'✅ killed' if m['killed'] else '❌ survived'}</td></tr>"
        for m in mutation.get("mutants", [])
    )

    # RAGAS / DeepEval 专项
    rag_eval_html = _render_rag_eval(metrics.get("rag_eval", {}))

    # 功能准确性双口径（第十四轮）：严格口径是主指标，语义等价只做补充说明。
    # 只在实际跑出该字段时才渲染，避免旧版 metrics.json 缺字段时报告出现空白行。
    dual_html = ""
    fm = metrics.get("functional", {})
    if "语义等价准确率" in fm:
        rescued = fm.get("严格判错但语义等价", 0)
        dual_html = (
            '<div class="card"><h2>功能准确性 · 双口径（严格为主，语义等价为辅）</h2>'
            '<table><tr><th>口径</th><th>实测</th><th>含义</th></tr>'
            f'<tr><td>严格（结果集逐字节一致）</td><td><b>{fm.get("执行准确率", 0):.2%}</b></td>'
            '<td>行序无关的多重集精确比对，主指标</td></tr>'
            f'<tr><td>语义等价（数值容差 / 列对齐 / 集合语义）</td><td>{fm.get("语义等价准确率", 0):.2%}</td>'
            '<td>允许"同样对、写法不同"，仅作补充</td></tr>'
            f'<tr><td>严格判错但语义等价</td><td>{rescued} 条</td>'
            '<td>若为 0，说明失败不是"写法不同"造成的</td></tr>'
            f'<tr><td>SQL 可执行率</td><td>{fm.get("SQL可执行率", 0):.2%}</td>'
            '<td>生成的 SQL 能否跑通（区分"写错"与"没写"）</td></tr>'
            '</table>'
            '<p class="muted">默认只放开数值容差；"多选列/重复行/少几行"必须由用例显式声明 '
            '`equiv` 才放宽，且<b>不允许默认放行"少列"</b>——按学院统计却丢了学院名，行就不可判读。'
            '两个数都摆出来，谁也不能替谁说话。<br>'
            '命令：<code>python -m testsuite.run_suite</code>（严格口径不变，语义口径见 metrics.json 的 '
            '<code>functional.语义等价准确率</code>）。</p></div>'
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
.bar{{background:#e2e8f0;border-radius:6px;height:12px;width:140px;overflow:hidden;display:inline-block;vertical-align:middle}}
.bar-fill{{height:100%;border-radius:6px}}
code.cmd{{background:#f1f5f9;border:1px solid #cbd5e1;border-radius:6px;padding:2px 8px;cursor:pointer;font-size:12px;white-space:nowrap;display:inline-block}}
code.cmd:hover{{background:#e0e7ff;border-color:#818cf8}}
code.cmd.copied{{background:#dcfce7;border-color:#4ade80}}
code.cmd.copied::after{{content:" ✓ 已复制";color:#16a34a;font-weight:700}}
{_trace_css_for_report()}
</style></head><body>
<h1>问数官 · 智能体可靠性测试报告（mock 轨道 · 框架回归基线）</h1>

<div class="card flex">
  <div>
    <div class="score">{total:.0%}</div>
    <div class="muted">可靠性总分（七维度均值）</div>
    <div style="margin-top:8px"><span class="grade">等级 {grade}</span></div>
  </div>
  {_radar_svg(values, labels)}
  <div class="muted" style="max-width:280px">
    <b>双口径声明</b>：本报告由 mock 轨道（确定性 mock SQL 生成器 + 关键词路由）产出，
    定位是<b>框架回归基线</b>——用例与生成器共设计，预期全绿，用于回归与变异注入的机械验证，
    <b>1.0 的通过率不是成绩</b>。真实 LLM 主评测口径见 <code>data/report/metrics_llm.json</code>
    与 <code>llm_track.md</code>（deepseek-chat 严格口径 93.2%）。<br>
    一句话结论：变异测试 {mutation.get('mutation_score','-')} 个注入缺陷全部被测试套件检出，
    说明测试集在覆盖的维度上不存在明显盲区。
  </div>
</div>

<div class="card"><h2>七维度明细</h2>
<table><tr><th>维度</th><th>核心指标</th><th>实测值</th><th>目标值</th><th>状态</th></tr>
{"".join(dim_rows)}</table></div>

{dual_html}

{rag_eval_html}

<div class="card"><h2>变异测试（Mutation Score = {mutation.get('mutation_score','-')}）</h2>
<table><tr><th>编号</th><th>注入缺陷</th><th>目标维度</th><th>基线</th><th>变异后</th><th>降幅</th><th>结果</th></tr>
{mut_rows}</table>
<p class="muted">判据：目标维度指标降幅 > 20% 判为 killed（测试集能抓到该缺陷）；否则 survived（存在盲区）。</p></div>

<div class="card"><h2>分项通过率</h2>
<table><tr><th>维度</th><th>通过/总数</th><th>通过率</th><th>分布</th></tr>
{dim_pass_rows}</table>
<p class="muted">按通过率升序排列，最上面的即当前最弱维度。</p></div>

{_comparison_section(prev_metrics, metrics)}

{_trace_section(results, failed)}

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
<tr><th>严重度</th><th>用例</th><th>类别</th><th>问题</th><th>回答/错误</th><th>修复建议</th><th>复现命令</th></tr>
{failed_rows}</table>
<p class="muted">点击「复现命令」即可复制，粘贴到终端可单独重跑该用例：
<code>python -m testsuite.run_suite --list-cases</code> 可列出全部用例 ID。</p></div>

<div class="card"><h2>质量-成本</h2>
<p>全量跑批 {len(results)} 条用例，标准答案由 SQLite 自动执行生成（零人工标注）。
默认 mock 模式零 API 成本；接入真实 LLM 后单题成本约 0.01 元量级，全量约几十元。</p>
<p class="muted">口径提醒：本页所有通过率均为 mock 轨道（框架回归基线）读数；
对外引用的成绩应以真实 LLM 轨道（<code>metrics_llm.json</code>，deepseek-chat 93.2% 严格口径）为准。</p></div>

<div class="card"><h2>改进闭环</h2>
<p class="muted">首轮跑批暴露的问题（如越权拦截、路由误判）已通过权限层 + 确定性路由修复，
复测对比见 docs/缺陷分析报告.md；首轮 mock 轨道快照保留在
<code>data/report/metrics_round1.json</code> 作为改进闭环证据。</p></div>

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

  // 复现命令一键复制
  document.querySelectorAll("code.cmd").forEach(function (el) {{
    el.addEventListener("click", function () {{
      var text = el.dataset.cmd || el.textContent;
      function done() {{
        el.classList.add("copied");
        setTimeout(function () {{ el.classList.remove("copied"); }}, 1200);
      }}
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(text).then(done, function () {{}});
      }} else {{
        // 离线/非安全上下文兜底
        var ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        try {{ document.execCommand("copy"); done(); }} catch (e) {{}}
        document.body.removeChild(ta);
      }}
    }});
  }});
}})();
</script>
</body></html>"""
