"""端点探针的测试（2026-10-28 新增）。

探针的价值在于**给出可操作的结论**，而不是只回一句"失败"。所以这里按结论分支逐条测：
- 正常端点 → `ready`，且四条冒烟真的跑了；
- 端口没人听 → `unreachable`（用"先占端口再关掉"的确定做法，不依赖外网）；
- key 错 → `auth_failed`（用本地协议桩返回 401）；
- `/v1` 归一 → 带不带 `/v1`、带子路径都不能拼出 `//v1`。
"""

from __future__ import annotations

import socket
import sys
import threading

import pytest

from examples.openai_stub.server import make_server
from testsuite import probe_endpoint as P


@pytest.fixture()
def stub():
    servers = []

    def _start(brain: str = "guarded", api_key: str = ""):
        srv = make_server(0, None, brain, api_key)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_port}/v1"

    yield _start
    for s in servers:
        s.shutdown()
        s.server_close()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_probe_ready_and_runs_smoke(stub):
    rep = P.probe(stub(), "stub", timeout=5)
    assert rep["verdict"] == "ready", rep
    steps = {c["step"]: c for c in rep["checks"]}
    assert {"tcp", "models", "minimal_chat", "smoke"} <= set(steps)
    assert len(steps["smoke"]["samples"]) == len(P.SMOKE_QUESTIONS)
    assert all(s["ok"] for s in steps["smoke"]["samples"])
    assert "run_external" in rep["advice"]


def test_probe_unreachable_returns_actionable_advice():
    port = _free_port()
    rep = P.probe(f"http://127.0.0.1:{port}/v1", "stub", timeout=2)
    assert rep["verdict"] == "unreachable"
    assert "端口" in rep["advice"]


def test_probe_auth_failed_on_bad_key(stub):
    base = stub(api_key="sk-right")
    rep = P.probe(base, "stub", api_key="sk-wrong", timeout=5)
    assert rep["verdict"] == "auth_failed", rep
    assert "鉴权" in rep["advice"]


def test_probe_protocol_mismatch_on_non_openai_response(stub, monkeypatch):
    """端点活着但响应不是 chat.completion 形状 → 必须判"协议不匹配"，而不是笼统失败。"""
    from testsuite.adapters import openai_chat as A

    monkeypatch.setattr(A.OpenAIChatAdapter, "_post", lambda self, payload: {"object": "chat.completion"})
    rep = P.probe(stub(), "stub", timeout=5)
    assert rep["verdict"] == "protocol_mismatch", rep


def test_join_normalizes_v1():
    assert P._join("http://h", "/v1/models") == "http://h/v1/models"
    assert P._join("http://h/", "/v1/models") == "http://h/v1/models"
    assert P._join("http://h/v1", "/v1/models") == "http://h/v1/models"
    assert P._join("http://h/api/v1", "/v1/models") == "http://h/api/v1/models"


def test_model_not_in_list_gets_note(stub, monkeypatch):
    monkeypatch.setattr(P, "_get_models", lambda *a, **k: (["qwen2.5:7b", "llama3:8b"], "mocked"))
    rep = P.probe(stub(), "not-exist", timeout=5)
    assert "qwen2.5:7b" in rep["advice_model"]


def test_cli_exit_code_and_json(stub, tmp_path, monkeypatch):
    out = tmp_path / "probe.json"
    monkeypatch.setattr(sys, "argv", [
        "probe", "--base", stub(), "--model", "stub", "--timeout", "5",
        "--json-out", str(out),
    ])
    assert P.main() == 0
    assert out.exists() and "verdict" in out.read_text(encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "probe", "--base", f"http://127.0.0.1:{_free_port()}/v1", "--model", "stub",
        "--timeout", "2",
    ])
    assert P.main() == 1
