"""用例规模与对抗用例结构测试（Task #14）。

- 用例总数 >= 150（原 ~60，审计指出交付度仅计划的 16%）；
- 注入/绕过类对抗用例不得使用 admin（admin 无限制，测不出任何东西）；
- D4-009~011 对抗用例存在且带正确 role/user_id。
"""

from __future__ import annotations

from testsuite.cases import (
    MULTITURN,
    PERFORMANCE_QUESTIONS,
    STABILITY,
    boundary_cases,
    functional_cases,
    rag_cases,
    routing_cases,
    security_cases,
)


def _total_cases() -> int:
    total = len(functional_cases())
    total += len(security_cases())
    total += len(routing_cases())
    total += len(rag_cases())
    total += sum(len(c["turns"]) for c in MULTITURN)
    total += len(boundary_cases())
    total += len(STABILITY)
    total += len(PERFORMANCE_QUESTIONS)
    return total


def test_total_cases_exceeds_150():
    n = _total_cases()
    assert n >= 150, f"用例总数应 >= 150，实际 {n}"


def test_adversarial_cases_present():
    ids = {c["id"] for c in security_cases()}
    assert {"D4-009", "D4-010", "D4-011"} <= ids


def test_injection_cases_never_admin():
    for c in security_cases():
        if c["category"] == "注入":
            assert c.get("role") != "admin", f"{c['id']} 不应使用 admin（测不出注入防御）"


def test_bypass_cases_carry_sql_and_student_role():
    for c in security_cases():
        if c["category"] == "绕过":
            assert "sql" in c, f"{c['id']} 缺少 sql"
            # student 测行级过滤；teacher 测院系/单生明细（第十二轮新增 D4-014~016）。
            # admin 无限制，测不出绕过，仍禁止。
            assert c.get("role") in ("student", "teacher"), f"{c['id']} 绕过用例应用受限角色"


if __name__ == "__main__":
    raise SystemExit(__import__("pytest").main([__file__, "-q"]))
