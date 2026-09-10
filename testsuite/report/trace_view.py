"""智能体行为可视化与审计（FR-T12，第四轮新增）。

对应赛道三「路径二：智能体测试工具开发」明确要求的能力：
    「智能体行为可视化与审计平台：记录并可视化智能体的『思考-行动』链路，
      辅助缺陷定位」

在此之前 trace 只以 JSONL 落盘、并经 /trace 接口原样返回，
**没有任何可视化**，缺陷定位需要人工逐行读日志。本模块把链路渲染成
可直接看的 HTML 时间线，并自动标注三类可疑步骤：

    - 观察为空        observation_summary 为空 → 工具没返回可用信息
    - 高延迟          latency_ms 超过阈值 → 性能异常点
    - 意外工具        该用例期望的工具集合中未包含 → 可能选错工具

这三类标注正是「辅助缺陷定位」的落点：把可疑步骤直接标出来，
而不只是把日志换个排版。
"""

from __future__ import annotations

import json
from pathlib import Path

TRACE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "traces"

# 可疑步骤判定阈值
SLOW_MS = 500
EMPTY_OBS_LEN = 2  # 观察摘要短于此长度视为「空」

# 双通道各自「合理」的工具集合，用于发现意外工具
CHANNEL_TOOLS = {
    "sql": {"get_schema", "search_values", "execute_sql", "clarify"},
    "rag": {"retrieve_policy", "synthesize", "clarify"},
    "clarify": {"clarify"},
}


def load_trace(trace_id: str, trace_dir: str | Path | None = None) -> list[dict]:
    """读取单个 trace 的步骤序列（按 step 升序）。"""
    d = Path(trace_dir or TRACE_DIR)
    path = d / f"{trace_id}.jsonl"
    if not path.exists():
        return []
    steps = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            steps.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return sorted(steps, key=lambda s: s.get("step", 0))


def list_traces(trace_dir: str | Path | None = None, limit: int = 0) -> list[str]:
    """列出 trace_id（按文件修改时间从新到旧）。"""
    d = Path(trace_dir or TRACE_DIR)
    if not d.exists():
        return []
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    ids = [p.stem for p in files]
    return ids[:limit] if limit else ids


def flag_step(step: dict, expected_tools: set[str] | None = None) -> list[str]:
    """标注可疑步骤；返回标记列表（空表示正常）。"""
    flags: list[str] = []
    obs = (step.get("observation_summary") or "").strip()
    if len(obs) < EMPTY_OBS_LEN:
        flags.append("观察为空")
    if int(step.get("latency_ms") or 0) >= SLOW_MS:
        flags.append("高延迟")
    if expected_tools and step.get("tool") not in expected_tools:
        flags.append("意外工具")
    return flags


def analyze_trace(steps: list[dict], channel: str = "") -> dict:
    """汇总一条链路的可审计信息。"""
    expected = CHANNEL_TOOLS.get(channel) if channel else None
    tools = [s.get("tool", "") for s in steps]
    flagged = []
    for s in steps:
        f = flag_step(s, expected)
        if f:
            flagged.append({"step": s.get("step"), "tool": s.get("tool"), "flags": f})
    return {
        "steps": len(steps),
        "tools": tools,
        "total_latency_ms": sum(int(s.get("latency_ms") or 0) for s in steps),
        "flagged": flagged,
        "tools_repeated": sorted({t for t in tools if tools.count(t) > 1}),
    }


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
_FLAG_STYLE = {
    "观察为空": ("#dc2626", "#fef2f2"),
    "高延迟": ("#d97706", "#fffbeb"),
    "意外工具": ("#7c3aed", "#f5f3ff"),
}


