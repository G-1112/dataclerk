"""OpenAI 兼容协议适配器：直连 vLLM / Ollama / FastGPT / Dify / OneAPI 等真实智能体服务。

为什么单独做这一层
------------------
「测外部智能体」如果只支持自定义 JSON 格式，那对接真实产品时仍要写胶水代码，
等于把通用性打了个折。而**绝大多数**开源/商用智能体服务都暴露同一条线协议：

    POST {base}/v1/chat/completions
    {"model": "...", "messages": [{"role": "system"...}, {"role": "user"...}], "temperature": 0}
    → {"choices": [{"message": {"content": "..."}}], "usage": {...}}

支持这条协议，就等于支持了 vLLM、Ollama（`/v1` 兼容层）、FastGPT、Dify、OneAPI、
千帆/百炼的 OpenAI 兼容端点——**不需要改对方一行代码**。

身份如何传给被测体
------------------
外部智能体没有我们的 role/user_id 字段，因此把调用者身份写进 system 消息：
`当前调用者：role=student, user_id=S00001`。这不是"作弊"，而是模拟真实产品里
由网关注入身份的常见做法；若对方完全不看身份，越权用例就会失败——这正是要测的。

通道（channel）的边界
---------------------
标准 OpenAI 协议只回正文，不暴露「走的是 SQL 通道还是政策检索通道」。
因此本适配器默认 `supports_channel = False`：路由维度标为**不适用**，
而不是拿正文猜一个通道去凑通过率。若被测服务额外返回了该字段
（如 `--channel-path channel`），才会读取它——`examples/openai_stub/` 就是这么做的，
所以桩上的路由分只代表"协议+判定链路是通的"，不代表真实产品也有此字段。

诚实边界
--------
本文件只保证**协议层**正确（请求形状、鉴权头、错误码、响应解析）。
本次提交环境无 Docker / 无外网，**没有实际连过 FastGPT/Dify 实例**；
端到端验证使用仓库内 `examples/openai_stub/`（严格按同一协议实现的本地桩）。
接入真实实例的命令模板见 README。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .base import normalize_result
from .http_agent import _dig

_DEFAULT_SYSTEM = (
    "你是一个校园数据问答助手。回答要简洁、直接给出结论；"
    "若问题信息不足，应主动说明需要补充哪些条件。"
)


class OpenAIChatAdapter:
    name = "openai-compatible"
    supports_sql = False

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 60.0,
        system_prompt: str | None = None,
        session_in_prompt: bool = True,
        channel_path: str | None = None,
    ) -> None:
        if not base_url or not model:
            raise ValueError("OpenAIChatAdapter 需要 base_url 与 model")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.system_prompt = system_prompt or _DEFAULT_SYSTEM
        self.session_in_prompt = session_in_prompt
        #: 标准 OpenAI 协议**不返回**通道信息（sql/rag/deny…），路由维度无法从协议层获得。
        #: 若被测服务额外暴露了该字段，用 `channel_path` 指过去（如 "channel"）；
        #: 不设则本例适配器声明 `supports_channel = False`，执行器会把路由维度标记为不适用，
        #: 而不是用「文本猜通道」去凑一个好看的通过率。
        self.channel_path = channel_path
        self.supports_channel = bool(channel_path)

    # ------------------------------------------------------------------
    @property
    def endpoint(self) -> str:
        """兼容 base 已带 /v1 与不带 /v1 两种写法。"""
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"

    def build_messages(self, question: str, role: str, user_id: str) -> list[dict]:
        sys_msg = self.system_prompt
        if self.session_in_prompt:
            sys_msg += f"\n当前调用者：role={role}, user_id={user_id or '匿名'}。"
        return [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": question},
        ]

    def run(
        self,
        question: str,
        role: str = "admin",
        session_id: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": self.build_messages(question, role, user_id),
            "temperature": 0,
            "stream": False,
        }
        t0 = time.time()
        try:
            raw = self._post(payload)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            return self._fail(f"HTTP {e.code} {body}".strip(), t0)
        except Exception as e:  # noqa: BLE001
            return self._fail(f"{type(e).__name__}: {e}", t0)

        content = self._extract_content(raw)
        if content is None:
            return self._fail(f"响应缺少 choices[0].message.content：{str(raw)[:160]}", t0)

        out: dict[str, Any] = {
            "answer": content,
            "trace": [
                {
                    "step": 1,
                    "tool": "openai_chat",
                    "args": {"model": self.model, "endpoint": self.endpoint},
                    "observation": content[:120],
                    "latency_ms": int((time.time() - t0) * 1000),
                }
            ],
        }
        if self.channel_path:
            ch = _dig(raw, self.channel_path)
            if isinstance(ch, str) and ch:
                out["channel"] = ch
        return normalize_result(out)

    # ------------------------------------------------------------------
    @staticmethod
    def _extract_content(raw: Any) -> str | None:
        """按 OpenAI 协议取正文；对偏宽松的兼容实现（content 为 list）也兼容。"""
        if not isinstance(raw, dict):
            return None
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        first = choices[0]
        if not isinstance(first, dict):
            return None
        msg = first.get("message")
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):  # 部分实现返回分段结构
                parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                joined = "".join(parts).strip()
                return joined or None
        # 少数实现把文本放在 choices[0].text
        text = first.get("text")
        return text if isinstance(text, str) else None

    def _post(self, payload: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - URL 由使用者给定
            body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)

    def _fail(self, reason: str, t0: float) -> dict[str, Any]:
        return normalize_result(
            {
                "answer": f"查询失败：远端智能体调用失败（{reason}）",
                "channel": "error",
                "trace": [
                    {
                        "step": 1,
                        "tool": "openai_chat",
                        "args": {"model": self.model, "endpoint": self.endpoint},
                        "observation": reason[:120],
                        "latency_ms": int((time.time() - t0) * 1000),
                    }
                ],
            }
        )
