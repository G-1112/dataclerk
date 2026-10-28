"""跨模型稳定性矩阵：同一批用例跑多个模型，回答「这个准确率是模型的还是模型+运气」。

为什么需要
----------
单模型单轮的 89.8%（修复基准后 93.2%）只能说明"这一家、这一轮是这样"。
评委真正会问的是：换一家模型，你的**测试口径**还站得住吗？哪些题是"谁来都错"（真难题），
哪些只是"某家模型不行"？这两种失败的处理方式完全不同。

用法
----
1) 复制配置模板，填 base/model 与 key 所在的环境变量名（key 本身不要写进文件）：

    cp testsuite/models.example.json models.json

2) 把各家 key 放进环境变量（示例）：

    # Windows PowerShell
    $env:QWEN_API_KEY="sk-..."; $env:GLM_API_KEY="..."
    # macOS / Linux
    export QWEN_API_KEY=sk-...; export GLM_API_KEY=...

3) 跑矩阵：

    python -m testsuite.model_matrix --config models.json

缺少某个 key 的模型会被标成 `skipped`，**不会伪造结果**，也不影响其它模型。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def load_env_file(path: Path) -> None:
    """把 env 文件里尚未设置的变量补进进程环境（不覆盖已有值）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _apply_model(cfg: dict) -> str:
    """把某个模型的配置写进环境并重建 LLM 客户端，返回 key（空串表示缺 key）。"""
    from agent import llm as agent_llm

    key = cfg.get("key") or os.getenv(cfg.get("key_env", ""), "")
    if not key:
        return ""
    os.environ["LLM_API_KEY"] = key
    os.environ["LLM_BASE_URL"] = cfg.get("base", "https://api.deepseek.com")
    os.environ["LLM_MODEL"] = cfg.get("model", cfg.get("name", "deepseek-chat"))
    # agent.llm 的单例在 import 时按环境变量构造，换模型必须重建它（不改全局代码路径）
    agent_llm._llm = agent_llm.LlmClient()
    return key


def run_matrix(configs: list[dict], limit: int | None = None) -> dict:
    """逐模型跑功能维度，返回矩阵结果（含跨模型不稳定题）。"""
    from testsuite import llm_track as T

    cases = T._load_functional()
    if limit:
        cases = cases[:limit]

    rows: list[dict] = []
    for cfg in configs:
        name = cfg.get("name") or cfg.get("model") or "unknown"
        key = _apply_model(cfg)
        if not key:
            rows.append({"name": name, "status": "skipped",
                         "reason": f"缺少环境变量 {cfg.get('key_env', '<未指定>')}",
                         "model": cfg.get("model", "")})
            print(f"· {name}: 跳过（缺 key：{cfg.get('key_env')}）", flush=True)
            continue
        r = T.run_functional(cases)
        failed = [d["id"] for d in r["details"] if not d["passed"]]
        rows.append({
            "name": name, "status": "ok", "model": cfg.get("model", ""),
            "base": cfg.get("base", ""),
            "passed": r["passed"], "total": r["total"],
            "accuracy": round(r["accuracy"], 4),
            "passed_semantic": r["passed_semantic"],
            "accuracy_semantic": round(r["accuracy_semantic"], 4),
            "failed_ids": failed,
        })
        print(f"· {name}: {r['passed']}/{r['total']} = {r['accuracy']:.1%}"
              f"（语义 {r['passed_semantic']}/{r['total']}）", flush=True)

    done = [r for r in rows if r["status"] == "ok"]
    all_ids = {d["id"] for d in cases}
    fail_map: dict[str, list[str]] = {}
    for r in done:
        for cid in r["failed_ids"]:
            fail_map.setdefault(cid, []).append(r["name"])
    hard = sorted(cid for cid, ms in fail_map.items() if len(ms) == len(done) and done)
    model_specific = {cid: ms for cid, ms in fail_map.items() if len(ms) < len(done)}
    return {
        "models": rows,
        "case_total": len(all_ids),
        "hard_cases": hard,
        "model_specific_failures": model_specific,
        "all_models_identical": len({r["passed"] for r in done}) <= 1 if done else False,
    }


def _md(rep: dict) -> str:
    lines = ["# 跨模型稳定性矩阵（同一批功能用例）", "",
             "| 模型 | 严格 | 语义（含列子集前） | 失败用例 |", "|---|---|---|---|"]
    for r in rep["models"]:
        if r["status"] != "ok":
            lines.append(f"| {r['name']} | skipped | — | {r['reason']} |")
            continue
        lines.append(f"| {r['name']} | {r['passed']}/{r['total']} = {r['accuracy']:.1%} "
                     f"| {r['passed_semantic']}/{r['total']} | {', '.join(r['failed_ids']) or '—'} |")
    lines += ["", f"- 所有模型都错（真难题）：{', '.join(rep['hard_cases']) or '无'}",
              f"- 仅部分模型错（模型特有问题）：{rep['model_specific_failures'] or '无'}",
              f"- 各模型通过数完全一致：**{rep['all_models_identical']}**"]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="跨模型稳定性矩阵")
    ap.add_argument("--config", default="models.json", help="模型配置 JSON（见 testsuite/models.example.json）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条用例（冒烟用）")
    ap.add_argument("--out-dir", default="data/report")
    args = ap.parse_args()

    load_env_file(ROOT / ".env.llm")
    cfg_path = Path(args.config)
    if not cfg_path.is_absolute():
        cfg_path = ROOT / cfg_path
    if not cfg_path.exists():
        print(f"✗ 找不到配置：{cfg_path}\n  先复制模板：cp testsuite/models.example.json models.json")
        return 2
    configs = json.loads(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(configs, list) or not configs:
        print("✗ 配置必须是数组，且至少一个模型")
        return 2

    if not os.getenv("LLM_API_KEY"):
        # llm_track 在导入时要求已配置 key；这里先用第一个可用模型的 key 顶住
        for c in configs:
            if c.get("key") or os.getenv(c.get("key_env", ""), ""):
                _apply_model(c)
                break
        else:
            print("✗ 一个模型的 key 都没配到（检查环境变量名是否写错）")
            return 2

    rep = run_matrix(configs, args.limit or None)
    done = [r for r in rep["models"] if r["status"] == "ok"]
    print(f"\n完成 {len(done)} 个模型；所有模型都错的题：{rep['hard_cases'] or '无'}")
    if rep["model_specific_failures"]:
        print(f"仅部分模型错：{rep['model_specific_failures']}")

    out = Path(args.out_dir)
    if not out.is_absolute():
        out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    (out / "model_matrix.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "model_matrix.md").write_text(_md(rep), encoding="utf-8")
    print(f"已写出：{out / 'model_matrix.json'}  {out / 'model_matrix.md'}")
    return 0 if done else 1


if __name__ == "__main__":
    raise SystemExit(main())
