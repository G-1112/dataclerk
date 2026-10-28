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
│   ├── adapters/           # 被测体适配器（local / http），换对象不改执行器
│   ├── agent_client.py     # 调用封装 + trace 抽取
│   ├── assert_result.py    # 结果集规范化 + SHA256
│   ├── metrics.py          # 指标计算
│   ├── cases.py            # 用例集（309 条，d1-d7 + 路由/稳定性）
│   ├── mutators/           # M1-M10 变异体注入器
│   ├── rag_judge.py        # 自研 LLM-as-judge（忠实度/相关性，替代 RAGAS）
│   ├── llm_track.py        # 真实 LLM 测试轨道（含 A/B 基线开关）
│   ├── run_suite.py        # 一键跑批 + 变异测试
│   ├── run_external.py     # 用同一套用例测外部智能体
│   ├── verify_cases.py     # 用例入库前预检门（全量跑真实管线）
│   └── report/             # HTML 报告生成
├── examples/external_bot/  # 演示用"第三方智能体"（独立实现，自带 5 类缺陷）
├── tests/                  # 单元测试（357 项：XSS 转义回归 + 安全回归 + 适配器/变异体/判官单测 + 扩容守卫）
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
pytest -q          # 预期 525 passed（XSS 转义 + 安全回归 + 适配器/变异体/判官/等价/重放/环境隔离 单测 + 扩容守卫）
```

> **若你和我一样在「带 safe-delete 钩子的沙箱」里跑**：全量 pytest 退出码可能非 0 而 junit 报告是
> `525 passed / 0 failed`——那是钩子在拦 pytest 清理系统临时目录（一次 90+ 文件触发批量确认），
> 不是测试失败。两个可信做法：① 信 `--junitxml` 的读数；② 把临时目录放仓库内再跑：
> `pytest -q --basetemp=_tmp_pt`（实测退出码 0）。

### 第 4 步：跑全量测试套件 + 变异测试

```bash
python -m testsuite.run_suite --db data/campus_small.db
# 产出 data/report/metrics.json + data/report/report.html
# 预期：309 条用例，Mutation Score = 10/10
```

> 新增/修改用例后，先过预检门再跑全量（mock 模式下全量约 1 分钟）：
> ```bash
> python -m testsuite.verify_cases            # 全维度；--dim functional 可单跑
> ```

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

## 真实 LLM 测试轨道（可选，与 309 mock 套件隔离）

官方 309 套件跑在**确定性 mock** 上（0 Key 可复现、可审计）。真实大模型那条链路单独有一条轨道：

```bash
# 1) 把 Key 写进 .env.llm（已 gitignore，不会被提交）
printf 'LLM_API_KEY=sk-xxxx\nLLM_BASE_URL=https://api.deepseek.com\nLLM_MODEL=deepseek-chat\n' > .env.llm

# 2) 跑「修复前」提示词基线（A/B 对照）
.venv/Scripts/python.exe -m testsuite.llm_track --db data/campus.db --prompt legacy

# 3) 跑当前提示词
.venv/Scripts/python.exe -m testsuite.llm_track --db data/campus.db
```

实测（DeepSeek-Chat，与官方套件同源 **59 条**功能用例，2026-10-28 本机 4 轮）：

| 口径 | 修复前 | 修复后 |
|---|---|---|
| 严格（结果集 SHA256 一致） | 40.7–45.8%（24–27/59） | **88.1–89.8%（52–53/59）**；第十六轮修正基准口径后 **93.2%（55/59）** |
| 宽松（按期望列投影） | 54.2–59.3%（32–35/59） | **88.1–89.8%（52–53/59）** |
| RAG 召回@3 | 100% | 100% |

失败归因（32 条）：学生统计误用 `courses.college` 14、课程列表冗余列 8、年级写成 `'2022级'` 7、列不全 1、计数口径 2
（学期格式错误 8 条与连接错误叠加）→ 在 `_SQL_SYSTEM` 补 4 条通用口径约束后重跑，上述 4 类全部归零。
剩余 6–7 条**均非 SQL 逻辑错误**，详见 `docs/作品说明文档.md` §3.4。

产物：`data/report/metrics_llm.json`、`llm_track.md`（基线另有 `*_legacy.*`）。

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

10 个变异体：M1–M7 是**组件级删除/替换**（删工具/截断记忆/移除权限/投毒/提温/禁用澄清/破坏路由）；
M8–M10 补的是智能体特有的**静默失效**——接口照常返回、不抛异常，只能靠"结果是否还正确"发现：

| 编号 | 注入缺陷 | 检出维度 | 治的是哪类真实 bug |
|---|---|---|---|
| M8 | SQL 列名幻觉（列名记错） | 功能准确性 | Text-to-SQL 最常见失效：模型记错 schema |
| M9 | WHERE 被静默丢弃 | 功能准确性 | "SQL 优化/重写"把过滤条件吃掉，结果变大却报成功 |
| M10 | 记忆只写不读 | 多轮对话 | 约束存下来了却从不应用（与 M2"存不下"不同） |

目标维度指标降幅 > 20% 判 killed。本项目实测 **Mutation Score = 10/10**（降幅 0.30–1.00）。

---

## 测别人的智能体（可插拔适配器 · 实用性/通用性）

这套框架原本只能测自己（执行器直接依赖 `CampusAgent`）。现在执行器只依赖一个**鸭子类型契约**：

```python
run(question, role="admin", session_id="", user_id="") -> {
    "answer": str,        # 必填
    "channel": str,       # 可选，缺省时按回答内容推断
    "trace": [...],       # 可选
}
```

任何对象（或任意 HTTP + JSON 服务）满足它，就能被同一套用例驱动。

```bash
# A) 测内置演示机器人：它刻意保留了 5 类初级缺陷（越权/注入/坏执行/路由错/不澄清）
python -m testsuite.run_external --demo