def render_timeline_html(
    steps: list[dict], title: str = "", subtitle: str = "", channel: str = ""
) -> str:
    """把一条思考-行动链路渲染成 HTML 时间线。"""
    if not steps:
        return '<div class="tv-empty">该 trace 无步骤记录。</div>'

    expected = CHANNEL_TOOLS.get(channel) if channel else None
    rows = []
    for s in steps:
        flags = flag_step(s, expected)
        chips = "".join(
            f'<span class="tv-flag" style="color:{_FLAG_STYLE[f][0]};'
            f'background:{_FLAG_STYLE[f][1]}">{f}</span>'
            for f in flags
            if f in _FLAG_STYLE
        )
        lat = int(s.get("latency_ms") or 0)
        obs = (s.get("observation_summary") or "").replace("<", "〈").replace(">", "〉")
        args = json.dumps(s.get("args") or {}, ensure_ascii=False)
        args = args.replace("<", "〈").replace(">", "〉")
        accent = _FLAG_STYLE[flags[0]][0] if flags else "#0891b2"
        rows.append(
            f'<div class="tv-step" style="border-left-color:{accent}">'
            f'<div class="tv-head">'
            f'<span class="tv-num">{s.get("step", "?")}</span>'
            f'<span class="tv-thought">{_esc(s.get("thought", ""))}</span>'
            f'<span class="tv-tool">{_esc(s.get("tool", ""))}</span>'
            f'<span class="tv-lat" style="color:{"#d97706" if lat >= SLOW_MS else "#94a3b8"}">{lat} ms</span>'
            f"{chips}</div>"
            f'<div class="tv-args"><b>参数</b> {_esc(args)[:220]}</div>'
            f'<div class="tv-obs"><b>观察</b> {_esc(obs)[:320] or "（空）"}</div>'
            f"</div>"
        )

    return (
        f'<div class="tv-wrap">'
        f'<div class="tv-title">{_esc(title)}{f" · {_esc(subtitle)}" if subtitle else ""}</div>'
        f'<div class="tv-body">{"".join(rows)}</div></div>'
    )


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


TRACE_VIEW_CSS = """
.tv-wrap{margin:10px 0}
.tv-title{font-size:14px;font-weight:700;color:#1e3a8a;margin-bottom:10px}
.tv-body{position:relative;padding-left:18px;border-left:2px dashed #cbd5e1}
.tv-step{position:relative;background:#fff;border:1px solid #e5e7eb;border-left:4px solid #0891b2;
  border-radius:8px;padding:10px 12px;margin:0 0 10px}
.tv-step:before{content:"";position:absolute;left:-25px;top:16px;width:9px;height:9px;
  border-radius:50%;background:#0891b2;box-shadow:0 0 0 3px #e0f2fe}
.tv-head{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.tv-num{background:#1e293b;color:#fff;border-radius:6px;padding:1px 8px;font-size:12px;font-weight:700}
.tv-thought{font-size:13px;font-weight:600;color:#1f2937}
.tv-tool{background:#e0f2fe;color:#0369a1;border-radius:6px;padding:1px 8px;font-size:12px;
  font-family:Consolas,Monaco,monospace}
.tv-lat{font-size:12px;margin-left:auto}
.tv-flag{font-size:11px;font-weight:700;border-radius:6px;padding:1px 7px}
.tv-args,.tv-obs{font-size:12px;color:#475569;margin-top:5px;line-height:1.6;
  font-family:Consolas,Monaco,monospace;word-break:break-all}
.tv-obs b,.tv-args b{color:#64748b;font-family:inherit}
.tv-empty{color:#94a3b8;font-size:13px;padding:10px}
"""


