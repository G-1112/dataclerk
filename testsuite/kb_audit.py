"""RAG 知识库体检（FR-T10，第四轮新增）。

对应赛道三「路径二：智能体测试工具开发」明确要求的能力：
    「RAG知识库测试工具：自动检测知识库中的冲突、冗余与错误，评估检索质量」

在此之前项目只有 rag_eval（召回@3 + 忠实度），衡量的是**检索结果好不好**，
却没有回答**知识库本身是否健康**。本模块补上这一段：

    检查项            说明                                    判定强度
    ----------------  --------------------------------------  ----------------
    冗余/重复         两片段 bigram 余弦 ≥ 阈值，语义近重复    高置信
    疑似冲突          同单位、同上下文但数值不同              需人工确认
    质量问题          过短碎片、无标点长句、空文档、重复行    高置信
    覆盖度            文档数/片段数/平均长度/唯一字符          统计
    检索健康度        探针查询的 Top-1 分数是否达阈值          统计

设计取舍：全部为确定性规则（零 LLM、零依赖、可离线复现），
故结论都是「确定性可复现」的；对无法确证的情况一律标注为「疑似，需人工确认」，
不把启发式判断包装成确定结论。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from agent.tools.retrieve_policy import PolicyRetriever, _bigrams, _cosine

# ---------------------------------------------------------------------------
# 阈值（可按知识库特点调整；报告会显示所用阈值，保证结论可复现）
# ---------------------------------------------------------------------------
DUP_COSINE = 0.85        # 冗余判定：余弦相似度阈值
SHORT_CHARS = 8          # 过短碎片阈值
LONG_NO_PUNCT = 60       # 无标点长句阈值
PROBE_MIN_SCORE = 0.10   # 检索健康度：Top-1 最低可接受分

_PUNCT = "。！？；：、，,.!?;:"
# 数值 + 单位，用于冲突检测
_NUM_UNIT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(元|万元|个|门|次|小时|分钟|天|周|月|年|学分|分|%|人)")


def _norm_num(s: str) -> float:
    return float(s.replace(",", ""))


def _context_of(text: str, start: int, span: int = 6) -> str:
    """取数值前的上下文片段（用于判断'说的是不是同一件事'）。"""
    ctx = text[max(0, start - span) : start]
    return "".join(c for c in ctx if c not in _PUNCT and not c.isspace())[-span:]


def extract_facts(text: str) -> list[dict]:
    """抽取片段中的 (上下文, 数值, 单位) 三元组。"""
    out = []
    for m in _NUM_UNIT.finditer(text):
        out.append(
            {
                "context": _context_of(text, m.start()),
                "value": _norm_num(m.group(1)),
                "unit": m.group(2),
                "raw": m.group(0),
            }
        )
    return out


# ---------------------------------------------------------------------------
# 各项检查
# ---------------------------------------------------------------------------
def find_duplicates(snippets: list, threshold: float = DUP_COSINE) -> list[dict]:
    """冗余：两两比较 bigram 余弦，找出近重复片段。

    O(n²) 在此可接受：知识库规模为百级片段，实测全量约 0.1s（见 tests）。
    若知识库增长到万级，应改为 MinHash/LSH 近似去重。
    """
    dups: list[dict] = []
    n = len(snippets)
    for i in range(n):
        for j in range(i + 1, n):
            score = _cosine(snippets[i].vector, snippets[j].vector)
            if score >= threshold:
                dups.append(
                    {
                        "a": snippets[i].doc_id,
                        "b": snippets[j].doc_id,
                        "a_text": snippets[i].text,
                        "b_text": snippets[j].text,
                        "similarity": round(score, 4),
                    }
                )
    return sorted(dups, key=lambda d: -d["similarity"])


def find_conflicts(snippets: list) -> list[dict]:
    """疑似冲突：同一上下文表述下出现不同数值。

    例：两条都写「每生每年 X 元」但 X 不同 —— RAG 检索到哪条就会答哪个值，
    属于典型的知识库隐患。**判为「疑似」**：也可能是不同对象（如两种奖学金），
    因此报告会同时给出两条原文供人工确认。

    实现注意（实测踩坑）：跨文档判定必须用 `title` 而非 `doc_id`，
    因为 `doc_id` 形如「奖学金评定办法#1」，含片段序号，
    用它比较会让「跨文档」恒为 True，从而把同文档内的比较误报为跨文档。
    """
    buckets: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    for s in snippets:
        for f in extract_facts(s.text):
            if len(f["context"]) < 2:
                continue  # 上下文太短不足以判断是否同一件事
            buckets[(f["context"], f["unit"])].append((s, f))

    conflicts: list[dict] = []
    for (ctx, unit), items in buckets.items():
        values = {f["value"] for _, f in items}
        if len(values) < 2 or len(items) < 2:
            continue
        titles = {s.title for s, _ in items}
        conflicts.append(
            {
                "context": ctx,
                "unit": unit,
                "values": sorted(values),
                "evidence": [
                    {
                        "doc": s.title,
                        "section": s.section,
                        "text": s.text,
                        "value": f["value"],
                    }
                    for s, f in items[:6]
                ],
                "cross_doc": len(titles) > 1,
                "same_section": len({s.section for s, _ in items}) == 1,
            }
        )
    # 跨文档的冲突风险更高，排在前面
    return sorted(conflicts, key=lambda c: (not c["cross_doc"], -len(c["values"])))


def find_quality_issues(snippets: list, docs: dict[str, str]) -> list[dict]:
    """质量问题：空文档、过短碎片、无标点长句、文档内重复行。"""
    issues: list[dict] = []

    for title, text in docs.items():
        if not text.strip():
            issues.append({"kind": "空文档", "doc_id": title, "detail": "文件无有效内容"})
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        dup_lines = [l for l, c in Counter(lines).items() if c > 1]
        if dup_lines:
            issues.append(
                {
                    "kind": "文档内重复行",
                    "doc_id": title,
                    "detail": f"{len(dup_lines)} 行重复，例：{dup_lines[0][:40]}",
                }
            )

    for s in snippets:
        t = s.text
        if len(t) < SHORT_CHARS:
            issues.append(
                {"kind": "过短碎片", "doc_id": s.doc_id, "detail": f"仅 {len(t)} 字：{t[:30]}"}
            )
        elif len(t) >= LONG_NO_PUNCT and not any(c in _PUNCT for c in t):
            issues.append(
                {
                    "kind": "无标点长句",
                    "doc_id": s.doc_id,
                    "detail": f"{len(t)} 字无标点，可能切片异常：{t[:34]}…",
                }
            )
    return issues


def retrieval_health(probe_queries: list[str], top_k: int = 3) -> dict:
    """检索健康度：探针查询的 Top-1 分数是否达阈值。

    分数过低说明该问题在知识库中没有像样的对应内容（覆盖缺口）。
    """
    r = PolicyRetriever()
    rows = []
    for q in probe_queries:
        hits = r.retrieve_with_source(q, top_k=top_k)
        top1 = hits[0]["score"] if hits else 0.0
        rows.append(
            {
                "query": q,
                "top1_score": top1,
                "hits": len(hits),
                "ok": top1 >= PROBE_MIN_SCORE,
                "top_doc": hits[0]["title"] if hits else None,
            }
        )
    weak = [r0 for r0 in rows if not r0["ok"]]
    return {
        "probes": rows,
        "weak_count": len(weak),
        "weak": weak,
        "coverage": round(1 - len(weak) / len(rows), 4) if rows else 0.0,
        "threshold": PROBE_MIN_SCORE,
    }


# ---------------------------------------------------------------------------
# 汇总入口
# ---------------------------------------------------------------------------
def audit(policies_dir: str | Path | None = None) -> dict:
    """执行全量体检，返回结构化结论（供 CLI / 报告渲染使用）。"""
    retriever = PolicyRetriever(policies_dir)
    snippets = retriever.load()

    docs: dict[str, str] = {}
    for path in sorted(retriever.dir.glob("*.txt")):
        docs[path.stem] = path.read_text(encoding="utf-8")

    dup = find_duplicates(snippets)
    conflicts = find_conflicts(snippets)
    quality = find_quality_issues(snippets, docs)

    lengths = [len(s.text) for s in snippets] or [0]
    chars = set()
    for s in snippets:
        chars.update(c for c in s.text if not c.isspace())

    # 严重度分级（避免把启发式判断包装成确定结论）：
    #   blockers —— 高置信、确定性：空文档、近重复片段（余弦阈值判定）
    #   warnings —— 启发式，需人工确认：疑似冲突、结构/质量问题
    blockers = len([q for q in quality if q["kind"] == "空文档"]) + len(dup)
    warnings = len(conflicts) + len([q for q in quality if q["kind"] != "空文档"])

    return {
        "docs": len(docs),
        "snippets": len(snippets),
        "avg_len": round(sum(lengths) / len(lengths), 1),
        "unique_chars": len(chars),
        "duplicates": dup,
        "conflicts": conflicts,
        "quality": quality,
        "thresholds": {
            "DUP_COSINE": DUP_COSINE,
            "SHORT_CHARS": SHORT_CHARS,
            "LONG_NO_PUNCT": LONG_NO_PUNCT,
        },
        "blockers": blockers,
        "warnings": warnings,
        "healthy": blockers == 0,
    }


# ---------------------------------------------------------------------------
# CLI：python -m testsuite.kb_audit [--json]
# ---------------------------------------------------------------------------
def main() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="RAG 知识库体检（冲突/冗余/质量/检索健康度）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    ap.add_argument("--probes", default="", help="逗号分隔的探针查询（默认用内置 8 条）")
    args = ap.parse_args()

    defaults = [
        "奖学金怎么申请",
        "宿舍几点关门",
        "转专业流程",
        "考试作弊怎么处理",
        "学分不够怎么办",
        "图书能借几本",
        "请假需要什么手续",
        "校园卡丢了怎么办",
    ]
    probes = [p.strip() for p in args.probes.split(",") if p.strip()] or defaults

    rep = audit()
    health = retrieval_health(probes)
    rep["retrieval_health"] = health

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    print("=" * 62)
    print("RAG 知识库体检报告")
    print("=" * 62)
    print(f"规模    : {rep['docs']} 篇文档 / {rep['snippets']} 条片段 / 平均 {rep['avg_len']} 字")
    print(f"阈值    : 冗余余弦≥{rep['thresholds']['DUP_COSINE']}，"
          f"过短<{rep['thresholds']['SHORT_CHARS']}字，"
          f"无标点长句≥{rep['thresholds']['LONG_NO_PUNCT']}字")
    print(f"结论    : blockers={rep['blockers']}  warnings={rep['warnings']}  "
          f"healthy={'是' if rep['healthy'] else '否'}")
    print()

    print(f"[冗余] {len(rep['duplicates'])} 对（高置信，确定性判定）")
    for d in rep["duplicates"][:5]:
        print(f"  {d['similarity']:.4f}  {d['a']}  <->  {d['b']}")
        print(f"      A: {d['a_text'][:56]}")
        print(f"      B: {d['b_text'][:56]}")

    print()
    print(f"[疑似冲突] {len(rep['conflicts'])} 组（需人工确认；可能是不同对象）")
    for c in rep["conflicts"][:5]:
        print(f"  上下文「{c['context']}」单位「{c['unit']}」值={c['values']} "
              f"跨文档={'是' if c['cross_doc'] else '否'}")
        for e in c["evidence"][:4]:
            print(f"      [{e['doc']} · {e['section']}] {e['value']} <- {e['text'][:52]}")

    print()
    print(f"[质量问题] {len(rep['quality'])} 项")
    for q in rep["quality"][:8]:
        print(f"  [{q['kind']}] {q['doc_id']}: {q['detail'][:60]}")

    print()
    print(f"[检索健康度] 覆盖 {health['coverage']:.0%}"
          f"（Top-1 分数阈值 {health['threshold']}，弱项 {health['weak_count']} 条）")
    for p in health["probes"]:
        flag = "OK " if p["ok"] else "弱 "
        print(f"  {flag} {p['query']:16s} top1={p['top1_score']:.4f}  <- {p['top_doc']}")

    print()
    print("提示：冗余为确定性判定（余弦），可据此直接合并或删除重复片段；")
    print("      疑似冲突为启发式判定，请人工核对原文后再决定是否修订。")
    return 0 if rep["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
