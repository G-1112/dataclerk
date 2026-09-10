"""双通道路由（FR-A03）：判断问题应走 sql / rag / hybrid / clarify。

- 确定性关键词路由（mock/默认）；
- 路由结果写入 trace，供"路由正确性"测试使用。

第三轮修复（路由误判）：
- R-RT-01：「长度 < 8 即澄清」的粗粒度启发式会把**明确但很短的数据问题**
  （如「各学院人数统计」「借阅排名」「平均成绩」）误判为 clarify；
  加一个无意义语气词（「各学院人数统计啦」）反而能路由成功，说明阈值不成立。
  改为：只要有强数据意图关键词或有实质内容，就不再因长度被澄清。
- R-RT-02：纯疑问片段（「怎么样」）因含 RAG 关键词「怎么」被误判为政策问题。
  改为：剥离语气/疑问词后若无实质内容，判为 clarify。
- R-RT-03：带数据关键词的短追问（「那他的成绩呢」）在有历史上下文时
  应延续数据通道，而非被长度启发式打回 clarify。
"""

from __future__ import annotations

import re

_SQL_KEYWORDS = [
    "成绩", "绩点", "选课", "课程", "学生", "同学", "老师", "教师", "教室",
    "图书", "借阅", "借了", "消费", "奖学金", "人数", "统计", "平均", "最高",
    "最低", "多少", "几个", "数量", "哪个学院", "各学院", "分布", "排名",
]

_RAG_KEYWORDS = [
    "政策", "规定", "流程", "申请", "要求", "怎么", "如何", "条件", "标准",
    "评定", "处分", "考试纪律", "选课规则", "学分", "毕业", "学籍", "请假",
    "休学", "复学", "转专业", "重修", "缓考",
]

# 强数据意图：命中即视为明确的数据查询，不因问题短而被澄清（R-RT-01）
_STRONG_SQL = [
    "人数", "多少", "统计", "平均", "最高", "最低", "名单", "哪些", "几个",
    "数量", "分布", "排名", "总额", "总数", "合计", "占比", "比例", "明细",
]

# 语气/疑问虚词：剥离后若几乎无剩余内容，说明问题本身无信息量（R-RT-02）
# 注意按长度降序排列，先替换长词（「怎么样」先于「怎么」）
_VAGUE_WORDS = (
    "可不可以", "能不能", "有没有", "是不是", "怎么样", "怎么办", "是什么",
    "有哪些", "哪一个", "什么样",
    "怎么", "如何", "什么", "哪些", "哪个", "谁", "是否", "请", "帮我",
    "看看", "一下", "呢", "吗", "啊", "吧", "了", "的",
)

_PUNCT_RE = re.compile(r"[\s，。？！、；：,.\?!;:（）()【】\[\]\"'“”‘’]+")


def _content(question: str) -> str:
    """去掉标点与语气虚词后的实质内容。"""
    core = _PUNCT_RE.sub("", question or "")
    for w in _VAGUE_WORDS:
        core = core.replace(w, "")
    return core


def _is_vague_only(question: str) -> bool:
    """是否为无实质内容的疑问片段（「怎么样」「谁」「查一下」）。"""
    return len(_content(question)) <= 1


# 含人称代词指代且无上文时，无法解析指代对象，应要求澄清而非猜测（R-RT-03）
_PRONOMINAL = ("他们", "她们", "它们", "其他", "他的", "她的", "它的", "他", "她")


def _has_unresolved_ref(question: str) -> bool:
    """问题含人称代词指代。"""
    return any(w in (question or "") for w in _PRONOMINAL)


def detect_foreign_student_id(question: str, current_user: str) -> list[str]:
    """问题中是否显式出现了**他人**学号。

    第四轮新增：实测发现学生问「S00002 这学期选了什么课」时，
    路由把它判为 clarify 并回复「请补充具体条件」——
    既没有拒绝，也没有触发权限层，等于把越权请求当成「问得不清楚」。
    安全上没有泄露（clarify 不含任何数据），但口径不一致：
    显式索取他人数据应当明确拒绝，而不是邀请对方换个说法再试。

    正则必须**大小写不敏感**：数据中学号是大写 S00001，
    若只匹配大写，攻击者写小写 s00002 即可绕过本拦截。
    """
    import re

    ids = {m.upper() for m in re.findall(r"S\d{3,}", question or "", re.I)}
    me = (current_user or "").upper()
    return sorted(x for x in ids if x != me)


def route_question(
    question: str,
    history: list[str] | None = None,
    role: str = "",
    current_user: str = "",
) -> dict:
    """返回 {channel, reason}。

    在常规路由之前先做**越权前置拦截**：学生显式索取他人数据一律拒绝，
    不再进入 clarify / sql / rag。教师与管理员不受此限（其查询他人数据属正常业务）。
    """
    q = question or ""

    # 0) 越权前置拦截（仅学生角色）
    if role == "student" and current_user:
        foreign = detect_foreign_student_id(q, current_user)
        if foreign:
            return {
                "channel": "deny",
                "reason": f"越权：无权访问其他学生（{foreign[0]}）的数据",
                "foreign_ids": foreign,
            }

    sql_hits = [k for k in _SQL_KEYWORDS if k in q]
    rag_hits = [k for k in _RAG_KEYWORDS if k in q]

    strong_sql = any(k in q for k in _STRONG_SQL)

    # 1) 一个关键词都没命中
    if not sql_hits and not rag_hits:
        # 指代消解：历史里有上文时，模糊追问默认延续 sql 通道
        if history:
            return {"channel": "sql", "reason": "指代延续：沿用上一轮数据通道"}
        return {"channel": "clarify", "reason": "问题过于简短/模糊，需要澄清"}

    # 2) 同时命中数据与政策关键词
    if sql_hits and rag_hits:
        # 有 RAG 命中但无明确数据意图（统计/数量/平均/名单）→ 倾向政策通道
        if not strong_sql:
            return {"channel": "rag", "reason": f"命中政策关键词：{rag_hits[0]}（无明确数据意图）"}
        return {"channel": "hybrid", "reason": f"同时命中数据({sql_hits[0]})与政策({rag_hits[0]})"}

    # 3) 只命中政策关键词
    if rag_hits:
        # R-RT-02：纯疑问片段（「怎么样」）无信息量 → 澄清而非硬答政策
        if _is_vague_only(q):
            return {"channel": "clarify", "reason": "问题缺少具体对象，需要澄清"}
        return {"channel": "rag", "reason": f"命中政策关键词：{rag_hits[0]}"}

    # 4) 只命中数据关键词
    # R-RT-03：无上文却含人称代词指代（「那他的成绩呢」）→ 无法解析对象，要求澄清
    if not history and _has_unresolved_ref(q):
        return {"channel": "clarify", "reason": "问题含指代但无上文，请补充具体对象"}
    # R-RT-01：有强数据意图 / 含数字 / 有实质内容 → 直接走数据通道，不再因长度澄清
    if strong_sql or any(c.isdigit() for c in q) or len(_content(q)) >= 4:
        return {"channel": "sql", "reason": f"命中数据关键词：{sql_hits[0]}"}

    # R-RT-03：很短的数据追问——有上文则延续，无上文才要求澄清
    if history:
        return {"channel": "sql", "reason": "指代延续：沿用上一轮数据通道"}
    return {"channel": "clarify", "reason": "问题缺少具体对象，需要澄清"}
