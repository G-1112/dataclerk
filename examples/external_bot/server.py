"""把演示机器人暴露成 HTTP 服务（仅标准库）。

    python -m examples.external_bot.server --port 8901
    curl -s localhost:8901/chat -d '{"question":"计算机学院有多少学生？"}'
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .bot import NaiveCampusBot

_BOT: NaiveCampusBot | None = None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - 标准库命名
        if self.path.rstrip("/") != "/chat":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid json"})
            return
        if not isinstance(payload, dict):
            self._send(400, {"error": "payload must be object"})
            return

        assert _BOT is not None
        result = _BOT.handle(
            question=str(payload.get("question") or ""),
            role=str(payload.get("role") or "admin"),
            user_id=str(payload.get("user_id") or ""),
            session_id=str(payload.get("session_id") or ""),
        )
        self._send(200, result)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            self._send(200, {"ok": True, "bot": _BOT.name if _BOT else None})
        else:
            self._send(404, {"error": "not found"})

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # 静默访问日志
        return

    def handle_one_request(self) -> None:
        """客户端提前断连（探活/超时）属正常，不该打整屏 traceback。"""
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True


def make_server(port: int = 0, db_path: str | None = None) -> ThreadingHTTPServer:
    """创建（未启动的）服务实例；port=0 时由系统分配，便于单测。"""
    global _BOT
    _BOT = NaiveCampusBot(db_path=db_path)
    return ThreadingHTTPServer(("127.0.0.1", port), _Handler)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8901)
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    srv = make_server(args.port, args.db)
    print(f"外部演示智能体已启动：http://127.0.0.1:{srv.server_port}/chat")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