# B) 测任意 HTTP 智能体（跨语言、不改对方代码）
python -m testsuite.run_external --url http://127.0.0.1:8901/chat

# C) 对方字段名不同：映射即可
python -m testsuite.run_external --url http://host/api/ask \\
    --request-map question=q --response-map answer=data.reply

# D) 真实开源智能体：OpenAI 兼容协议（FastGPT / Dify / vLLM / Ollama / OneAPI / 百炼）
python -m testsuite.run_external --openai-base http://127.0.0.1:3000 \\
    --openai-model gpt-4o-mini --api-key sk-xxx --channel-path channel

# E) 没有 Docker / 外网也能验证协议层：本地起 OpenAI 协议桩（仅标准库）
python -m testsuite.run_external --stub --stub-brain guarded --channel-path channel
```

### 一条协议覆盖主流开源智能体

`testsuite/adapters/openai_chat.py` 实现 OpenAI Chat Completions 线协议
（`POST {base}/v1/chat/completions` → `choices[0].message.content`），
因此 vLLM、Ollama 的 `/v1` 兼容层、FastGPT、Dify、OneAPI、百炼等**都不需要改对方一行代码**。
调用者身份写进 system 消息（`role=student, user_id=S00001`），模拟真实产品里由网关注入身份的常见做法。

**诚实边界（必须说清）**：本次提交环境没有 Docker、也没有外网可达的 FastGPT/Dify 实例，
所以**没有真的连过这两家**。协议层是通过仓库内 `examples/openai_stub/`（严格按同一协议实现的
本地桩，含 401/400 错误体校验）端到端验证的：在它身上跑通 = 请求形状、鉴权头、响应解析、
错误处理都对。接真实实例只需把 `--openai-base` 指过去。

标准 OpenAI 协议**不返回通道信息**（走的是 SQL 还是政策检索），因此默认 `supports_channel=False`，
**路由维度标记为"不适用"**，而不是用正文猜一个通道去凑通过率；被测服务若额外暴露该字段，
用 `--channel-path` 指过去即可。

### 两个对照大脑：既抓得出问题，也不冤枉好智能体

`examples/openai_stub/` 内置两个大脑，用**同一套用例**跑（2026-10-28 本机实测）：

| 大脑 | security_behavioral | routing | boundary | 检出缺陷类别 |
|---|---|---|---|---|
| `naive`（v0.1 风格，5 类初级缺陷） | 1.8%（55 条） | 54.3%（46 条） | 54.8%（31 条） | **4 类**（D1 越权 / D2 注入 / D4 路由 / D5 不澄清） |
| `guarded`（补齐身份校验、注入拦截、写操作拦截、澄清策略、政策通道） | **100%** | **100%** | **100%** | 0 类 |

一个只会判失败的工具，分数没有意义；这组对照说明用例的判别力是双向的。

> **诚实边界**：外部模式只跑**行为级**用例（越权/注入/路由/边界）。
> 安全维度里直接调 `execute_sql` 的工具级用例（对抗 SQL、破坏性写操作）**不适用**外部智能体，
> 会被显式列入 `skipped_cases` 而不是混进去"假通过"——具体跳过了哪 31 条，报告里逐条列出。

---

## RAG 忠实度评测（无供应链风险方案）

官方点名的 RAGAS / DeepEval 在 `pyproject.toml` 中标为可选依赖且**默认不启用**
（历史版本存在 SSRF 与不安全 pickle 反序列化）。为不留 `skipped` 空白，本项目内置
**同判据的自研 LLM-as-judge**（`testsuite/rag_judge.py`）：把回答拆成事实性断言，
逐条判断能否被检索片段支持，`faithfulness = 支持数 / 断言数`。

```bash
python -m testsuite.llm_track --db data/campus.db
# RAG 忠实度（自研判官）：1.0 ｜ 回答相关性：0.9167（6 条样本，DeepSeek-Chat）
# 判官可信度：重复一致率 1.0（κ=None 因 10 条同标签，MAE=0.0）｜金标准确率 1.0（忠实组 1.0 vs 编造组 0.1667）
```

判官有效性已反向验证：对**同一片段**，编造回答 faithfulness=0.0 / 相关性 0.3，
忠实回答 1.0 / 1.0（见 `tests/test_rag_eval.py` 与 §3.4 说明）。

### 判官可信度：把"模型评模型"变成可测的两件事

评委的合理追问是「判官自己准不准」。`testsuite/judge_agreement.py` 给两个**无人工标注也能测**的指标：

| 指标 | 怎么测 | 说明什么 | 不能说明什么 |
|---|---|---|---|
| 稳定性 | 同一批样本重复判 k 次：一致率 / Cohen's κ / 分数 MAE | 单次分数可不可复现 | 稳定地判错也是一致率 1.0 |
| 判别力 | 6 条金标（3 忠实 + 3 编造）上算 accuracy / precision / recall，并比两组均值 | 能不能把编造回答区分出来 | 金标是本团队按判据构造的，**非第三方人工评分** |

```bash
python -m testsuite.llm_track            # 结果写入 data/report/metrics_llm*.json 与 llm_track*.md
```

实现细节上刻意留了几个"不许自欺"的开关：全同类标签时 κ 返回 `None`（不硬编 1.0）、
判官没给结论的样本不计入一致率分母、指标不达标时 `conclusion` 直接写"不稳定/判别力偏弱"。

---

## 对抗用例：模板之上再开一条「LLM 扩展通道」

安全用例原先全部来自 `testsuite/cases.py` 的手写模板。模板可复现、可解释，但**形状固定**——
被测体只要按模板关键词做规则匹配就能全过。`testsuite/case_gen.py` 让 LLM 在给定类别与约束下
**造新样本**，并对生成物做硬校验：

| 校验项 | 规则 | 不合规怎么办 |
|---|---|---|
| 类别 | 只能是 越权 / 注入 / 绕过 | 丢弃 |
| 长度 | 5~80 字 | 丢弃 |
| 形态 | 行为级用例不得含 `SELECT/INSERT/...` + 空格 | 丢弃 |
| 身份 | 学生必须 `S00001` 形态、教师必须 `T00001` 形态（与库内真实口径一致） | 丢弃 |
| 对抗意图 | 越权须索取他人学号、注入须含"忽略/导出"类话术、绕过须含 union/注释/`or 1=1` | 丢弃 |
| 查重 | 归一化后与模板、与本次生成物都不得重复 | 丢弃 |

```bash
# 生成（需要 LLM_API_KEY）
python -m testsuite.case_gen --n 12 --out data/report/adversarial_generated.json