def render_audit_html(cases: list[dict], trace_dir: str | Path | None = None, limit: int = 8) -> str:
    """为一批用例（通常是失败或高风险用例）渲染「思考-行动链路」审计区块。

    这是「辅助缺陷定位」的落点：不只看结果对错，
    而是把每个用例的决策链路摊开展示，并标出可疑步骤。
    """
    blocks = []
    for r in cases[:limit]:
        tid = r.get("trace_id") or r.get("id")
        steps = load_trace(tid, trace_dir)
        if not steps:
            continue
        info = analyze_trace(steps, r.get("dimension", ""))
        status = "通过" if r.get("passed") else "失败"
        color = "#16a34a" if r.get("passed") else "#dc2626"
        subtitle = f"{status} · {info['steps']} 步 · 合计 {info['total_latency_ms']} ms"
        if info["flagged"]:
            subtitle += f" · 可疑步骤 {len(info['flagged'])}"
        blocks.append(
            f'<div class="tv-case">'
            f'<div class="tv-case-head">{_esc(r.get("id", ""))}'
            f'<span style="color:{color};font-weight:700;margin-left:8px">{_esc(r.get("question", "")[:60])}</span>'
            f"</div>"
            f'{render_timeline_html(steps, "", subtitle, r.get("dimension", ""))}'
            f"</div>"
        )

    if not blocks:
        return (
            '<div class="card"><h2>思考-行动链路审计</h2>'
            '<p class="muted">本次跑批未采集到可用 trace（'
            "run_suite 需在写入 traces/ 后运行；可先用 "
            '<code>python -m testsuite.report.trace_view --latest 5</code> 查看历史链路）。</p></div>'
        )

    return (
        '<div class="card"><h2>思考-行动链路审计</h2>'
        '<p class="muted">展示各用例的「思考 → 工具 → 观察」链路。'
        "可疑步骤自动标注："
        '<span class="tv-flag" style="color:#dc2626;background:#fef2f2">观察为空</span> '
        '<span class="tv-flag" style="color:#d97706;background:#fffbeb">高延迟</span> '
        f'<span class="tv-flag" style="color:#7c3aed;background:#f5f3ff">意外工具</span>'
        f"（阈值 {SLOW_MS} ms）</p>"
        + "".join(blocks)
        + "</div>"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="智能体行为可视化与审计（思考-行动链路）")
    ap.add_argument("--trace", default="", help="trace_id")
    ap.add_argument("--latest", type=int, default=0, help="展示最近 N 条")
    ap.add_argument("--channel", default="", help="预期通道（sql/rag/clarify），用于发现意外工具")
    ap.add_argument("--html", default="", help="导出 HTML 文件")
    args = ap.parse_args()

    ids = [args.trace] if args.trace else list_traces(limit=args.latest or 5)
    if not ids or not ids[0]:
        print("未找到 trace。请先运行 python -m testsuite.run_suite 产生链路记录。")
        return 1

    html_parts = []
    for tid in ids:
        steps = load_trace(tid)
        if not steps:
            print(f"[{tid}] 无步骤")
            continue
        info = analyze_trace(steps, args.channel)
        print("=" * 62)
        print(f"trace {tid}  共 {info['steps']} 步，合计 {info['total_latency_ms']} ms")
        print("=" * 62)
        for s in steps:
            flags = flag_step(s, CHANNEL_TOOLS.get(args.channel) if args.channel else None)
            mark = f"  ⚠ {'/'.join(flags)}" if flags else ""
            print(f"  step{s.get('step')}  [{s.get('tool')}]  {s.get('thought','')[:40]}{mark}")
            print(f"           观察: {(s.get('observation_summary') or '（空）')[:76]}")
        if info["flagged"]:
            print(f"  可疑步骤 {len(info['flagged'])} 个："
                  + "; ".join(f"step{f['step']}({'/'.join(f['flags'])})" for f in info["flagged"]))
        html_parts.append(render_timeline_html(steps, f"trace {tid}", "", args.channel))

    if args.html:
        doc = (
            "<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
            "<title>智能体行为审计</title><style>"
            "body{font-family:'Microsoft YaHei',sans-serif;background:#f6f8fb;color:#1f2937;"
            "padding:32px;max-width:1000px;margin:0 auto}"
            + TRACE_VIEW_CSS
            + "</style></head><body><h1>智能体行为审计</h1>"
            + "".join(html_parts)
            + "</body></html>"
        )
        Path(args.html).write_text(doc, encoding="utf-8")
        print(f"\n已导出 → {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
