"""路由 + 记忆槽位测试（FR-A03/A07）。"""

from agent.memory.slots import Slots
from agent.router import route_question


def test_route_sql():
    assert route_question("计算机学院有多少学生？")["channel"] == "sql"


def test_route_rag():
    assert route_question("奖学金评定需要什么条件？")["channel"] == "rag"


def test_route_clarify():
    assert route_question("查一下")["channel"] == "clarify"


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
