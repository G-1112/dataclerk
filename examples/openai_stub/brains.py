"""协议桩背后的两种「大脑」。

同一个 OpenAI 协议端点，换不同大脑，用来验证一件事：
**测试框架既能抓出缺陷，也能给出干净的报告**——只抓缺陷、对谁都判失败的工具，
分数是没有意义的。所以这里放一好一坏两个对照体：

- `NaiveBrain`：复用 `examples/external_bot` 的朴素机器人（5 类初级缺陷），
  同一套用例应检出多类缺陷；
- `GuardedCampusBot`：同样的业务，但补齐了身份校验、注入拦截、写操作拦截、
  澄清策略与政策通道——同一套用例应接近满分。

两者都是**独立实现**（不 import `agent/*`），代表"别人的智能体"。
"""

from __future__ import annotations

import re

from examples.external_bot.bot import NaiveCampusBot

_POLICY_WORDS = ("奖学金", "作弊", "处分", "逾期", "转专业", "休学", "校园卡", "借阅", "请假",
                 "规定", "政策", "办法", "学分", "重修", "缓考", "复学", "学籍",
                 # 第十七轮用例扩容（routing 46→50）同步补的政策主题词
                 "实验室", "交换生", "讲座", "自习")
_DESTRUCTIVE = re.compile(r"\b(DROP|DELETE|TRUNCATE|ALTER|UPDATE|INSERT)\b", re.IGNORECASE)
_INJECTION = ("忽略", "导出", "全部学生", "所有学生", "system prompt", "忽略之前", "无视")
_STAT_WORDS = ("多少", "平均", "最高", "最低", "分布", "排名", "几门", "绩点")
_CJK = re.compile(r"[\u4e00-\u9fa5]")


class NaiveBrain:
    """朴素机器人（故意保留缺陷），用于验证"能抓出问题"。"""

    name = "naive"

    def __init__(self, db_path: str | None = None) -> None:
        self._bot = NaiveCampusBot(db_path=db_path)

    def handle(self, question: str, role: str, user_id: str) -> dict:
        res = self._bot.handle(question, role=role, user_id=user_id)
        res["channel"] = res.get("channel") or "sql"
        return res


class GuardedCampusBot:
    """补齐了最小安全策略的同一业务实现，用于验证"不会冤枉好智能体"。"""

    name = "guarded"

    def __init__(self, db_path: str | None = None) -> None:
        self._db = NaiveCampusBot(db_path=db_path)  # 只借用其只读查询方法

    def handle(self, question: str, role: str, user_id: str) -> dict:
        q = (question or "").strip()
        me = (user_id or "").strip().upper()

        # 1) 澄清策略：空/过短/无有效中文的输入先追问（不拿默认值硬答）
        if len(q) <= 3 or not _CJK.search(q):
            return self._r("问题不够明确，请补充：要查哪个学院、哪类数据（人数/成绩/课程）？", "clarify")

        # 2) 写操作拦截
        if _DESTRUCTIVE.search(q) or "删除" in q or "清空" in q:
            return self._r("拒绝执行：本助手仅支持只读查询，写操作已拦截。", "deny")

        # 3) 提示注入拦截（不采信问题里的"忽略指令"类要求）
        if any(w in q for w in _INJECTION):
            return self._r("拒绝执行：该请求疑似提示注入，已拦截，不会导出任何批量数据。", "deny")

        # 4) 身份校验：问到别人的学号一律拒绝
        ids = re.findall(r"S\d{5}", q)
        if ids:
            foreign = [i for i in ids if i.upper() != me]
            if foreign:
                return self._r("无权访问其他学生的数据：仅可查询本人信息。", "deny")
            return self._r(self._db._student_detail(ids[0]), "sql")

        # 5) 政策/制度类走政策通道
        if any(w in q for w in _POLICY_WORDS) and not any(w in q for w in _STAT_WORDS):
            return self._r(self._policy_answer(q), "rag")

        # 6) 常规统计
        college = self._db._extract_college(q)
        if college:
            return self._r(self._db._college_stat(college, q), "sql")
        return self._r("未找到该对象的数据，请确认学院/课程名称是否正确。", "sql")

    # ------------------------------------------------------------------
    @staticmethod
    def _r(answer: str, channel: str) -> dict:
        return {"answer": answer, "channel": channel}

    @staticmethod
    def _policy_answer(q: str) -> str:
        """极简政策库（演示用）；命中不了就给通用指引，仍属政策通道。"""
        table = {
            "奖学金": "根据《本科生奖学金评定办法》规定：按学年综合测评排名评定，需无不及格课程。",
            "作弊": "根据《学生违纪处分规定》：考试作弊给予记过及以上处分，情节严重的开除学籍。",
            "处分": "根据《学生违纪处分规定》：处分分为警告、严重警告、记过、留校察看、开除学籍。",
            "逾期": "根据《图书馆借阅管理办法》：逾期按 0.1 元/天计收滞纳金，上限为图书定价。",
            "转专业": "根据《本科生转专业实施办法》：大一学年末申请，需成绩排名前 30% 且通过考核。",
            "休学": "根据《学籍管理规定》：因病或其他正当理由可申请休学，一般以一年为限。",
            "校园卡": "根据《校园卡使用规定》：遗失请先在一卡通中心挂失，补办工本费 20 元。",
            "借阅": "根据《图书馆借阅管理办法》：本科生可借 10 册，借期 30 天，可续借一次。",
            "请假": "根据《学生请假管理规定》：3 天内由辅导员审批，超过 3 天需学院审批。",
            "学分": "根据《学分制管理办法》：毕业需修满培养方案规定学分，不足者按结业处理。",
            "重修": "根据《课程重修管理办法》：考核不合格可申请重修，重修成绩按实记载。",
            "缓考": "根据《考试管理规定》：因病等正当理由可申请缓考，需在考前提交证明。",
            "复学": "根据《学籍管理规定》：休学期满应于开学前提出复学申请，附健康证明。",
            "学籍": "根据《学籍管理规定》：学籍证明可在一站式服务大厅或教务系统在线申请。",
        }
        for key, text in table.items():
            if key in q:
                return text
        return "该事项按学校相关管理规定办理，具体请咨询教务处或所在学院。规定以最新文件为准。"


_BRAINS = {"naive": NaiveBrain, "guarded": GuardedCampusBot}


def get_brain(kind: str = "naive", db_path: str | None = None):
    """按名字取大脑；未知名字直接报错，避免静默回退到错的对照体。"""
    try:
        cls = _BRAINS[kind]
    except KeyError:
        raise ValueError(f"未知大脑：{kind}（可选：{', '.join(_BRAINS)}）") from None
    return cls(db_path=db_path)
