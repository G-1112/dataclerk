"""全局配置与常量。"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.getenv("DATACLERK_DB", str(DATA_DIR / "campus.db")))

# 角色
ROLES = ("student", "teacher", "admin")

# 变异体编号（对应 testsuite/mutators）
MUTANTS = ["M1", "M2", "M3", "M4", "M5", "M6", "M7"]
