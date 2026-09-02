"""确保项目根目录在 sys.path，使 `import agent` / `import testsuite` 可用。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
