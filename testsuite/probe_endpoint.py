"""把「能连上」与「协议对」拆开验：一条命令检查任意 OpenAI 兼容端点。

为什么单做一个探针
------------------
接第三方智能体失败时，报错往往只有一句「调用失败」，分不清是
**网络不通 / 鉴权错 / 协议不兼容 / 模型太慢**。而四类的处置完全不同：
换地址、换 key、改适配器、还是调超时。所以先把它们逐项测出来，再谈跑评测。

检查顺序（任一步失败即给出可操作结论，不再往下猜）
1. TCP 可达（3s）——不通就是地址/端口/防火墙问题
2. `GET {base}/models`——列出可用模型名（部分网关不实现，返回 404 属正常）
3. 最小对话（`只回复 OK`）——验证请求体/响应体形状与鉴权
4. 三条业务冒烟（人数/成绩/政策）——验证它真的在回答校园数据问题
5. 结论：`ready` / `auth_failed` / `protocol_mismatch` / `unreachable` / `too_slow`

用法
----
# Ollama（本机）
python -m testsuite.probe_endpoint --base http://127.0.0.1:11434/v1 --model qwen2.5:7b

# vLLM / OneAPI / 百炼兼容模式
python -m testsuite.probe_endpoint --base http://127.0.0.1:8000/v1 --model Qwen2.5-7B-Instruct

# FastGPT / Dify（多数网关不需要 /v1，按对方文档给的 base 填）
python -m testsuite.probe_endpoint --base http://127.0.0.1:3000/api/v1 --model fastgpt --api-key sk-xxx
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

if hasattr(sys.stdout, "reconfigure"):  # Windows 控制台默认 GBK
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

SMOKE_QUESTIONS = [
    "计算机学院有多少学生？",
    "软件学院的平均绩点是多少？",
    "奖学金评定需要什么条件？",
]


def _join(base: str, path: str) -> str:
    """把 base 与 path 拼起来：base 已带 /v1 就不重复加。"""
    base = base.rstrip("/")
    if base.endswith("/v1") and path.startswith("/v1/"):
        return base + path[3:]
    return base + path


def _tcp_ok(base: str, timeout: float = 3.0) -> tuple[bool, str]:
    u = urlparse(base)
    host = u.hostname or ""
    port = u.port or (443 if u.scheme == "https" else 80)
    if not host:
        return False, f"base_url 解析不到主机名：{base!r}"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"TCP {host}:{port} 可达"
    except OSError as e:
        return False, f"TCP {host}:{port} 不可达：{type(e).__name__}: {e}"


def _get_models(base: str, api_key: str, timeout: float) -> tuple[list[str], str]:
    url = _join(base, "/v1/models")
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace") or "{}")
        items = data.get("data") if isinstance(data, dict) else None
        ids = [str(m.get("id")) for m in (items or []) if isinstance(m, dict) and m.get("id")]
        return ids, f"GET /v1/models 200，可用模型 {len(ids)} 个：{ids[:5]}"
    except urllib.error.HTTPError as e:
        return [], f"GET /v1/models 返回 {e.code}（部分网关不实现，属正常）"
    except Exception as e:  # noqa: BLE001
        return [], f"GET /v1/models 失败：{type(e).__name__}: {e}"


def probe(base: str, model: str, api_key: str = "", timeout: float = 30.0,
          channel_path: str | None = None) -> dict:
    """逐项探测端点，返回结构化结论（不抛异常）。"""
    out: dict = {"base_url": base, "model": model, "checks": []}
    ok, msg = _tcp_ok(base)
    out["checks"].append({"step": "tcp", "ok": ok, "detail": msg})
    if not ok:
        out["verdict"] = "unreachable"
        out["advice"] = "地址/端口不通：确认服务已启动、端口未被占用、未被防火墙拦截。"
        return out

    ids, msg = _get_models(base, api_key, min(timeout, 15.0))
    out["checks"].append({"step": "models", "ok": True, "detail": msg, "models": ids})
    if ids and model not in ids:
        out["advice_model"] = f"`{model}` 不在可用模型列表中，可能需要改成 {ids[:3]} 之一"

    from .adapters.openai_chat import OpenAIChatAdapter

    a = OpenAIChatAdapter(base, model, api_key=api_key, timeout=timeout,
                          channel_path=channel_path)
    t0 = time.perf_counter()
    r = a.run("只回复两个字：OK", role="admin")
    ms = round((time.perf_counter() - t0) * 1000, 1)
    text = str(r.get("answer", ""))
    failed = "查询失败" in text or r.get("channel") == "error"
    out["checks"].append({
        "step": "minimal_chat", "ok": not failed, "latency_ms": ms,
        "detail": text[:160],
    })
    if failed:
        low = text.lower()
        if "401" in low or "403" in low or "auth" in low or "api key" in low:
            out["verdict"] = "auth_failed"
            out["advice"] = "鉴权失败：确认 api_key 是否正确、是否需要在 header 里换鉴权方式。"
        else:
            out["verdict"] = "protocol_mismatch"
            out["advice"] = ("协议不匹配：确认端点是 OpenAI 兼容的 /v1/chat/completions，"
                             "以及响应里是否有 choices[0].message.content。")
        return out

    smokes = []
    for q in SMOKE_QUESTIONS:
        t = time.perf_counter()
        rr = a.run(q, role="admin", session_id="probe")
        ms = round((time.perf_counter() - t) * 1000, 1)
        ans = str(rr.get("answer", ""))
        smokes.append({
            "question": q, "latency_ms": ms, "ok": "查询失败" not in ans,
            "answer": ans[:120],
            "has_number": any(ch.isdigit() for ch in ans),
            "channel": rr.get("channel"),
        })
    out["checks"].append({"step": "smoke", "ok": all(s["ok"] for s in smokes), "samples": smokes})
    out["verdict"] = "ready" if all(s["ok"] for s in smokes) else "too_slow"
    if out["verdict"] == "ready":
        out["advice"] = ("端点可用：可以直接跑完整评测 "
                         "`python -m testsuite.run_external --openai-base <base> --openai-model <model>`")
    else:
        out["advice"] = "端点能应答但业务冒烟有失败：先看 samples，再决定是模型能力还是超时问题。"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="探测任意 OpenAI 兼容端点是否可测")
    ap.add_argument("--base", required=True, help="base_url，如 http://127.0.0.1:11434/v1")
    ap.add_argument("--model", required=True, help="模型名（服务端要求什么就写什么）")
    ap.add_argument("--api-key", default="", help="Bearer token，不需要就留空")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--channel-path", default=None,
                    help="响应中携带通道信息的字段路径（如 channel），用于路由维度")
    ap.add_argument("--json-out", default="", help="把完整结果写到该路径（JSON）")
    args = ap.parse_args()

    rep = probe(args.base, args.model, args.api_key, args.timeout, args.channel_path)
    print(f"端点：{rep['base_url']}  模型：{rep['model']}")
    for c in rep["checks"]:
        mark = "✓" if c.get("ok") else "✗"
        print(f"  {mark} [{c['step']}] {str(c.get('detail', ''))[:150]}")
        for s in c.get("samples", []):
            print(f"      · {s['question'][:20]} → {s['latency_ms']}ms | {s['answer'][:60]}")
    print(f"\n结论：{rep['verdict']}")
    if rep.get("advice"):
        print(f"建议：{rep['advice']}")
    if rep.get("advice_model"):
        print(f"注意：{rep['advice_model']}")
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写出：{p}")
    return 0 if rep["verdict"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
