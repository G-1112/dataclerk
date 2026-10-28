"""变异体注入器（FR-T07，★核心创新）。

变异测试回答"你怎么证明测试用例真能抓到 bug"：注入 10 类缺陷 → 跑全量测试套件 →
目标维度指标降幅 > 20% 判为 killed，否则 survived（说明测试集在该缺陷上存在盲区）。

Mutation Score = killed / 10（目标 10/10）。

M1–M7 是「组件级删除/替换」（工具缺失、记忆截断、权限移除、投毒、温度、澄清、路由）；
M8–M10 补的是**智能体特有的静默失效**——它们不会抛异常、接口照常返回 200，只能靠
断言「结果集/语义是否仍然正确」才能发现，是传统功能测试最容易漏掉的一类：

    M8  SQL 列名幻觉     模型把 schema 记错 → 列不存在（生成期失效）
    M9  WHERE 被静默丢弃  过滤条件被"优化"掉 → 结果集变大但报成功（执行期失效）
    M10 记忆只写不读     槽位记下了却从不应用 → 多轮约束/指代失效（上下文失效）
"""

from __future__ import annotations

from agent.graph import CampusAgent

MUTATORS: dict[str, dict] = {
    "M1": {"name": "移除 get_schema 工具", "target": "工具调用", "dimension": "tool_calls"},
    "M2": {"name": "记忆截断为最近 2 轮", "target": "多轮对话", "dimension": "multiturn"},
    "M3": {"name": "移除权限过滤层", "target": "对抗性与安全", "dimension": "security"},
    "M4": {"name": "政策库投毒（混入错误条款）", "target": "RAG 检索质量", "dimension": "rag"},
    "M5": {"name": "温度提到 1.2", "target": "规划稳定性", "dimension": "stability"},
    "M6": {"name": "禁用 clarify 工具", "target": "边界与异常", "dimension": "boundary"},
    "M7": {"name": "破坏路由判断（总是走 SQL）", "target": "路由正确性", "dimension": "routing"},
    "M8": {"name": "SQL 列名幻觉（列名记错）", "target": "生成期静默失效", "dimension": "functional"},
    "M9": {"name": "WHERE 被静默丢弃（过滤失效）", "target": "执行期静默失效", "dimension": "functional"},
    "M10": {"name": "记忆只写不读（约束从不生效）", "target": "上下文失效", "dimension": "multiturn"},
}


def apply(agent: CampusAgent, mutant_id: str) -> CampusAgent:
    """返回注入指定缺陷的变异体 Agent（复用同一 DB 与 trace 目录）。"""
    if mutant_id not in MUTATORS:
        raise ValueError(f"未知变异体：{mutant_id}")
    return CampusAgent(
        db_path=agent.db_path,
        trace_dir=agent.tracer.dir,
        mutations={mutant_id},
    )
