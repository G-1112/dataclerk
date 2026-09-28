# 问数官 DataClerk

> 先造一个"教务数据问答 + 政策问答"双通道校园智能体，再用一套体系化测试量化它到底有多可靠。

传智杯第九届 · AI 赋能·智能测试创新挑战赛 · **路径一：智能体应用的全方位测试**（团队赛）

- **造**：双通道校园智能助手（Text-to-SQL + 政策 RAG）
- **测**：官方 7 维度 + 自创路由维度 + **变异测试**（★核心创新）

---

## 目录结构

```
dataclerk/
├── agent/                  # 被测智能体
│   ├── graph.py            # ReAct 状态机 + 双通道编排
│   ├── router.py           # 路由：sql / rag / hybrid / clarify
│   ├── llm.py              # LLM 客户端 + 确定性 mock SQL 生成器
│   ├── tools/              # get_schema / search_values / execute_sql / retrieve_policy / plot_chart / clarify
│   ├── guard/permission.py # 权限层（student/teacher/admin + 行级过滤）
│   ├── memory/slots.py     # 约束槽位
│   ├── trace.py            # JSONL 埋点
│   └── serve.py            # FastAPI：/chat /trace /reset /health
├── data/
│   ├── gen_db.py           # 合成数据库（10 表，含边界埋入）
│   └── policies/           # 政策文档库（RAG 用）
│   # 注：数据库与全部评测数据均为脚本合成的样例数据（非真实师生信息），
│   # 边界与对抗样本为测试目的刻意埋入，见 data/gen_db.py 文件头声明。
├── testsuite/              # 测试体系
│   ├── agent_client.py     # 调用封装 + trace 抽取
│   ├── assert_result.py    # 结果集规范化 + SHA256
│   ├── metrics.py          # 指标计算
│   ├── cases.py            # 用例集（d1-d7）
│   ├── mutators/           # M1-M7 变异体注入器
│   ├── run_suite.py        # 一键跑批 + 变异测试
│   └── report/             # HTML 报告生成
├── tests/                  # 单元测试（318 项：含 17 项 XSS 输出转义回归 + 22 项第十二轮安全回归）
└── docs/                   # 测试方案 / 缺陷分析报告 / 适配指南
```

---

## ⚡ 10 分钟极速复现

### 环境要求

- Python **3.11+**（推荐 3.11）

### 第 1 步：安装依赖

```bash
cd dataclerk

# 方式 A：uv（推荐）—— uv.lock 已随仓库提交，可精确复现
uv sync --extra dev            # 严格按 uv.lock 安装
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 方式 B：pip（不读 uv.lock，版本可能漂移）
pip install -e ".[dev]"
```

可选的额外能力（按需叠加到上面的命令）：

```bash
# RAGAS / DeepEval：官方点名的 RAG 评测工具（LLM-as-judge，需 LLM_API_KEY）
uv sync --extra dev --extra rag-eval
```

> sqlglot（权限层 SQL 解析 / 行级过滤重写）自第十二轮起为**主依赖**，
> 随 `uv sync` 一并安装 —— 此前被误标为可选 extra，未安装时整个包无法 import，
> 与「10 分钟复现」承诺冲突；安全组件不做可选。

> **关于 `uv.lock`**：仓库内提交了锁文件（默认依赖 150 个包，含精确版本与哈希）。
> 用 `uv sync` 可保证依赖版本与作者环境一致；用 pip 安装则按 `>=` 约束解析，
> **结果可能不同**。这也让 `pip-audit` 等依赖漏洞扫描有了确定的输入。

> **0 Key 即可跑通**：不配置 LLM，智能体用确定性 mock SQL 生成器 + 关键词路由，
> 测试套件（含变异测试）全流程可跑。

### 第 2 步：生成数据库

```bash
python data/gen_db.py --small --db data/campus_small.db   # 快速演示
python data/gen_db.py                                     # 全量（8000 学生）
```

### 第 3 步：跑单元测试

```bash
pytest -q          # 预期 318 passed（含 17 项 XSS 输出转义回归 + 22 项第十二轮安全回归）
```

### 第 4 步：跑全量测试套件 + 变异测试

```bash
python -m testsuite.run_suite --db data/campus_small.db
# 产出 data/report/metrics.json + data/report/report.html
# 预期：Mutation Score = 7/7
```

### 第 5 步（可选）：起服务手动交互

```bash
uvicorn agent.serve:app --reload --port 8000
# POST /chat  {"question":"计算机学院有多少学生？","role":"admin"}
```

---

## 需要哪些 Key

| 能力 | 必需？ | 说明 |
|---|---|---|
| 大模型（DeepSeek/千问/GLM） | 可选 | 不填则用确定性 mock 生成 SQL + 关键词路由 |
| 向量库（ChromaDB） | 可选 | 默认纯 Python TF-IDF 检索器，零依赖 |
| RAGAS / DeepEval | 可选 | 官方点名的 RAG 评测工具，需 `.[rag-eval]` + LLM Key；未配置自动跳过 |

配置 `.env`（复制 `.env.example`）后，智能体的 Text-to-SQL 与回答合成会走真实 LLM。

---

## 核心设计

### 双通道 Agent

```
用户提问 → router(sql/rag/hybrid/clarify)
  → SQL 通道：get_schema → search_values → execute_sql（只读+权限过滤）
  → RAG 通道：retrieve_policy → synthesize
  → 每步写 trace(JSONL) → 融合回答（带引用溯源）
```

### 权限层（安全测试的天然素材）

- `student`：自动注入 `student_id = 当前用户` 行级过滤，禁止访问他人成绩/消费/借阅
- `teacher`：可查本院系聚合，不可查单生明细
- 拒绝写操作（DELETE/DROP/UPDATE/INSERT）、拒绝无 LIMIT 大查询、拒绝未知表

### 变异测试（回答"测试用例真能抓到 bug 吗"）

7 个变异体（删工具/截断记忆/移除权限/投毒/提温/禁用澄清/破坏路由），
目标维度指标降幅 > 20% 判 killed。本项目实测 **Mutation Score = 7/7**。

---

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/chat` | `{question, role, session_id, user_id}` → `{answer, sources, trace_id}` |
| GET | `/trace/{trace_id}` | 返回完整步骤轨迹 |
| POST | `/reset` | 重置会话记忆 |
| GET | `/health` | 健康检查 |

---

## 提交材料

- ZIP ≤ 200MB，命名 `AI智能体测试挑战赛-问数官`
- 视频 5-10 分钟（B站/抖音链接），展示智能体实际表现 + 测试执行过程
- 文档：测试方案（`docs/测试方案.md`）、缺陷分析报告、适配指南

## 许可

参赛作品，仅用于展示与学习。
