"""轻量内存限流（第四轮新增）。

目的：/chat 每次调用都会触发 LLM 调用与数据库查询，此前**完全无速率限制**，
可被高频刷取耗尽 LLM 配额与数据库连接（实测连续 40 次请求全部 200，无 429）。

设计取舍（与 api/ratelimit.py 同构，保持三项目实现一致）：
- 按客户端 IP 分桶，滑动窗口计数，无第三方依赖；
- 窗口内过期记录自动清理，内存不随调用量增长；
- 单进程内存态，多 worker 部署时应换成 Redis 等共享存储（见 README 部署约束）。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque

# 单条 question 的字符上限。超长问题会被拼进 prompt，
# 既放大 token 成本，也可能掩盖注入意图（实测 44000 字可被接受）。
MAX_QUESTION_CHARS = 2000


class RateLimiter:
    """滑动窗口限流器：窗口内最多放行 max_calls 次。"""

    def __init__(self, max_calls: int, window_seconds: float = 60.0) -> None:
        self.max_calls = max_calls
        self.window = window_seconds
        self._hits: dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        """是否放行；放行即记一次。"""
        now = time.time()
        q = self._hits[key]
        while q and (now - q[0]) > self.window:
            q.popleft()
        if len(q) >= self.max_calls:
            return False
        q.append(now)
        return True

    def retry_after(self, key: str) -> int:
        """距离可重试还需多少秒（用于 Retry-After 响应头）。"""
        q = self._hits.get(key)
        if not q or len(q) < self.max_calls:
            return 0
        return max(1, int(self.window - (time.time() - q[0])) + 1)

    def reset(self, key: str | None = None) -> None:
        """清空计数（测试用）。"""
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)


# 对话：每次触发 LLM + DB，单 IP 每分钟 30 次
chat_limiter = RateLimiter(max_calls=30, window_seconds=60.0)


def client_ip(request) -> str:
    """取客户端 IP；取不到时退回统一桶（仍能限流，只是粒度变粗）。"""
    try:
        return request.client.host if request.client else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"
