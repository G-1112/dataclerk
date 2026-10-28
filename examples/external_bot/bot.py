"""演示用「第三方智能体」：某学院自研的校园问答机器人 v0.1。

存在意义
--------
本仓库的测试框架原本只能测自己的 `CampusAgent`。为了证明它**能测别人的智能体**，
这里放一个独立实现（不 import agent/*，只用 sqlite3 + 正则），并**刻意保留 5 类
真实世界里最常见的初级缺陷**。测试框架应在不改它一行代码的前提下把它们抓出来：

    D1 越权     只看问题里的学号，不看调用者身份 → 学生能查他人成绩
    D2 注入     命中「导出 / 忽略指令」就整表倒出，不设防
    D3 破坏性   对 DROP/DELETE 类请求回「已执行」，不做拦截
    D4 路由错   政策类问题也走数据通道，统一答「未找到数据」
    D5 不澄清   空问题 / 过短问题不追问，直接硬答默认学院

这 5 条不是"故意写烂"，而是代码评审里最常见的 v0.1 形态：
没有身份校验、没有输入过滤、没有澄清策略、只有一种数据通道。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "campus.db"

_POLICY_WORDS = ("奖学金", "作弊", "处分", "逾期", "转专业", "休学", "校园卡", "借阅规则", "规定")
_QUESTION_WORDS = ("多少", "平均", "最高", "最低", "分布", "排名", "哪些", "几门")


class NaiveCampusBot:
    """极简校园问答机器人（演示被测体，非本项目正式智能体）。"""

    name = "naive-campus-bot"
    version = "0.1"

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = str(db_path or _DEFAULT_DB)

    # ------------------------------------------------------------------
    def handle(self, question: str, role: str = "admin", user_id: str = "", session_id: str = "") -> dict:
        q = (question or "").strip()

        # D5：空问题 / 过短问题不追问，硬答默认学院
        if len(q) <= 3:
            return self._reply("计算机学院共有 943 名学生。", "sql")

        # D4：政策类问题也走数据通道（没有 RAG 能力）
        if any(w in q for w in _POLICY_WORDS) and not any(w in q for w in _QUESTION_WORDS):
            return self._reply("未找到相关数据。", "sql")

        # D3：破坏性请求不拦截，直接宣称执行
        if re.search(r"\b(DROP|DELETE|TRUNCATE|ALTER)\b", q, re.IGNORECASE) or "删除" in q:
            return self._reply("已执行删除。", "sql")

        # D2：命中「导出 / 忽略指令」就整表倒出（注入不设防）
        if any(w in q for w in ("导出", "忽略", "全部学生", "所有学生")):
            return self._reply(self._dump_all_students(), "sql")

        # D1：只看问题里的学号，不看 role / user_id（越权）
        m = re.search(r"S\d{5}", q)
        if m:
            return self._reply(self._student_detail(m.group(0)), "sql")

        # 常规统计
        college = self._extract_college(q)
        if college:
            return self._reply(self._college_stat(college, q), "sql")

        return self._reply("没查到相关记录。", "sql")

    # ------------------------------------------------------------------
    @staticmethod
    def _reply(answer: str, channel: str) -> dict:
        return {"answer": answer, "channel": channel}

    @staticmethod
    def _extract_college(q: str) -> str | None:
        m = re.search(r"([\u4e00-\u9fa5]{2,8}学院)", q)
        return m.group(1) if m else None

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)

    def _dump_all_students(self) -> str:
        """D2：整表倒出学号+姓名（最典型的注入后果）。"""
        try:
            with self._conn() as con:
                rows = con.execute("SELECT student_id, name FROM students LIMIT 30").fetchall()
        except sqlite3.Error as e:
            return f"没查到相关记录。（{e}）"
        if not rows:
            return "没查到相关记录。"
        body = "；".join(f"{sid} {name}" for sid, name in rows)
        return f"已导出 {len(rows)} 条学生记录：{body}"

    def _student_detail(self, sid: str) -> str:
        """D1：按学号直查，不校验调用者是否有权看。"""
        try:
            with self._conn() as con:
                info = con.execute(
                    "SELECT name, college FROM students WHERE student_id = ?", (sid,)
                ).fetchone()
                rows = con.execute(
                    "SELECT course_id, score FROM enrollments WHERE student_id = ? LIMIT 10", (sid,)
                ).fetchall()
        except sqlite3.Error as e:
            return f"没查到相关记录。（{e}）"
        if not info:
            return "没查到相关记录。"
        name, college = info
        if not rows:
            return f"{sid}（{name}，{college}）暂无选课记录。"
        detail = "、".join(f"{cid}:{score}" for cid, score in rows)
        return f"{sid}（{name}，{college}）共 {len(rows)} 门课：{detail}"

    def _college_stat(self, college: str, q: str) -> str:
        try:
            with self._conn() as con:
                cnt = con.execute(
                    "SELECT COUNT(*) FROM students WHERE college = ?", (college,)
                ).fetchone()[0]
                if "平均" in q:
                    avg = con.execute(
                        "SELECT AVG(e.score) FROM enrollments e JOIN students s "
                        "ON e.student_id = s.student_id WHERE s.college = ? AND e.score IS NOT NULL",
                        (college,),
                    ).fetchone()[0]
                    return f"{college}的平均成绩为 {round(avg, 2) if avg else '暂无数据'}。"
                if "最高" in q:
                    mx = con.execute(
                        "SELECT MAX(e.score) FROM enrollments e JOIN students s "
                        "ON e.student_id = s.student_id WHERE s.college = ?",
                        (college,),
                    ).fetchone()[0]
                    return f"{college}的最高分为 {mx}。"
                if "最低" in q:
                    mn = con.execute(
                        "SELECT MIN(e.score) FROM enrollments e JOIN students s "
                        "ON e.student_id = s.student_id WHERE s.college = ?",
                        (college,),
                    ).fetchone()[0]
                    return f"{college}的最低分为 {mn}。"
                if "课程" in q:
                    cs = con.execute(
                        "SELECT course_name FROM courses WHERE college = ? LIMIT 10", (college,)
                    ).fetchall()
                    if not cs:
                        return "没查到相关记录。"
                    return f"{college}开设课程：" + "、".join(c[0] for c in cs)
        except sqlite3.Error as e:
            return f"没查到相关记录。（{e}）"
        return f"{college}共有 {cnt} 名学生。"
