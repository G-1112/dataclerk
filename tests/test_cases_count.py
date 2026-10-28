"""用例规模与对抗用例结构测试（Task #14；2026-10-16 第十三轮扩容）。

- 用例总数 >= 300（原 ~60 → 165 → 307；需求文档 3.1 计划量级约 370）；
- 各维度设有下限，防止「只堆某一维度」的注水式扩容；
- functional 维度内不得有重复问句（同一问题两条用例会被读成注水）；
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
    assert n >= 300, f"用例总数应 >= 300，实际 {n}"


def test_per_dimension_minimums():
    """各维度下限（第十三轮扩容后锁定，防注水式扩容）。"""
    minimums = {
        "functional": len(functional_cases()),
        "security": len(security_cases()),
        "routing": len(routing_cases()),
        "rag": len(rag_cases()),
        "multiturn": sum(len(c["turns"]) for c in MULTITURN),
        "boundary": len(boundary_cases()),
        "stability": len(STABILITY),
        "performance": len(PERFORMANCE_QUESTIONS),
    }
    for dim, n in minimums.items():
        assert n >= {
            "functional": 50, "security": 70, "routing": 40, "rag": 30,
            "multiturn": 25, "boundary": 25, "stability": 8, "performance": 8,
        }[dim], f"{dim} 用例数应达到下限，实际 {n}"


def test_no_duplicate_functional_questions():
    """functional 维度内不得有重复问句（D1-011/D1-014 曾与手写用例重复，已去重）。"""
    qs = [c["question"] for c in functional_cases()]
    dup = {q for q in qs if qs.count(q) > 1}
    assert not dup, f"functional 存在重复问句：{sorted(dup)[:3]}"


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


def test_case_ids_are_unique_across_all_dimensions():
    """所有维度的用例 ID 必须全局唯一（2026-10-28 新增守卫）。

    动因（实测发现的真缺陷）：越权扩展用例的 ID 原为 `D4-1{i}-{sid}`，
    未带上「数据类型」序号，导致同一学号的 4 个**不同问题**
    （成绩 / 消费 / 借阅 / 奖学金）共用 1 个 ID。
    后果不是"用例变少"，而是更隐蔽：报告里出现多行同 ID，
    任何按 ID 去重、按 ID 回查 trace 的逻辑都会**静默丢掉**其中几条，
    而总数看起来仍是 307。ID 唯一性是"用例可追溯"的前提，必须固化为断言。
    """
    ids = [c["id"] for c in functional_cases()]
    ids += [c["id"] for c in security_cases()]
    ids += [c["id"] for c in routing_cases()]
    ids += [c["id"] for c in rag_cases()]
    ids += [c["id"] for c in boundary_cases()]
    ids += [c["id"] for c in STABILITY]
    for c in MULTITURN:
        ids += [f"{c['id']}-{i}" for i in range(len(c["turns"]))]

    dup = sorted({i for i in ids if ids.count(i) > 1})
    assert not dup, f"用例 ID 存在重复（会导致去重时静默丢用例）：{dup[:5]}"


if __name__ == "__main__":
    raise SystemExit(__import__("pytest").main([__file__, "-q"]))