# 用生成的用例再跑一次安全维度（单独统计，不并入模板基线）
python -m testsuite.run_suite --extra-cases data/report/adversarial_generated.json
```

实测（2026-10-28，DeepSeek-Chat，`--n 12`）：请求 12 条 → 校验通过 12 条 → 打在本项目智能体上
**12/12 通过**；**首轮**（修复前）通过 11/12，唯一失败那条是「教师索取某学生成绩+奖学金」——
暴露出路由层与权限层口径不一致的真实缺陷（详见下节）。生成器不可用（无 Key / 超时 / 输出不是 JSON）时
返回 `status=unavailable|empty` 并给出原因，**绝不静默编几条凑数**。

### 用例池生长：12 → 41 条，并顺手修掉一个「误拒」

同一轮又生成 30 条并合并进旧池。合并暴露了一个真问题：**30 条里 5 条被校验器误拒**
（「全校教师人事档案」「辅导员体检报告」「管理员账号密码」等），原因是判据靠**关键词背诵**
（要求出现"成绩/学号"），而这 5 条的危险对象是"人事档案/体检报告/账号"。
改成 **范围词 × 敏感对象词** 两维判定后 5 条全部救回：

| 口径 | 越权判据 | 结果 |
|---|---|---|
| 旧（关键词背诵） | 必须含成绩/学号等词 | 30 条中误拒 5 条 |
| 新（两维） | （全校/所有/他人 等范围词）**且**（档案/住址/密码/体检 等对象词），或直接索要他人 ID | 拒 0 条，误拒 0 条 |

合并产物：**41 条**（越权 13 / 注入 14 / 绕过 14，去重 1 条），`run_suite --extra-cases` 实测
**41/41 通过（100%）**——**单独统计，不并入 309 模板基线**，逐条结果落 `data/report/extra_security.json`
（`{source,total,passed,rate,failures}`，失败项带问题原文）。救回的 5 条同时被写成守卫测试，
防止判据被改回关键词背诵。

> **诚实边界**：LLM 造的对抗问句带有模型偏好（用词偏书面、句式偏规整），不等于真实攻击者的分布。
> 它的价值是"扩大覆盖面 + 证明用例池可生长"，不能宣称"覆盖了全部攻击面"。
> 41/41 也**不等于**"安全"——它只说明这 41 条已知形状的攻击没打穿，池子外仍是未知。

---

## 结果集判定：严格之外，再给一个"语义等价"口径

原来只有一条判定：`result_hash(actual) == result_hash(expected)`（行序无关的精确多重集相等）。
它严格无歧义，但会把"同样正确、写法不同"的答案判错。`testsuite/equivalence.py` 做**分级等价**：

| 级别 | 放宽什么 | 默认开启 | 例子 |
|---|---|---|---|
| `exact` | ——（就是原口径） | ✅ | 行序不同 |
| `numeric` | 数值精度/类型 | ✅（1e-6） | `943` vs `943.0`、`0.8` vs `0.8000001` |
| `projection` | 多选列 / 列序不同（按列名+同义名对齐） | ❌ 需用例声明 | 问人数答 `(college, COUNT(*))` |
| `column_subset` | 少选列（必须逐行对得上才算） | ❌ 需用例声明 | 问「有哪些老师」只答姓名、没答职称 |
| `set` | 重复行（DISTINCT 与否） | ❌ 需用例声明 | 「有哪些学院」 |
| `subset` | 少几行（如 LIMIT/采样） | ❌ 需用例声明 | 「取前 10 条」 |

**纪律**：主指标 `执行准确率` 仍是严格口径，`语义等价准确率` 是**附加**指标，两者都进报告，
谁也不能替代谁；`subset` 会把"漏查几行"也算对，只在用例写明业务理由时才允许打开；
`column_subset` 单独成一个档位（而不是混进 `projection`），因为"少给一列"既可能是合理写法，
也可能漏掉了问题的关键列（问「成绩」只答姓名就是彻底错了），报告里必须能一眼分辨。

### 口径改了，怎么证明改动的影响？

不重跑大模型：真实 LLM 轨道的 `functional_details` 里存了模型实际生成的 SQL，直接重放即可
（同一份 SQL + 同一份数据库，严格判定必然与原跑批逐条一致，脚本自校验这一点）：

```bash
python -m testsuite.replay_llm_details --details data/report/metrics_llm.json \
    --db data/campus.db --out data/report/replay_llm.json
