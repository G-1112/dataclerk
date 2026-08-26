"""约束槽位（FR-A07）。

短期记忆：消息历史 + 已澄清的约束槽位 {学院, 年级, 学期}。
槽位显式结构化，便于测试断言"约束是否保持 / 误继承"。

设计：`remember(original)` 只记录**显式**出现在原文中的槽位；`resolve(question)`
基于最近 max_memory 轮内显式出现的槽位做指代消解（不把消解结果回写为显式槽位，
从而 max_memory 截断能真实生效——这是 M2 变异体的可测基础）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_COLLEGES = [
    "计算机学院", "软件学院", "电子信息学院", "机械工程学院",
    "经济管理学院", "外国语学院", "理学院", "文学院",
]
_ALIAS = {"计算机科学与技术学院": "计算机学院", "计算机与软件学院": "软件学院"}


def _extract_college(text: str) -> str | None:
    for alias, real in _ALIAS.items():
        if alias in text:
            return real
    for c in _COLLEGES:
        if c in text:
            return c
    return None


def _extract_grade(text: str) -> str | None:
    m = re.search(r"(20\d{2})级", text)
    return m.group(1) if m else None


def _extract_semester(text: str) -> str | None:
    m = re.search(r"(20\d{2})年(春|秋)季", text)
    if m:
        return f"{m.group(1)}-{'SPRING' if m.group(2) == '春' else 'FALL'}"
    return None


_ANAPHORA = ["它", "那", "该", "这个", "这些", "那个"]


@dataclass
class Slots:
    max_memory: int = 20
    turns: list[dict] = field(default_factory=list)  # 每轮显式槽位
    history: list[str] = field(default_factory=list)  # 原文历史（路由上下文用）

    def remember(self, question: str) -> dict:
        """记录本轮显式出现的槽位（原文，非消解结果）。"""
        self.history.append(question)
        entry = {
            "college": _extract_college(question),
            "grade": _extract_grade(question),
            "semester": _extract_semester(question),
        }
        self.turns.append(entry)
        if len(self.turns) > self.max_memory:
            self.turns = self.turns[-self.max_memory :]
        if len(self.history) > 50:
            self.history = self.history[-50:]
        return entry

    def resolve(self, question: str) -> str:
        """指代消解：把"那/它/该…"替换为最近显式出现的学院。"""
        q = question
        if any(w in q for w in _ANAPHORA) and "学院" not in q:
            college = self._most_recent("college")
            if college:
                q = f"{college}{q}"
        return q

    def _most_recent(self, key: str) -> str | None:
        for t in reversed(self.turns):
            if t.get(key):
                return t[key]
        return None

    def reset(self) -> None:
        self.turns = []
        self.history = []
