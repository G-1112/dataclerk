"""测试环境的「真实 LLM 隔离」守卫（第十六轮新增）。

对应缺陷：`testsuite/llm_track.py` import 时会灌 `.env.llm` 到 `os.environ`，导致
后续测试从 mock 模式静默切到真实网络，网络不通就 502 假红 9 项。
`tests/conftest.py::_isolate_real_llm` 堵住了它，这里加两条守卫，防止哪天 fixture 被删/被改。

守卫的牙齿从哪来：把 fixture 去掉，只要仓库里存在 `.env.llm`（本机真实状态）且前面有测试
import 过 `testsuite.llm_track`，`LLM_API_KEY` 就会非空、`llm_enabled()` 变 True，两条全红。
仓库不带 `.env.llm` 时第一条恒真——这是环境限制，不是断言问题，已在注释里写明。
"""

from __future__ import annotations

import os
from pathlib import Path

from agent import llm as agent_llm

ROOT = Path(__file__).resolve().parent.parent


def test_pytest_always_runs_in_mock_mode():
    """无论仓里有没有 `.env.llm`，pytest 进程都必须是 mock 模式（无 key、单例未启用）。"""
    assert os.getenv("LLM_API_KEY") is None, "测试进程里出现了 LLM_API_KEY：隔离 fixture 失效"
    assert agent_llm.llm_enabled() is False, "单例仍处于启用态：只删 env 没重建 _llm"


def test_importing_llm_track_does_not_switch_process_to_real_llm():
    """导入真实 LLM 轨道的 CLI 模块，不能把**当前进程**悄悄切成真连网。"""
    import testsuite.llm_track  # noqa: F401  （import 本身即被测行为）

    assert agent_llm.llm_enabled() is False, (
        "导入 testsuite.llm_track 后单例被启用：CLI 的 import 副作用漏进了测试进程"
    )