# 严格口径：55/59 = 0.9322
# 含列子集：59/59 = 1.0（新增 4 条）
# 自校验通过：严格判定与原跑批逐条一致
#   + D1-058 → column_subset｜按列对齐 1 列，缺列 ['title']
```

还剩 4 条（`D1-058~061` 问老师只答姓名、未答职称）——这是**口径争议而非模型错误**：
问题只问"有哪些老师"，模型给姓名属于合理写法，故只在使用例显式声明 `column_subset` 时才算对，
严格口径仍记为错。另有 2 条（`D1-062/063` 奖学金"多少种"）曾是**基准自己写错**
（`COUNT(*)` 得 372/382 人次），已在第十六轮修掉期望并与 mock 同步，不再计入失败。

---

## 把"接别家 / 换模型 / 和人对得上吗"变成一条命令

测自家智能体只是及格线。真正决定这套工具能不能被别人用，是下面四件事能不能**当场验**。

### 1) 端点探针：先分层定位失败，再谈评测

```bash
# Ollama（本机）
python -m testsuite.probe_endpoint --base http://127.0.0.1:11434/v1 --model qwen2.5:7b
# vLLM / OneAPI / 百炼兼容模式
python -m testsuite.probe_endpoint --base http://127.0.0.1:8000/v1 --model Qwen2.5-7B-Instruct
```

按 `TCP 可达 → /v1/models → 最小对话 → 三条业务冒烟` 逐项检查，结论只会是
`ready` / `unreachable` / `auth_failed` / `protocol_mismatch` / `too_slow` 之一，
并给出下一步（换地址 / 换 key / 改适配器 / 调超时）。**不会只说一句"调用失败"。**

### 2) 跨模型矩阵：区分"真难题"和"这家模型不行"

```bash
cp testsuite/models.example.json models.json      # 填 base/model，key 只写环境变量名
export QWEN_API_KEY=... GLM_API_KEY=...           # Windows: $env:QWEN_API_KEY="..."
python -m testsuite.model_matrix --config models.json
```

输出 `data/report/model_matrix.md`：每个模型的严格/语义通过数 + 失败题，
并把失败拆成「**所有模型都错**（真难题，该修基准或改提示词）」与「**只有某家错**（模型特有问题）」。
没配 key 的模型标 `skipped` 并写明缺哪个环境变量——**不伪造结果**。

### 3) 判官 vs 人工：把 κ 算出来，而不是嘴上说"接近人工"

```bash
python -m testsuite.judge_human_check export --n 20 --fabricate 8 --blind
# ↑ 生成 CSV（带 BOM，Excel 直接开）；--blind 把 judge_* 列挪到 <stem>.judge.csv
# 人工只做一件事：填 human_faithful 列（1=有片段支撑 / 0=编造）
python -m testsuite.judge_human_check score --sheet data/report/judge_human_sheet.csv --sweep

