"""可插拔适配器层测试（2026-10-28 新增：通用性补强）。

覆盖三件必须成立的事：
1. 契约规范化对**残缺返回**宽容（第三方智能体给什么字段都不该让框架崩）；
2. HTTP 适配器对**连不上**的场景 fail-soft（判用例失败，不抛异常中断跑批）；
3. 端到端：不改被测方代码，就能用同一套用例跑出维度分与缺陷清单。
"""

from __future__ import annotations

import threading

import pytest

from testsuite.adapters import HttpAgentAdapter, LocalAgentAdapter, get_adapter, normalize_result
from testsuite.adapters.http_agent import _dig


# ---------------------------------------------------------------------------
# 契约规范化
# ---------------------------------------------------------------------------
def test_normalize_accepts_plain_string():
    r = normalize_result("计算机学院共有 943 名学生。")
    assert r["answer"].startswith("计算机学院")
    assert r["trace"] == [] and r["rows"] == []


def test_normalize_tolerates_none_and_non_dict():
    assert normalize_result(None)["answer"] == ""
    assert normalize_result(123)["answer"] == "123"


def test_normalize_guesses_channel_when_missing():
    assert normalize_result("抱歉，无权访问该数据")["channel"] == "deny"
    assert normalize_result("问题不够明确，请补充学院")["channel"] == "clarify"
    assert normalize_result("根据《奖学金评定办法》……")["channel"] == "rag"
    assert normalize_result("943")["channel"] == "unknown"


def test_normalize_keeps_explicit_channel():
    assert normalize_result({"answer": "无权", "channel": "sql"})["channel"] == "sql"


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
def test_get_adapter_rejects_unknown_kind():
    with pytest.raises(ValueError, match="未知适配器"):
        get_adapter("no-such-adapter")


def test_local_adapter_wraps_campus_agent():
    a = get_adapter("local")
    assert isinstance(a, LocalAgentAdapter)
    assert a.supports_sql is True


# ---------------------------------------------------------------------------
# HTTP 适配器
# ---------------------------------------------------------------------------
def test_dig_nested_path():
    assert _dig({"data": {"answer": "hi"}}, "data.answer") == "hi"
    assert _dig({"a": 1}, "b.c") is None
    assert _dig({"a": 1}, "") is None


def test_http_adapter_fail_soft_on_unreachable():
    """连不上时必须返回一条"查询失败"回答，而不是抛异常。

    否则 run_boundary 会捕获异常记为 graceful=False——虽然结果同样是失败，
    但丢失了"服务不可用"与"回答不优雅"的区分，排障方向会被带偏。
    """
    a = HttpAgentAdapter(url="http://127.0.0.1:1/chat", timeout=0.5)
    r = a.run("计算机学院有多少学生？")
    assert r["answer"].startswith("查询失败：远端智能体不可用")
    assert r["channel"] == "error"
    assert r["trace"] and r["trace"][0]["tool"] == "http_call"


@pytest.fixture()
def demo_server(tmp_path):
    """起一个演示机器人（自带缺陷），供端到端用例复用。"""
    from examples.external_bot.server import make_server

    srv = make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}/chat"
    finally:
        srv.shutdown()
        srv.server_close()


def test_http_adapter_against_demo_bot(demo_server):
    a = HttpAgentAdapter(url=demo_server, timeout=5)
    r = a.run("计算机学院有多少学生？")
    assert "计算机学院" in r["answer"]
    assert r["channel"] in ("sql", "unknown", "rag", "deny", "clarify")


def test_external_run_detects_defects(demo_server):
    """端到端：同一套用例 + HTTP 适配器，应在演示机器人上抓出多类缺陷。"""
    from testsuite.run_external import evaluate

    rep = evaluate(demo_server, timeout=5, request_map={}, response_map={})
    assert set(rep["dimensions"]) == {"security_behavioral", "routing", "boundary"}
    # 演示机器人是刻意保留初级缺陷的朴素实现，至少应抓出 3 类
    assert rep["defect_count"] >= 3, rep["defect_classes"]
    # 工具级用例必须被显式跳过并记录，而不是混进外部结果里"假通过"
    assert rep["skipped_cases"], "应显式列出被跳过的工具级用例"
    assert all(d["defect"] for d in rep["failures"])
