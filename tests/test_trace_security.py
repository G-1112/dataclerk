r"""Trace 路径穿越回归测试（第四轮安全修复）。

实测发现（修复前）：
    GET /trace/..\HTTPPROBE   →  HTTP 200，返回了 data/traces 目录之外的
                                    .jsonl 文件内容。
Windows 下反斜杠是路径分隔符，而 FastAPI 的单段路径参数不会拦截它，
于是 trace_id 被直接拼进文件路径造成目录穿越。

修复：trace_id 必须匹配 ^[0-9a-fA-F]{1,64}$（Tracer.start() 生成的是
uuid4().hex[:12]），并在拼接后再用 resolve()+relative_to 做一次目录归属校验。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.serve import app
from agent.trace import TRACE_DIR, Tracer, is_valid_trace_id


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def outside_file():
    """在 traces 目录之外放一个探针 .jsonl。"""
    probe = Path(TRACE_DIR).parent / "TRAVERSAL_PROBE.jsonl"
    probe.write_text(
        json.dumps({"leak": "目录外内容", "token": "sk-DEMO"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    yield probe
    probe.unlink(missing_ok=True)


# ------------------------------------------------------------ trace_id 校验
def test_valid_trace_id_accepts_hex():
    assert is_valid_trace_id("698849b6a591")
    assert is_valid_trace_id("ABCDEF012345")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "..",
        "../SECRET",
        "..\\SECRET",
        "..\\..\\windows\\win.ini",
        "a/b",
        "a\\b",
        "C:\\Windows\\win.ini",
        "trace id",          # 空格
        "trace.jsonl",       # 点
        "..%2fSECRET",
        "x" * 65,            # 超长
    ],
)
def test_invalid_trace_id_rejected(bad):
    assert not is_valid_trace_id(bad), f"{bad!r} 不应通过校验"


# ------------------------------------------------------------ 目录穿越（库层）
def test_tracer_get_rejects_traversal(outside_file):
    t = Tracer()
    assert t.get("..\\TRAVERSAL_PROBE") == []
    assert t.get("../TRAVERSAL_PROBE") == []


def test_tracer_record_rejects_traversal(outside_file):
    """非法 trace_id 不得写入任何文件（不落盘）。"""
    t = Tracer()
    before = outside_file.read_text(encoding="utf-8")
    t.record("..\\TRAVERSAL_PROBE", 1, "t", "tool", {}, "obs")
    assert outside_file.read_text(encoding="utf-8") == before, "非法 id 竟然写入了目录外文件"


def test_tracer_still_works_for_normal_id():
    t = Tracer()
    tid = t.start()
    t.record(tid, 1, "think", "get_schema", {}, "ok")
    steps = t.get(tid)
    assert len(steps) == 1
    assert steps[0]["tool"] == "get_schema"


# ------------------------------------------------------------ 目录穿越（HTTP 层）
@pytest.mark.parametrize(
    "payload",
    ["..\\TRAVERSAL_PROBE", "../TRAVERSAL_PROBE", "..\\..\\windows\\win.ini", "TRAVERSAL_PROBE"],
)
def test_http_trace_endpoint_does_not_leak(client, outside_file, payload):
    r = client.get(f"/trace/{payload}")
    # 允许 200 + 空结果，或 404；但绝不能返回目录外文件内容
    assert r.status_code in (200, 404), r.status_code
    if r.status_code == 200:
        assert r.json()["steps"] == [], f"{payload!r} 泄露了目录外内容"


def test_http_trace_endpoint_returns_real_trace(client):
    """正常 trace 仍可经 HTTP 读取（修复不得破坏功能）。"""
    from agent.serve import agent

    tid = agent.tracer.start()
    agent.tracer.record(tid, 1, "think", "get_schema", {}, "ok")
    r = client.get(f"/trace/{tid}")
    assert r.status_code == 200
    assert len(r.json()["steps"]) == 1
