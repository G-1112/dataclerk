"""确保项目根目录在 sys.path，使 `import agent` / `import testsuite` 可用；
并把「真实 LLM」隔离在测试之外（第十六轮新增，见 `_isolate_real_llm`）。"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_LLM_ENV = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")


@pytest.fixture(autouse=True)
def _isolate_real_llm(monkeypatch):
    """每个测试默认运行在 mock 模式：无 key、无外部端点。

    **为什么需要这条**：`testsuite/llm_track.py` 在 **import 时**就把 `.env.llm` 灌进
    `os.environ`（CLI 必须如此——`agent.llm` 在 import 时就构造 `LlmClient` 并固化 `enabled`）。
    副作用是只要某个测试 import 了它，**整个 pytest 进程**的 `LLM_API_KEY` 就非空，
    后面所有"未配置 key 就该跳过"的测试会真的去连网，网络不通时一律 502 假红。

    实测症状：全量跑 9 项失败（`test_serve_hardening` 4 / `test_rag_eval` 2 /
    `test_mutators_extra` 2 / `test_permission_round12` 1），单独跑却全绿——典型的
    跨测试隐式全局状态。修法选在测试边界复位，而不是让 500 多个测试各自记得清 env（漏一个就复发）。

    注意：只删 env 不够——`LlmClient.enabled` 是构造时固化的，必须连单例一起重建。
    需要真实 LLM 的测试应在测试体内显式 `monkeypatch.setenv` 并重建单例，把依赖写在明面上。
    """
    for key in _LLM_ENV:
        monkeypatch.delenv(key, raising=False)
    from agent import llm as agent_llm

    monkeypatch.setattr(agent_llm, "_llm", agent_llm.LlmClient(), raising=False)

