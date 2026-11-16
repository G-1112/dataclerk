"""LLM 客户端 + 确定性 mock SQL 生成器。

- LlmClient：OpenAI 兼容协议（DeepSeek/Qwen/GLM），未配置 Key 时进入 mock 模式。
- MockSqlGenerator：确定性 NL→SQL，覆盖校园域常见查询模板，保证零 Key 可跑通
  与可复现（测试基线依赖其确定性）。
"""

from __future__ import annotations

import json
import os
import re

# ---------------------------------------------------------------------------
# 真实 LLM 客户端
# ---------------------------------------------------------------------------
_SQL_SYSTEM = """你是校园数据智能体的 SQL 生成器。根据表结构和用户问题生成一条只读 SQLite 查询。
只输出 SQL 语句本身，不要输出解释、不要加代码块标记。
若问题涉及政策、规定、流程等非结构化内容，或无法用 SQL 回答，输出空字符串。

额外约束（以下为通用规则，务必遵守）：
1. 涉及「某学院/某专业的学生」的统计（人数、平均绩点、成绩、选课等）时，必须以 students
   表为口径，或用 enrollments JOIN students ON student_id，按 students.college 过滤；
   不要用 courses.college（那是开课学院，口径不同）。
2. 只 SELECT 用户明确问到的列，禁止 SELECT * 或多带无关列。
   例如问「有哪些课程」只取 course_name；问「有多少学生」只取 COUNT(*)。
3. semester 列是「年份-季节」大写枚举：'2023-FALL'、'2024-SPRING'、'2024-FALL'、'2025-SPRING'。
   遇到「2024年春季」要转成 '2024-SPRING'，遇到「2024年秋季」转成 '2024-FALL'；
   不要写成 '2023-2024-2'、'2024秋季'、'spring 2024' 等其他形式。
4. grade 列是整数年份（如 2024），过滤时不要加引号。
5. enrollments.status 是选课状态：'completed'（已结课，有成绩）、'enrolled'（在修，无成绩）、
   'withdrawn'（已退课）。**凡统计「成绩/分数/绩点」（平均、最高、最低），只计入
   status = 'completed' 的记录**；退课与在修记录不计入成绩统计。"""

_ANSWER_SYSTEM = """你是校园智能助手。根据检索到的政策片段回答用户问题，只依据片段内容作答，
引用片段要点，不要编造。片段不足时如实说明"未找到相关政策"。"""


class LlmClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("LLM_API_KEY", "").strip()
        self.base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").strip()
        self.model = os.getenv("LLM_MODEL", "deepseek-chat").strip()
        self.enabled = bool(self.api_key)

    def complete(self, system: str, user: str, temperature: float = 0.0) -> str:
        if not self.enabled:
            raise RuntimeError("LLM 未配置")
        import urllib.request

        body = json.dumps(
            {
                "model": self.model,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# 确定性 mock SQL 生成器
# ---------------------------------------------------------------------------
_COLLEGES = [
    "计算机学院", "软件学院", "电子信息学院", "机械工程学院",
    "经济管理学院", "外国语学院", "理学院", "文学院",
]
_COLLEGE_ALIAS = {"计算机科学与技术学院": "计算机学院", "计算机与软件学院": "软件学院"}

def _extract_college(q: str) -> str | None:
    for alias, real in _COLLEGE_ALIAS.items():
        if alias in q:
            return real
    for c in _COLLEGES:
        if c in q:
            return c
    return None


def _extract_semester(q: str) -> str | None:
    # 允许数字与量词之间存在空格（口语化输入常见：「2024 年春季」）
    m = re.search(r"(20\d{2})\s*年\s*(春|秋)\s*季", q)
    if m:
        return f"{m.group(1)}-{'SPRING' if m.group(2) == '春' else 'FALL'}"
    return None


def _extract_student_id(q: str) -> str | None:
    m = re.search(r"(S\d{5})", q)
    return m.group(1) if m else None


def _extract_grade(q: str) -> str | None:
    # 修复（第三轮）：原正则 r"(20\d{2})级" 不含空格，
    # 而「理学院 2024 级有多少学生？」这类输入数字与「级」之间带空格，
    # 导致年级条件被静默丢弃 —— SQL 退化为只按学院聚合（实测 51 而非 9）。
    m = re.search(r"(20\d{2})\s*级", q)
    return m.group(1) if m else None


class MockSqlGenerator:
    """确定性 NL→SQL（校园域）。"""

    def generate(self, question: str, role: str = "admin", current_user: str = "") -> str:
        q = question
        college = _extract_college(q)
        semester = _extract_semester(q)
        sid = _extract_student_id(q)

        # 各学院分布（分组聚合，需优先于单学院聚合）
        if re.search(r"各学院|每个学院|分布", q):
            return self._group_by_college(q)

        # 平均绩点/平均成绩
        if re.search(r"平均|绩点|均分|avg", q) and re.search(r"成绩|绩点|分", q):
            return self._avg_score(college, semester)
        # 最高/最低成绩
        if re.search(r"最高|最低", q) and re.search(r"成绩|分", q):
            return self._maxmin_score(college, semester, "MAX" if "最高" in q else "MIN")
        # 选课人数（须先于「多少+人」的人数统计分支：否则「选课人数」被截胡成学生计数）
        if re.search(r"选课|选修|选了多少", q) and re.search(r"人|人数", q):
            return self._count_enrollments(college, semester)
        # 人数统计
        if re.search(r"多少|人数|统计|有几个|数量", q) and re.search(r"学生|同学|人", q):
            return self._count_students(college, _extract_grade(q))
        # 课程列表
        if re.search(r"课程|课", q) and re.search(r"有哪些|什么|哪些|列表", q):
            return self._list_courses(college)
        # 单个学生成绩
        if sid and re.search(r"成绩|分|选课", q):
            return self._student_scores(sid, semester)
        # 奖学金
        if re.search(r"奖学金", q):
            return self._scholarships(college, sid)
        # 借阅
        if re.search(r"借阅|借了|借书", q):
            return self._borrows(sid)
        # 消费
        if re.search(r"消费|花|食堂", q):
            return self._transactions(sid)
        # 教师
        if re.search(r"老师|教师|教授|讲师", q):
            return self._teachers(college)
        # 空结果查询（某学期无记录）等默认：返回学院学生列表
        return self._count_students(college, None)

    # ---- 模板 ----
    def _avg_score(self, college: str | None, semester: str | None) -> str:
        sql = "SELECT AVG(e.score) FROM enrollments e JOIN students s ON e.student_id = s.student_id"
        # 成绩统计只算已结课记录（enrolled 无成绩、withdrawn 退课不计）
        conds = ["e.status = 'completed'"]
        if college:
            conds.append(f"s.college = '{college}'")
        if semester:
            conds.append(f"e.semester = '{semester}'")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        return sql

    def _maxmin_score(self, college: str | None, semester: str | None, fn: str) -> str:
        sql = f"SELECT {fn}(e.score) FROM enrollments e JOIN students s ON e.student_id = s.student_id"
        conds = ["e.status = 'completed'"]
        if college:
            conds.append(f"s.college = '{college}'")
        if semester:
            conds.append(f"e.semester = '{semester}'")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        return sql

    def _count_students(self, college: str | None, grade: str | None) -> str:
        sql = "SELECT COUNT(*) FROM students"
        conds = []
        if college:
            conds.append(f"college = '{college}'")
        if grade:
            conds.append(f"grade = {grade}")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        return sql

    def _count_enrollments(self, college: str | None, semester: str | None) -> str:
        sql = "SELECT COUNT(*) FROM enrollments e JOIN students s ON e.student_id = s.student_id"
        conds = []
        if college:
            conds.append(f"s.college = '{college}'")
        if semester:
            conds.append(f"e.semester = '{semester}'")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        return sql

    def _list_courses(self, college: str | None) -> str:
        if college:
            return f"SELECT course_name FROM courses WHERE college = '{college}'"
        return "SELECT course_name FROM courses"

    def _student_scores(self, sid: str, semester: str | None) -> str:
        sql = f"SELECT e.course_id, e.score FROM enrollments e WHERE e.student_id = '{sid}'"
        if semester:
            sql += f" AND e.semester = '{semester}'"
        return sql

    def _scholarships(self, college: str | None, sid: str | None) -> str:
        if sid:
            return f"SELECT name, amount FROM scholarships WHERE student_id = '{sid}'"
        if college:
            # 「有多少种」问的是项目种类 → 必须 DISTINCT；COUNT(*) 会答成人次。
            # 第十五轮由评测基准修正反向同步到这里（基准与实现口径必须一致）。
            return (f"SELECT COUNT(DISTINCT sh.name) FROM scholarships sh "
                    f"JOIN students s ON sh.student_id = s.student_id "
                    f"WHERE s.college = '{college}'")
        return "SELECT name, COUNT(*) FROM scholarships GROUP BY name"

    def _borrows(self, sid: str | None) -> str:
        if sid:
            return f"SELECT book_id, borrow_date, due_date, return_date FROM library_borrow WHERE student_id = '{sid}'"
        return "SELECT COUNT(*) FROM library_borrow WHERE return_date IS NULL"

    def _transactions(self, sid: str | None) -> str:
        if sid:
            return f"SELECT merchant, amount, trans_time FROM card_transactions WHERE student_id = '{sid}'"
        return "SELECT SUM(amount) FROM card_transactions"

    def _teachers(self, college: str | None) -> str:
        if college:
            return f"SELECT name, title FROM teachers WHERE college = '{college}'"
        return "SELECT name, title FROM teachers"

    def _group_by_college(self, q: str) -> str:
        # 第十六轮：分组统计补 ORDER BY。缺省顺序在 SQLite 里是"未定义"的，
        # 同一库两次执行可能给出不同行序 → 稳定性维度会偶发假红（实测踩过 1 次）。
        # 另外「排名」类问题本就该排序，排序也是更符合用户预期的回答。
        if re.search(r"成绩|绩点", q):
            return ("SELECT s.college, AVG(e.score) FROM enrollments e "
                    "JOIN students s ON e.student_id = s.student_id "
                    "WHERE e.status = 'completed' GROUP BY s.college "
                    "ORDER BY 2 DESC, 1")
        return ("SELECT college, COUNT(*) FROM students GROUP BY college "
                "ORDER BY 2 DESC, 1")


mock_sql = MockSqlGenerator()


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
_llm = LlmClient()


def llm_enabled() -> bool:
    return _llm.enabled


def judge_complete(system: str, user: str) -> str:
    """通用补全入口（供评测器做 LLM-as-judge 用）；未配置 Key 返回空串。

    单独开这个口子而不是让评测器直接访问 `_llm`：评测逻辑属于测试侧，
    不应依赖被测模块的私有对象。
    """
    if not _llm.enabled:
        return ""
    return _llm.complete(system, user, temperature=0.0)


def generate_sql(question: str, schema_desc: str, role: str = "admin", current_user: str = "") -> str:
    """生成 SQL：有 Key 走 LLM，无 Key 走 mock。"""
    if _llm.enabled:
        user = f"【表结构】\n{schema_desc}\n\n【用户问题】\n{question}"
        sql = _llm.complete(_SQL_SYSTEM, user, temperature=0.0).strip()
        return _strip_code_fence(sql)
    return mock_sql.generate(question, role, current_user)


def synthesize_answer(question: str, retrieved: list[str]) -> str:
    """基于政策片段生成回答。"""
    if not retrieved:
        return "未找到相关政策。"
    if _llm.enabled:
        user = f"【用户问题】{question}\n\n【政策片段】\n" + "\n".join(retrieved)
        return _llm.complete(_ANSWER_SYSTEM, user, temperature=0.0).strip()
    # mock：直接拼接片段
    return "；".join(retrieved)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:sql)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()
