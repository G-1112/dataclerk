"""OpenAI 兼容协议适配器测试（2026-10-28 新增：把「测真实开源智能体」落到实处）。

覆盖两类：
- **正常路径**：协议请求形状正确、响应正文能被解析、身份写进 system 消息；
- **边界场景**：鉴权失败 401、服务不可达、响应缺字段、`content` 为分段结构、
  `choices[0].text` 变体、服务不返回通道字段时路由维度必须标"不适用"（而不是凑分）。
"""

from __future__ import annotations

import json
import threading

import pytest

from testsuite.adapters import OpenAIChatAdapter, get_adapter


# ---------------------------------------------------------------------------
# 请求构造（不依赖网络）
# ---------------------------------------------------------------------------
def test_endpoint_accepts_base_with_and_without_v1():
    assert OpenAIChatAdapter("http://h:8000", "m").endpoint == "http://h:8000/v1/chat/completions"
    # 已经带 /v1 的 base 不能再拼一次（否则变成 /v1/v1/...）
    assert OpenAIChatAdapter("http://h:8000/v1", "m").endpoint == "http://h:8000/v1/chat/completions"
    # 结尾斜杠要被归一
    assert OpenAIChatAdapter("http://h:8000/v1/", "m").endpoint == "http://h:8000/v1/chat/completions"


def test_missing_base_or_model_rejected():
    with pytest.raises(ValueError):
        OpenAIChatAdapter("", "m")
    with pytest.raises(ValueError):
        OpenAIChatAdapter("http://h", "")


def test_identity_is_written_into_system_message():
    a = OpenAIChatAdapter("http://h", "m")
    msgs = a.build_messages("计算机学院有多少学生？", "student", "S00001")
    assert msgs[0]["role"] == "system"
    assert "role=student" in msgs[0]["content"]
    assert "user_id=S00001" in msgs[0]["content"]
    assert msgs[1] == {"role": "user", "content": "计算机学院有多少学生？"}


def test_identity_can_be_turned_off():
    a = OpenAIChatAdapter("http://h", "m", session_in_prompt=False)
    msgs = a.build_messages("q", "student", "S00001")
    assert "S00001" not in msgs[0]["content"]


def test_registered_as_openai_kind():
    a = get_adapter("openai", base_url="http://h", model="m")
    assert isinstance(a, OpenAIChatAdapter)
    assert a.name == "openai-compatible"
    # 纯对话协议没有 SQL，功能维度应由调用方跳过
    assert a.supports_sql is False


# ---------------------------------------------------------------------------
# 响应解析（脏数据容忍）
# ---------------------------------------------------------------------------
def test_extract_content_standard_shape():
    raw = {"choices": [{"message": {"role": "assistant", "content": "943 人"}}]}
    assert OpenAIChatAdapter._extract_content(raw) == "943 人"


def test_extract_content_from_segmented_list():
    """部分兼容实现把 content 返回成分段数组。"""
    raw = {"choices": [{"message": {"content": [{"type": "text", "text": "94"}, {"type": "text", "text": "3"}]}}]}
    assert OpenAIChatAdapter._extract_content(raw) == "943"


def test_extract_content_from_text_field():
    assert OpenAIChatAdapter._extract_content({"choices": [{"text": "legacy"}]}) == "legacy"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"choices": []},
        {"choices": "nope"},
        {"choices": [None]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": []}}]},
    ],
)
def test_extract_content_returns_none_on_garbage(raw):
    assert OpenAIChatAdapter._extract_content(raw) is None


# ---------------------------------------------------------------------------
# 失败路径必须 fail-soft（判失败，不炸整批）
# ---------------------------------------------------------------------------
def test_unreachable_service_fails_soft():
    a = OpenAIChatAdapter("http://127.0.0.1:1", "m", timeout=0.5)
    r = a.run("计算机学院有多少学生？")
    assert r["answer"].startswith("查询失败：远端智能体调用失败")
    assert r["channel"] == "error"
    assert r["trace"][0]["tool"] == "openai_chat"


def test_garbage_response_fails_soft(monkeypatch):
    a = OpenAIChatAdapter("http://h", "m")
    monkeypatch.setattr(a, "_post", lambda payload: {"object": "chat.completion"})
    r = a.run("q")
    assert "缺少 choices[0].message.content" in r["answer"]
    assert r["channel"] == "error"


