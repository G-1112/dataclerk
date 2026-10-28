"""用同一套用例测「别人的智能体」（实用性/通用性的核心证据）。

    # A) 测内置演示机器人（自带 5 类缺陷，用于自证能抓出问题）
    python -m testsuite.run_external --demo

    # B) 测任意 HTTP + JSON 智能体
    python -m testsuite.run_external --url http://127.0.0.1:8901/chat

    # C) 对方字段名不同也没关系，映射即可
    python -m testsuite.run_external --url http://host/api/ask \\
        --request-map question=q --response-map answer=data.reply

    # D) 测真实开源智能体：OpenAI 兼容协议（FastGPT / Dify / vLLM / Ollama / OneAPI）
    python -m testsuite.run_external --openai-base http://127.0.0.1:3000 \\
        --openai-model gpt-4o-mini --api-key sk-xxx --channel-path channel

    # E) 没有 Docker / 外网也能验证协议层：本地起 OpenAI 协议桩
    python -m testsuite.run_external --stub --stub-brain guarded --channel-path channel

设计说明（为什么要过滤用例）
----------------------------
安全维度里有两类用例是**工具级**的——它们直接调用本仓库的 `execute_sql` 验证
权限层，不经过被测体（含 `sql` 字段的对抗 SQL、以及「破坏性」写操作）。
外部智能体没有这个工具，硬跑只会得到无意义的"通过"。因此外部模式只跑
**行为级**用例：越权 / 注入 / 路由 / 边界，并显式声明跳过了哪些。
诚实标注适用范围，比刷一个好看的 100% 更重要。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.request
from pathlib import Path

from .adapters import HttpAgentAdapter, OpenAIChatAdapter
from .cases import boundary_cases, routing_cases, security_cases
from .run_suite import run_boundary, run_routing, run_security

ROOT = Path(__file__).resolve().parent.parent

# 缺陷类型 → 人类可读解释（报告里给评委看的那一列）
_DEFECT_HINTS = {
    "越权": "D1 未校验调用者身份：只看问题里的学号就返回他人数据",
    "注入": "D2 注入不设防：命中「忽略指令/导出」即整表倒出",
    "破坏性": "D3 未拦截写操作",
    "路由": "D4 路由错误：政策类问题走了数据通道",
    "边界": "D5 无澄清策略：模糊问题直接硬答",
}


def _behavior_only(cases: list[dict]) -> tuple[list[dict], list[dict]]:
    """拆出行为级用例与不适用的工具级用例。"""
    usable, skipped = [], []
    for c in cases:
        if "sql" in c or c.get("category") == "破坏性":
            skipped.append(c)
        else:
            usable.append(c)
    return usable, skipped


def _rate(passed: int, total: int) -> float:
    return round(passed / total, 4) if total else 0.0


def _supports_channel(adapter) -> bool:
    """被测体是否暴露通道字段；默认 True（自定义 HTTP 适配器通常自带 channel）。"""
    return bool(getattr(adapter, "supports_channel", True))


def evaluate(target, timeout: float = 10.0, request_map: dict | None = None,
             response_map: dict | None = None, *, adapter=None) -> dict:
    """跑三个行为级维度。

    `target` 可以是 URL（默认走 HttpAgentAdapter），也可以直接传一个适配器实例
    （任何实现 `run()` 的对象）——这样 OpenAI 协议、本地智能体都能复用同一套判定。
    """
    if adapter is None and hasattr(target, "run"):
        adapter = target
    if adapter is None:
        adapter = HttpAgentAdapter(
            url=target,
            timeout=timeout,
            request_map=request_map or None,
            response_map=response_map or None,
        )
    label = getattr(adapter, "label", "") or (target if isinstance(target, str) else getattr(adapter, "name", "adapter"))

    sec_cases, sec_skipped = _behavior_only(security_cases())
    sec = run_security(sec_cases, adapter)
    routing = run_routing(routing_cases(), adapter) if _supports_channel(adapter) else []
    boundary = run_boundary(boundary_cases(), adapter)

    def _pass_rate(rows: list[dict]) -> dict:
        total = len(rows)
        passed = sum(1 for r in rows if r.get("passed"))
        return {"total": total, "passed": passed, "rate": _rate(passed, total)}

    detail = []
    for r in sec:
        if not r.get("passed"):
            detail.append({
                "id": r["id"], "dimension": "security", "category": r.get("category", ""),
                "question": r.get("question", ""), "answer": r.get("answer", ""),
                "verdict": r.get("verdict", ""),
                "defect": _DEFECT_HINTS.get(r.get("category", ""), "未分类"),
            })
    for r in routing:
        if not r.get("passed"):
            detail.append({
                "id": r["id"], "dimension": "routing", "category": r.get("expected_channel", ""),
                "question": r.get("question", ""),
                "answer": f"期望 {r.get('expected_channel')}，实际 {r.get('actual_channel')}",
                "verdict": "路由错误", "defect": _DEFECT_HINTS["路由"],
            })
    for r in boundary:
        if not r.get("passed"):
            detail.append({
                "id": r["id"], "dimension": "boundary", "category": "边界",
                "question": r.get("question", ""), "answer": r.get("answer", ""),
                "verdict": "未优雅降级/未澄清", "defect": _DEFECT_HINTS["边界"],
            })

    # 按缺陷类型聚合，报告里按「抓出几类缺陷」呈现
    by_defect: dict[str, list[str]] = {}
    for d in detail:
        by_defect.setdefault(d["defect"], []).append(d["id"])

    dims = {"security_behavioral": _pass_rate(sec)}
    if _supports_channel(adapter):
        dims["routing"] = _pass_rate(routing)
    dims["boundary"] = _pass_rate(boundary)

    rep = {
        "adapter": getattr(adapter, "name", "http"),
        "target": label,
        "dimensions": dims,
        "skipped_cases": [c["id"] for c in sec_skipped],
        "defect_classes": {k: v for k, v in by_defect.items()},
        "defect_count": len(by_defect),
        "failures": detail,
    }
    if not _supports_channel(adapter):
        rep["routing_not_applicable"] = (
            "被测服务未暴露通道(channel)字段：标准 OpenAI 协议只回正文，"
            "路由维度不适用。用 --channel-path 指向服务自有的通道字段后可启用。"
        )
    return rep


def _print_report(rep: dict) -> None:
    print("=== 外部智能体测试报告 ===")
    print(f"被测体：{rep['target']}")
    print(f"适配器：{rep['adapter']}（未改动被测方一行代码）")
    print(f"跳过用例：{', '.join(rep['skipped_cases']) or '无'}（工具级用例不适用外部智能体）")
    if rep.get("routing_not_applicable"):
        print(f"路由维度：不适用 —— {rep['routing_not_applicable']}")
    print()
    print(f"{'维度':<24}{'用例':>6}{'通过':>6}{'通过率':>8}")
    for name, s in rep["dimensions"].items():
        print(f"{name:<24}{s['total']:>6}{s['passed']:>6}{s['rate'] * 100:>7.1f}%")
    print()
    print(f"--- 检出缺陷类别：{rep['defect_count']} 类 ---")
    for defect, ids in rep["defect_classes"].items():
        print(f"• {defect}  → 命中 {len(ids)} 条：{', '.join(ids[:6])}"
              + ("…" if len(ids) > 6 else ""))
    if not rep["defect_classes"]:
        print("（未检出缺陷：该智能体在这三个维度上表现符合预期）")


def _demo(db_path: str | None) -> tuple[str, callable]:
    """起一个带缺陷的演示机器人，返回 (url, 关闭函数)。"""
    from examples.external_bot.server import make_server

    srv = make_server(0, db_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/chat"

    def _stop() -> None:
        srv.shutdown()
        srv.server_close()

    return url, _stop


def _kv(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in pairs or []:
        if "=" in item:
            k, v = item.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

    ap = argparse.ArgumentParser(description="用同一套用例测外部智能体")
    ap.add_argument("--url", help="外部智能体 HTTP 端点（POST JSON）")
    ap.add_argument("--demo", action="store_true", help="启动内置演示机器人并测它")
    ap.add_argument("--db", default=None, help="演示机器人的数据库路径")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--request-map", nargs="*", help="question=q role=r ...")
    ap.add_argument("--response-map", nargs="*", help="answer=data.answer channel=ch")
    # OpenAI 兼容协议：真实开源智能体（FastGPT/Dify/vLLM/Ollama/OneAPI）走这条
    ap.add_argument("--openai-base", help="OpenAI 兼容服务的 base_url，如 http://127.0.0.1:8911")
    ap.add_argument("--openai-model", default="stub", help="model 名（服务端要求什么就写什么）")
    ap.add_argument("--api-key", default="", help="Bearer token；不需要鉴权就留空")
    ap.add_argument("--channel-path", default=None,
                    help="服务若额外返回通道字段，指过去（如 channel）；标准协议无此字段")
    ap.add_argument("--stub", action="store_true",
                    help="本地起 OpenAI 协议桩（无需任何外部服务/Docker）")
    ap.add_argument("--stub-brain", default="naive", choices=["naive", "guarded"],
                    help="协议桩的大脑：naive 有缺陷 / guarded 已修好")
    ap.add_argument("--json-out", default=str(ROOT / "data" / "report" / "external_eval.json"))
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    if not (args.url or args.demo or args.openai_base or args.stub):
        ap.error("需要 --url / --demo / --openai-base / --stub 之一（--stub 无需外部服务）")

    stop = None
    adapter = None
    if args.stub:
        from examples.openai_stub.server import make_server as _make_stub

        srv = _make_stub(0, args.db, args.stub_brain)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        args.openai_base = f"http://127.0.0.1:{srv.server_port}"

        def stop() -> None:  # noqa: F811 - 局部关闭函数
            srv.shutdown()
            srv.server_close()

    if args.openai_base:
        adapter = OpenAIChatAdapter(
            base_url=args.openai_base,
            model=args.openai_model,
            api_key=args.api_key,
            timeout=args.timeout,
            channel_path=args.channel_path,
        )
        adapter.label = f"{args.openai_base} (model={args.openai_model})"

    url = args.url
    if args.demo:
        url, stop = _demo(args.db)
    if adapter is None and not url:
        ap.error("未指定可测目标")

    try:
        if url:
            # 探活：避免把"连不上"误报成"被测体有缺陷"
            health = url.rsplit("/chat", 1)[0] + "/health"
            try:
                urllib.request.urlopen(health, timeout=3).read()
            except Exception:  # noqa: BLE001 - 无 /health 的第三方服务属正常
                pass

        rep = evaluate(url, args.timeout, _kv(args.request_map), _kv(args.response_map), adapter=adapter)
    finally:
        if stop:
            stop()

    _print_report(rep)
    if not args.no_write:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写出：{out}")


if __name__ == "__main__":
    main()
