"""Trace 埋点（FR-A08）。

每步记录为 JSONL，是全部归因分析的基础。粒度不足则报告无法做失败分类。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

TRACE_DIR = Path(__file__).resolve().parent.parent / "data" / "traces"


class Tracer:
    def __init__(self, trace_dir: str | Path | None = None) -> None:
        self.dir = Path(trace_dir or TRACE_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)

    def start(self) -> str:
        return uuid.uuid4().hex[:12]

    def record(
        self,
        trace_id: str,
        step: int,
        thought: str,
        tool: str,
        args: dict,
        observation_summary: str,
        latency_ms: int = 0,
        tokens: int = 0,
    ) -> None:
        line = {
            "trace_id": trace_id,
            "step": step,
            "thought": thought,
            "tool": tool,
            "args": args,
            "observation_summary": observation_summary[:200],
            "latency_ms": latency_ms,
            "tokens": tokens,
            "ts": time.time(),
        }
        with (self.dir / f"{trace_id}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def get(self, trace_id: str) -> list[dict]:
        path = self.dir / f"{trace_id}.jsonl"
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
