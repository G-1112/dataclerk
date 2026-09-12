"""第九轮回归：指标只能统计「真实测量过」的项。

背景（都是**测量缺陷**，不是被测对象的真实缺陷，但会直接误导评委）：
1. tool_calls：routing 用例不含 tool_correct 字段，被一律计为 False
   → 官方维度「工具调用」的准确率从 100% 被稀释成 18/(18+33)=35.29%；
   且结果从不携带 trace/steps → 「平均步数」恒为 0.0。
2. multiturn：run_multiturn 只设 resolve_ok，keep_ok / misinherit 从未赋值
   → 「约束保持率」恒为 0.0，会被误读为「多轮约束完全失效」。

修复原则：各子指标只统计**真正赋值过**的样本；未测量的项不计入分母。
"""

from __future__ import annotations

from testsuite import metrics


def _r(**kw):
    return dict(**kw)


def test_tool_calls_ignores_unmeasured():
    """没有 tool_correct 字段的用例不得被计入分母（否则准确率被稀释）。"""
    results = [
        _r(tool_correct=True, steps=3) for _ in range(18)
    ] + [_r(steps=1) for _ in range(33)]  # routing 风格：无 tool_correct
    m = metrics.tool_calls(results)
    assert m["工具选择准确率"] == 1.0, m
    assert m["用例数"] == 18, m


def test_tool_calls_steps_not_zero():
    """结果携带 steps 时，「平均步数」必须是真实值而非 0。"""
    results = [_r(tool_correct=True, steps=2), _r(tool_correct=False, steps=4)]
    m = metrics.tool_calls(results)
    assert m["平均步数"] == 3.0, m
    assert m["工具选择准确率"] == 0.5, m


def test_multiturn_only_counts_measured():
    """keep_ok 未赋值的轮次不得被当作「约束未保持」。"""
    results = [
        _r(resolve_ok=True),                       # 首轮：只测指代消解
        _r(resolve_ok=True, keep_ok=True, misinherit=False),   # 第 3 轮起
        _r(resolve_ok=False, keep_ok=False, misinherit=True),
    ]
    m = metrics.multiturn(results)
    assert m["指代消解准确率"] == 0.6667, m    # 3 个被测，2 个通过
    assert m["约束保持率"] == 0.5, m           # 2 个被测，1 个通过
    assert m["约束误继承率"] == 0.5, m         # 2 个被测，1 个误继承


def test_multiturn_all_unmeasured_is_not_zero_score():
    """若某子指标完全没测，应排除在分母外（表现为 0 个样本），
    而不是给出 0% 这种会被误读为「完全失败」的数值。"""
    results = [_r(resolve_ok=True) for _ in range(5)]
    m = metrics.multiturn(results)
    assert m["指代消解准确率"] == 1.0, m
