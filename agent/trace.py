"""Trace 埋点（FR-A08）。

每步记录为 JSONL，是全部归因分析的基础。粒度不足则报告无法做失败分类。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

TRACE_DIR = Path(__file__).resolve().parent.parent / "data" / "traces"

# trace_id 由 Tracer.start() 生成为 uuid4().hex[:12]（纯十六进制）。
# 严格白名单校验，防止 trace_id 被用作路径穿越（第四轮实测修复）：
#   GET /trace/..\HTTPPROBE 原可读到 traces 目录之外的 .jsonl 文件。
#   Windows 下反斜杠是路径分隔符，而 URL 路由不会拦截它，故必须在取值处校验。
_TRACE_ID_RE = re.compile(r"^[0-9a-fA-F]{1,64}$")


def is_valid_trace_id(trace_id: str) -> bool:
    """trace_id 必须是纯十六进制串（拒绝 . / \\ / : 等路径字符）。"""
    return bool(trace_id) and _TRACE_ID_RE.match(trace_id) is not None


class Tracer:
    def __init__(self, trace_dir: str | Path | None = None) -> None:
        self.dir = Path(trace_dir or TRACE_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)

    def start(self) -> str:
        return uuid.uuid4().hex[:12]

    def _path(self, trace_id: str) -> Path | None:
        """仅接受合法 trace_id，并确保解析后的路径仍位于 trace 目录内。"""
        if not is_valid_trace_id(trace_id):
            return None
        candidate = (self.dir / f"{trace_id}.jsonl").resolve()
        try:
            candidate.relative_to(self.dir.resolve())
        except ValueError:
            return None  # 归一化后逃出目录 → 拒绝（纵深防御）
        return candidate

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
        path = self._path(trace_id)
        if path is None:
            return  # 非法 trace_id 直接丢弃，不落盘
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
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def get(self, trace_id: str) -> list[dict]:
        path = self._path(trace_id)
        if path is None or not path.exists():
            return []
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
