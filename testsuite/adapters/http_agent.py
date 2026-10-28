"""通用 HTTP 智能体适配器：让本框架能测任意「HTTP + JSON」的智能体服务。

设计约束
--------
- **零第三方依赖**（urllib），评委环境无需额外安装即可复现；
- **不改被测方代码**：只要求对方有一个接受 JSON POST 的端点，字段名不同就用
  `request_map` / `response_map` 映射，支持 `data.answer` 这种嵌套路径；
- **失败即降级**：超时/非 200/JSON 解析失败都转成一条 error 回答返回，
  让该条用例判失败，而不是让整个跑批抛异常中断。

典型用法
--------
    python -m testsuite.run_external --url http://127.0.0.1:8901/chat \\
        --request-map question=q --response-map answer=data.answer
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

from .base import normalize_result

# 我方契约字段 → 对方请求体字段（默认同名）
_DEFAULT_REQUEST_MAP = {
    "question": "question",
    "role": "role",
    "session_id": "session_id",
    "user_id": "user_id",
}
# 我方契约字段 → 对方响应体路径（默认同名，支持 a.b.c 嵌套）
_DEFAULT_RESPONSE_MAP = {"answer": "answer", "channel": "channel"}


class HttpAgentAdapter:
    name = "http-remote"
    supports_sql = False  # 远端 SQL 能力未知，默认不跑功能准确性维度（可显式打开）

    def __init__(
        self,
        url: str,
        timeout: float = 10.0,
        headers: dict[str, str] | None = None,
        request_map: dict[str, str] | None = None,
        response_map: dict[str, str] | None = None,
        supports_sql: bool = False,
    ) -> None:
        if not url:
            raise ValueError("HttpAgentAdapter 需要 url")
        self.url = url
        self.timeout = timeout
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.request_map = {**_DEFAULT_REQUEST_MAP, **(request_map or {})}
        self.response_map = {**_DEFAULT_RESPONSE_MAP, **(response_map or {})}
        self.supports_sql = supports_sql

    # ------------------------------------------------------------------
    def run(
        self,
        question: str,
        role: str = "admin",
        session_id: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        payload = {
            self.request_map["question"]: question,
            self.request_map["role"]: role,
            self.request_map["session_id"]: session_id,
            self.request_map["user_id"]: user_id,
        }
        t0 = time.time()
        try:
            raw = self._post(payload)
        except Exception as e:  # noqa: BLE001
            # 统一成"查询失败"前缀：与内置智能体的失败表达一致，
            # 边界维度的「优雅降级」判定据此识别（服务挂了不算优雅）。
            return normalize_result(
                {
                    "answer": f"查询失败：远端智能体不可用（{type(e).__name__}: {e}）",
                    "channel": "error",
                    "trace": [
                        {
                            "step": 1,
                            "tool": "http_call",
                            "args": {"url": self.url},
                            "observation": f"{type(e).__name__}: {e}",
                            "latency_ms": int((time.time() - t0) * 1000),
                        }
                    ],
                }
            )

        result = {
            "answer": _dig(raw, self.response_map["answer"]),
            "channel": _dig(raw, self.response_map.get("channel", "")),
            "sql": _dig(raw, self.response_map.get("sql", "")) if "sql" in self.response_map else None,
            "rows": _dig(raw, self.response_map.get("rows", "")) if "rows" in self.response_map else [],
            "trace_id": _dig(raw, self.response_map.get("trace_id", "")) if "trace_id" in self.response_map else "",
        }
        if result["answer"] is None:
            result["answer"] = f"查询失败：响应缺少字段 {self.response_map['answer']}"
        result["trace"] = [
            {
                "step": 1,
                "tool": "http_call",
                "args": {"url": self.url},
                "observation": str(result.get("answer"))[:120],
                "latency_ms": int((time.time() - t0) * 1000),
            }
        ]
        return normalize_result(result)

    # ------------------------------------------------------------------
    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self.headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - 显式由使用者给定 URL
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            body = resp.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError(f"响应不是 JSON 对象：{type(data).__name__}")
        return data


def _dig(obj: Any, path: str) -> Any:
    """按 `a.b.c` 取嵌套字段；空路径或缺失返回 None。"""
    if not path:
        return None
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur
