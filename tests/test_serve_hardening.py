"""HTTP 层防护测试（第四轮新增）。

实测发现（修复前）：
  - /chat 完全无速率限制：连发 40 次全部 200，可被高频刷取耗尽 LLM 配额；
  - 输入长度无上限：44000 字的 question 被接受（会整体拼进 prompt）。

修复：
  - 滑动窗口限流 30 次/分，超限 429 并带 Retry-After；
  - question 上限 2000 字（超限 413），空问题 400。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent import db as db_mod
from agent.ratelimit import MAX_QUESTION_CHARS, RateLimiter, chat_limiter
from agent.serve import app

_DB = "data/campus_small.db"


@pytest.fixture(autouse=True)
def _configure():
    db_mod.set_db_path(_DB)
    chat_limiter.reset()
    yield
    chat_limiter.reset()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _ask(client, q: str):
    return client.post("/chat", json={"question": q, "role": "student", "user_id": "S00001"})


# ---------------------------------------------------------------- 限流器单元
def test_rate_limiter_allows_up_to_limit():
    rl = RateLimiter(max_calls=3, window_seconds=60)
    assert [rl.allow("k") for _ in range(4)] == [True, True, True, False]


def test_rate_limiter_retry_after_positive_after_block():
    rl = RateLimiter(max_calls=1, window_seconds=60)
    assert rl.allow("k")
    assert rl.retry_after("k") >= 1


def test_rate_limiter_isolates_keys():
    rl = RateLimiter(max_calls=1, window_seconds=60)
    assert rl.allow("a")
    assert rl.allow("b"), "不同 key 不应互相影响"


def test_rate_limiter_reset():
    rl = RateLimiter(max_calls=1, window_seconds=60)
    rl.allow("k")
    assert not rl.allow("k")
    rl.reset()
    assert rl.allow("k")


# ---------------------------------------------------------------- HTTP 限流
def test_chat_is_rate_limited(client):
    codes = [_ask(client, "计算机学院有多少学生？").status_code for _ in range(40)]
    assert 429 in codes, f"/chat 未限流：{set(codes)}"
    # 前 30 次应放行
    assert codes[:30] == [200] * 30, codes[:30]


def test_rate_limit_response_has_retry_after(client):
    for _ in range(31):
        _ask(client, "计算机学院有多少学生？")
    r = _ask(client, "计算机学院有多少学生？")
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    assert "频繁" in r.json()["detail"]


def test_rate_limit_reset_restores_service(client):
    for _ in range(31):
        _ask(client, "计算机学院有多少学生？")
    assert _ask(client, "计算机学院有多少学生？").status_code == 429
    chat_limiter.reset()
    assert _ask(client, "计算机学院有多少学生？").status_code == 200


# ---------------------------------------------------------------- 输入长度
def test_question_at_limit_is_accepted(client):
    q = "计" * MAX_QUESTION_CHARS
    assert _ask(client, q).status_code == 200


def test_question_over_limit_rejected(client):
    q = "计" * (MAX_QUESTION_CHARS + 1)
    r = _ask(client, q)
    assert r.status_code == 413
    assert str(MAX_QUESTION_CHARS) in r.json()["detail"]


def test_very_long_question_rejected(client):
    r = _ask(client, "计" * 44000)
    assert r.status_code == 413


def test_empty_question_rejected(client):
    for q in ("", "   ", "\n\t "):
        r = _ask(client, q)
        assert r.status_code == 400, f"{q!r} 应被拒绝"


# ---------------------------------------------------------------- 角色解析
@pytest.mark.parametrize("requested", ["admin", "root", "", "SUPERUSER", "teacher"])
def test_role_never_escalates_to_admin_by_client(client, requested, monkeypatch):
    """客户端不得自提 admin（除非服务端显式开启）。"""
    monkeypatch.delenv("DATACLERK_ADMIN", raising=False)
    from agent.serve import _resolve_role

    assert _resolve_role(requested) != "admin"


def test_admin_allowed_only_when_server_enabled(monkeypatch):
    from agent.serve import _resolve_role

    monkeypatch.setenv("DATACLERK_ADMIN", "1")
    assert _resolve_role("admin") == "admin"