# ---------------------------------------------------------------------------
# 端到端：本地 OpenAI 协议桩
# ---------------------------------------------------------------------------
# 跑整套外部用例（130+ 次 HTTP 往返）时的超时。桩是确定性实现，正常情况下单次
# 请求是毫秒级；给 10s 是为了吃住"机器被别的任务占满"时的调度抖动——否则会偶发
# 单条用例超时，把"能用"误判成"不可用"。（单点用例仍用 5s，保持对慢响应的敏感度。）
STUB_SUITE_TIMEOUT = 10


@pytest.fixture(scope="module")
def stub_server():
    from examples.openai_stub.server import make_server

    def _start(brain: str = "guarded", api_key: str = ""):
        srv = make_server(0, None, brain, api_key)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    servers = []

    def _factory(**kwargs):
        srv = _start(**kwargs)
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_port}"

    yield _factory
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def test_stub_end_to_end_answers_question(stub_server):
    base = stub_server(brain="guarded")
    a = OpenAIChatAdapter(base, "stub", channel_path="channel")
    r = a.run("计算机学院有多少学生？", role="admin")
    assert r["channel"] == "sql"
    assert "计算机学院" in r["answer"]
    assert r["trace"][0]["latency_ms"] >= 0


def test_stub_channel_field_is_opt_in(stub_server):
    """服务返回了 channel，但没配 channel_path 时必须忽略它——否则等于白送路由分。"""
    base = stub_server(brain="guarded")
    a = OpenAIChatAdapter(base, "stub")
    assert a.supports_channel is False
    r = a.run("根据规定，奖学金怎么评定？")
    assert r["channel"] == "rag"  # 只能由正文推断（文本含"规定"）
    assert r["channel"] != "sql"


def test_stub_rejects_wrong_api_key(stub_server):
    base = stub_server(brain="guarded", api_key="sk-right")
    bad = OpenAIChatAdapter(base, "stub", api_key="sk-wrong", timeout=5)
    r = bad.run("计算机学院有多少学生？")
    assert "HTTP 401" in r["answer"]
    assert r["channel"] == "error"

    good = OpenAIChatAdapter(base, "stub", api_key="sk-right", timeout=5)
    ok = good.run("计算机学院有多少学生？")
    assert "计算机学院" in ok["answer"]
    assert not ok["answer"].startswith("查询失败")


def test_stub_validates_payload_shape(stub_server):
    """协议桩按 OpenAI 规范校验；适配器发的包必须一次就过。"""
    import urllib.error
    import urllib.request

    base = stub_server(brain="guarded")
    body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
    req = urllib.request.Request(
        f"{base}/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 400

    a = OpenAIChatAdapter(base, "stub", timeout=5)
    assert not a.run("计算机学院有多少学生？")["answer"].startswith("查询失败")


# ---------------------------------------------------------------------------
# 端到端：协议桩 + 同一套外部用例（两个对照大脑）
# ---------------------------------------------------------------------------
def test_external_suite_on_openai_guarded_brain(stub_server):
    """修好的智能体应接近满分——证明用例不是"见谁都判失败"。"""
    from testsuite.run_external import evaluate

    a = OpenAIChatAdapter(stub_server(brain="guarded"), "stub", channel_path="channel",
                          timeout=STUB_SUITE_TIMEOUT)
    rep = evaluate(a)
    assert rep["adapter"] == "openai-compatible"
    assert rep["dimensions"]["security_behavioral"]["rate"] == 1.0
    assert rep["dimensions"]["routing"]["rate"] == 1.0
    assert rep["dimensions"]["boundary"]["rate"] == 1.0
    assert rep["defect_count"] == 0, rep["defect_classes"]


def test_external_suite_on_openai_naive_brain(stub_server):
    """朴素智能体应被检出多类缺陷——证明用例有牙齿。"""
    from testsuite.run_external import evaluate

    a = OpenAIChatAdapter(stub_server(brain="naive"), "stub", channel_path="channel",
                          timeout=STUB_SUITE_TIMEOUT)
    rep = evaluate(a)
    assert rep["defect_count"] >= 3, rep["defect_classes"]
    assert rep["dimensions"]["security_behavioral"]["rate"] < 0.2


def test_routing_marked_not_applicable_without_channel_path(stub_server):
    """标准 OpenAI 协议不暴露通道 → 路由维度必须诚实标"不适用"。"""
    from testsuite.run_external import evaluate

    a = OpenAIChatAdapter(stub_server(brain="guarded"), "stub", timeout=STUB_SUITE_TIMEOUT)
    rep = evaluate(a)
    assert "routing" not in rep["dimensions"]
    assert "routing_not_applicable" in rep
    # 其余两个维度照常评
    assert rep["dimensions"]["security_behavioral"]["total"] > 0
    assert rep["dimensions"]["boundary"]["total"] > 0
