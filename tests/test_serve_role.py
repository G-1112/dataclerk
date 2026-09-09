"""serve 层角色解析测试：admin 不得由客户端自提（审计 P0）。

role 必须由服务端解析：默认最小权限 student；admin 仅当服务端环境变量
DATACLERK_ADMIN=1 时可用，否则任何 admin 请求降级为 student。
"""

from __future__ import annotations

from agent.serve import _resolve_role


def test_admin_downgraded_by_default(monkeypatch):
    monkeypatch.delenv("DATACLERK_ADMIN", raising=False)
    assert _resolve_role("admin") == "student"


def test_admin_allowed_when_server_enabled(monkeypatch):
    monkeypatch.setenv("DATACLERK_ADMIN", "1")
    assert _resolve_role("admin") == "admin"


def test_legit_roles_preserved(monkeypatch):
    monkeypatch.delenv("DATACLERK_ADMIN", raising=False)
    assert _resolve_role("student") == "student"
    assert _resolve_role("teacher") == "teacher"


def test_unknown_role_downgraded(monkeypatch):
    monkeypatch.delenv("DATACLERK_ADMIN", raising=False)
    assert _resolve_role("root") == "student"
    assert _resolve_role("") == "student"


if __name__ == "__main__":
    raise SystemExit(__import__("pytest").main([__file__, "-q"]))
