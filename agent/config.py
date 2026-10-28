"""全局配置与常量。

设计原则
--------
- **可调参数集中在此**，不在业务代码里散落魔法数字（评审 P2 项）；
- 全部支持环境变量覆盖，便于压测/演示时改参而不改码；
- 默认值与历史行为保持一致（改动前实测基线：307 用例 / 变异 7-7），
  调整默认值必须重跑全量套件。
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.getenv("DATACLERK_DB", str(DATA_DIR / "campus.db")))


def _int_env(name: str, default: int) -> int:
    """读取整型环境变量，非法值回退默认（防演示现场改错环境变量导致崩）。"""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# 角色
ROLES = ("student", "teacher", "admin")

# --- 智能体运行参数 ---------------------------------------------------------
# ReAct 最大步数：太小会截断多跳查询，太大会放大单请求延迟与 token 成本。
MAX_STEPS = _int_env("DATACLERK_MAX_STEPS", 8)
# 会话槽位上限：防长跑/压测下 _slots 无界增长（内存线性上涨）。
MAX_SLOT_KEYS = _int_env("DATACLERK_MAX_SLOT_KEYS", 1000)
# 单会话记忆轮数上界（M2 变异体会把它压到 2）。
MAX_MEMORY_ROUNDS = _int_env("DATACLERK_MAX_MEMORY_ROUNDS", 20)
# 结果集行数上限的**唯一真源**是 agent/db.py 的 MAX_ROWS（execute_sql 用它做 fetchmany 截断），
# 此处不再重复定义，避免两处口径漂移。
# 回答中最多展示的行数（其余折叠为「共 N 行」）。
MAX_DISPLAY_ROWS = _int_env("DATACLERK_MAX_DISPLAY_ROWS", 20)
# RAG 检索返回片段数。
RAG_TOP_K = _int_env("DATACLERK_RAG_TOP_K", 3)

# --- 变异测试 ---------------------------------------------------------------
# 变异体编号（对应 testsuite/mutators）；新增变异体须同步在 mutators 注册。
MUTANTS = ["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10"]
# 判 killed 的降幅阈值：目标维度指标必须下降超过该值。
MUTATION_KILL_DROP = float(os.getenv("DATACLERK_KILL_DROP", "0.2"))
