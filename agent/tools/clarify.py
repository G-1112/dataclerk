"""clarify 工具（FR-A05）：主动澄清模糊需求。

可测失效模式：该澄清时硬答（幻觉来源）。
"""

from __future__ import annotations


def clarify(question: str, options: list[str] | None = None) -> str:
    """生成澄清提问（mock 版为确定性模板，真实版由 LLM 生成）。"""
    if options:
        return f"您的需求不够明确，请问您是指：{' / '.join(options)}？"
    return f"您的需求不够明确（{question[:30]}…），请补充具体条件（如学院、学期、学号等）。"
