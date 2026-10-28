"""OpenAI 兼容协议桩服务（仅标准库）。

    # 起桩（朴素大脑，默认）
    python -m examples.openai_stub.server --port 8911

    # 换成"已修好"的大脑，验证框架不会冤枉好智能体
    python -m examples.openai_stub.server --port 8912 --brain guarded

    # 然后让测试框架直接按 OpenAI 协议测它
    python -m testsuite.run_external --openai-base http://127.0.0.1:8911 \\
        --openai-model stub --channel-path channel

协议实现要点（严格照 OpenAI Chat Completions）
---------------------------------------------
- 路径：`POST /v1/chat/completions`；
- 鉴权：配置了 `--api-key` 时校验 `Authorization: Bearer <key>`，否则 401；
- 请求校验：`model` 必填且为字符串，`messages` 非空数组，每条含 `role`/`content`，否则 400；
- 响应：`{id, object, created, model, choices[{index, message{role,content}, finish_reason}], usage}`；
- 错误：`{"error": {"message", "type", "code"}}`（与真实服务同构，便于适配器解析）。

`channel` 是本桩**额外**返回的字段（标准协议没有），只为让路由维度可测；
真实服务不返回该字段时，`OpenAIChatAdapter` 会把路由维度标记为不适用。
"""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .brains import get_brain

_ID_RE = re.compile(r"role=([A-Za-z_]+)\s*,\s*user_id=([^\s,，。]+)")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # 由 make_server 注入
    brain = None
    api_key = ""

    def do_POST(self) -> None:  # noqa: N802 - 标准库命名
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._err(404, "Unknown path", "invalid_request_error", "path_not_found")
            return

        if self.api_key:
            auth = self.headers.get("Authorization") or ""
            if auth != f"Bearer {self.api_key}":
                self._err(401, "Invalid API key", "invalid_request_error", "invalid_api_key")
                return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._err(400, "Invalid JSON body", "invalid_request_error", "invalid_json")
            return

        problem = _validate(payload)
        if problem:
            self._err(400, problem, "invalid_request_error", "invalid_payload")
            return

        messages = payload["messages"]
        question, role, user_id = _split_messages(messages)
        assert self.brain is not None
        result = self.brain.handle(question, role=role, user_id=user_id)
        answer = str(result.get("answer") or "")

        self._send(
            200,
            {
                "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": payload.get("model") or "stub",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": sum(len(m["content"]) for m in messages),
                    "completion_tokens": len(answer),
                    "total_tokens": sum(len(m["content"]) for m in messages) + len(answer),
                },
                # 非标准扩展字段：仅供路由维度评估
                "channel": result.get("channel") or "unknown",
            },
        )

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            self._send(200, {"ok": True, "brain": getattr(self.brain, "name", None)})
        else:
            self._err(404, "Unknown path", "invalid_request_error", "path_not_found")

    # ------------------------------------------------------------------
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _err(self, code: int, message: str, etype: str, ecode: str) -> None:
        self._send(code, {"error": {"message": message, "type": etype, "code": ecode}})

    def log_message(self, *args) -> None:  # 静默访问日志
        return

    def handle_one_request(self) -> None:
        """客户端提前断连（探活/超时）属正常，不该打整屏 traceback。"""
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True


def _validate(payload: dict) -> str | None:
    """按协议校验请求体；返回错误描述或 None。"""
    if not isinstance(payload, dict):
        return "'body' must be a JSON object"
    model = payload.get("model")
    if not isinstance(model, str) or not model:
        return "'model' is required and must be a string"
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return "'messages' must be a non-empty array"
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            return f"'messages[{i}]' must be an object"
        if not isinstance(m.get("role"), str) or not m.get("role"):
            return f"'messages[{i}].role' is required"
        if not isinstance(m.get("content"), str):
            return f"'messages[{i}].content' must be a string"
    return None


def _split_messages(messages: list[dict]) -> tuple[str, str, str]:
    """从 messages 里取出「用户问题」与「调用者身份」。

    身份是适配器写在 system 消息里的 `role=student, user_id=S00001`；
    取不到就按 admin 处理（与真实网关在缺省情况下的宽松行为一致）。
    """
    question = ""
    role, user_id = "admin", ""
    for m in messages:
        if m.get("role") == "system":
            hit = _ID_RE.search(m.get("content") or "")
            if hit:
                role, user_id = hit.group(1), hit.group(2)
        elif m.get("role") == "user":
            question = m.get("content") or ""
    return question, role, user_id


def make_server(
    port: int = 0,
    db_path: str | None = None,
    brain: str = "naive",
    api_key: str = "",
) -> ThreadingHTTPServer:
    """创建（未启动的）协议桩；port=0 时由系统分配，便于单测。"""
    handler = type("_BoundHandler", (_Handler,), {"brain": get_brain(brain, db_path), "api_key": api_key})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main() -> None:
    ap = argparse.ArgumentParser(description="OpenAI 兼容协议桩")
    ap.add_argument("--port", type=int, default=8911)
    ap.add_argument("--db", default=None)
    ap.add_argument("--brain", default="naive", choices=["naive", "guarded"])
    ap.add_argument("--api-key", default="", help="非空则校验 Authorization: Bearer")
    args = ap.parse_args()
    srv = make_server(args.port, args.db, args.brain, args.api_key)
    print(f"OpenAI 协议桩已启动：http://127.0.0.1:{srv.server_port}/v1/chat/completions  (brain={args.brain})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