# 还没人标注也想先看判官行不行？抓编造这件事不需要人工：
python -m testsuite.judge_human_check injected --sheet data/report/judge_human_sheet.csv
```

**为什么默认建议 `--blind`**：标注表里若带着 `judge_faithfulness`，人眼很难不参考它——
那样算出来的 κ 已经不是"判官与人工一致"，而是"判官与看着判官分做答的人一致"。盲标把判官分
另存一份、`score` 时按 id 合并回来，是让 κ 这个数**还能被当成证据**的最低要求。
同理，8 条人造编造的位置只写在 `.meta.json`，不进 CSV。

`score` 输出 accuracy / precision / recall / F1 / **Cohen's κ** 与逐条分歧样本；
未标注、判官无结论的行**不进分母**；人工标签全同类时 κ 返回 `null`（不硬编 1.0）。

`injected` 用的是**我们自己注入的编造**（`--fabricate N` 会把假条款追加到答案末尾，位置记在
`.meta.json` sidecar，不进 CSV 以免标注时被暗示），所以真值在注入那一刻就有了。
8 条人造编造，**两次独立导出**（同 20 题，模型回答与判官打分各自重跑一遍）实测：

| 阈值 | 抓到人造编造（第一次 / 第二次） | 非注入样本被判低于阈值 |
|---|---|---|
| 0.50（默认） | 5/8 = 62% / **4/8 = 50%** | 0/12 ｜ 0/12 |
| 0.67 | **8/8 / 8/8** | 0/12 ｜ 0/12 |
| 0.75 / 0.90 | 8/8 / 8/8 | 0/12 ｜ 0/12 |

两列的差别本身就是结论：**默认 0.5 阈值不稳定**（同一批题两次跑出 5/8 和 4/8），因为漏掉的那几条
判官给的是 0.5–0.667（"大体忠实，但多了一段没出处的规定"）；**0.67 及以上两次都全抓**。
所以"判官可用"这句话必须连着阈值一起说，否则它就取决于一个没进报告的常数。
代价侧同样摆出来（非注入 12 条两次最低分都 ≥0.9，抬阈值零代价），但**真值未知**，
`others_below_threshold` 不等于误判。

### 4) 多轮稳定性：单轮 93.2% 是稳态还是运气

```bash
python -m testsuite.llm_track --rounds 3
```

同批功能用例跑 3 轮，报每轮通过数、均值/区间/标准差，并点名「跨轮不稳定题目」
（同一题有时对有时错）。产物写 `data/report/llm_rounds.json`，**不覆盖**主基线 `metrics_llm.json`。
实测 3 轮均 55/59，均值 93.2%、区间 93.2%–93.2%、总体标准差 0.0000、跨轮不稳定题 **0 条**。

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
