"""路由 + 记忆槽位测试（FR-A03/A07）。"""

import pytest

from agent.memory.slots import Slots
from agent.router import route_question
from testsuite.cases import routing_cases


def test_route_sql():
    assert route_question("计算机学院有多少学生？")["channel"] == "sql"


def test_route_rag():
    assert route_question("奖学金评定需要什么条件？")["channel"] == "rag"


def test_route_clarify():
    assert route_question("查一下")["channel"] == "clarify"


# ---- 第三轮：把官方 33 条路由用例纳入回归（此前只测 3 条手写断言，
#      导致 D7-010「怎么样」误判为 rag 长期未被发现）----
@pytest.mark.parametrize("case", routing_cases(), ids=lambda c: c["id"])
def test_official_routing_cases(case):
    got = route_question(case["question"])["channel"]
    assert got == case["expected_channel"], (
        f"{case['id']} {case['question']!r} 期望 {case['expected_channel']}，实得 {got}"
    )


def test_short_data_question_not_clarified():
    """R-RT-01：短但明确的数据问题不应被长度启发式打回澄清。"""
    for q in ["各学院人数", "借阅排名", "平均成绩", "选课人数", "奖学金总额"]:
        assert route_question(q)["channel"] == "sql", f"{q} 被误判为 clarify"


def test_vague_fragment_is_clarified():
    """R-RT-02：纯疑问片段无信息量，应澄清而非硬答政策。"""
    for q in ["怎么样", "谁", "怎么", "如何", "查一下"]:
        assert route_question(q)["channel"] == "clarify", f"{q} 未被要求澄清"


def test_pronoun_without_history_is_clarified():
    """R-RT-03：含指代但无上文时无法解析对象，应澄清。"""
    assert route_question("那他的成绩呢")["channel"] == "clarify"
    # 有上文则延续数据通道
    assert route_question("那他的成绩呢", ["各学院人数统计"])["channel"] == "sql"


def test_slots_resolve_anaphora():
    s = Slots()
    s.remember("计算机学院有多少学生？")
    resolved = s.resolve("那平均绩点呢？")
    assert "计算机学院" in resolved


def test_slots_memory_truncation():
    """M2：记忆截断为 2 轮后，第 1 轮的学院被遗忘。"""
    s = Slots(max_memory=2)
    s.remember("计算机学院有多少学生？")
    s.remember("软件学院有多少学生？")
    s.remember("理学院有多少学生？")
    resolved = s.resolve("那平均绩点呢？")  # 应解析到最近的"理学院"
    assert "理学院" in resolved
    assert "计算机学院" not in resolved


def test_slots_long_memory_holds():
    s = Slots(max_memory=20)
    s.remember("计算机学院有多少学生？")
    s.remember("那平均绩点呢？")  # 隐式，无显式学院
    s.remember("那选课人数呢？")
    resolved = s.resolve("那最高分呢？")  # 仍能回到第 1 轮的计算机学院
    assert "计算机学院" in resolved
