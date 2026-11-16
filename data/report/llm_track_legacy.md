# 真实 LLM 测试轨道 · 本机实测片段

- 模型：`deepseek-chat`｜数据库：`campus.db`
- 功能准确性（严格·结果集完全一致）：**25/59 = 42.4%**
- 功能准确性（宽松·按期望列投影）：**33/59 = 55.9%**
- RAG 召回@3（确定性 TF-IDF 检索 + LLM 合成回答）：**6/6 = 100.0%**
- 规划稳定性（temp=0 多次生成一致率）：**3/3 = 100.0%**

## 功能用例（真实 LLM 生成 SQL 抽样）

- ✅ `D1-001` 计算机学院有多少学生？
  - LLM SQL：`SELECT COUNT(*) FROM students WHERE college = '计算机学院';`
- ❌ `D1-002` 软件学院的平均绩点是多少？
  - LLM SQL：`SELECT AVG(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '软件学院' AND e.score IS NOT NULL`
- ❌ `D1-003` 计算机学院有哪些课程？
  - LLM SQL：`SELECT course_id, course_name, credit, teacher_id, semester, capacity FROM courses WHERE college = '计算机学院'`
- ❌ `D1-004` 2024年春季经济管理学院的平均成绩是多少？
  - LLM SQL：`SELECT AVG(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '经济管理学院' AND e.semester = '2024春季' AND e.score IS NOT NULL;`
- ✅ `D1-005` 理学院 2024 级有多少学生？
  - LLM SQL：`SELECT COUNT(*) FROM students WHERE college = '理学院' AND grade = '2024';`
- ✅ `D1-006` 各学院的学生人数分布是怎样的？
  - LLM SQL：`SELECT college, COUNT(*) AS student_count FROM students GROUP BY college ORDER BY student_count DESC;`
- ❌ `D1-007` 外国语学院的平均绩点是多少？
  - LLM SQL：`SELECT AVG(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '外国语学院' AND e.score IS NOT NULL`
- ✅ `D1-008` 机械工程学院有多少学生？
  - LLM SQL：`SELECT COUNT(*) FROM students WHERE college = '机械工程学院';`
- ❌ `D1-009` 文学院有哪些课程？
  - LLM SQL：`SELECT course_id, course_name, credit, teacher_id, semester, capacity FROM courses WHERE college = '文学院'`
- ❌ `D1-010` 电子信息学院的平均绩点是多少？
  - LLM SQL：`SELECT AVG(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '电子信息学院' AND e.score IS NOT NULL`
- ❌ `D1-019` 计算机学院的平均绩点是多少？
  - LLM SQL：`SELECT AVG(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '计算机学院' AND e.score IS NOT NULL`
- ✅ `D1-020` 机械工程学院的平均绩点是多少？
  - LLM SQL：`SELECT AVG(e.score) FROM enrollments e JOIN students s ON e.student_id = s.student_id WHERE s.college = '机械工程学院' AND e.score IS NOT NULL`
- ❌ `D1-021` 经济管理学院的平均绩点是多少？
  - LLM SQL：`SELECT AVG(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '经济管理学院' AND e.score IS NOT NULL AND e.status = 'completed'`
- ❌ `D1-022` 理学院的平均绩点是多少？
  - LLM SQL：`SELECT AVG(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '理学院' AND e.status = 'completed' AND e.score IS NOT NULL`
- ❌ `D1-023` 文学院的平均绩点是多少？
  - LLM SQL：`SELECT AVG(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '文学院' AND e.score IS NOT NULL`
- ❌ `D1-024` 软件学院有哪些课程？
  - LLM SQL：`SELECT course_id, course_name, credit, teacher_id, semester, capacity FROM courses WHERE college = '软件学院'`
- ❌ `D1-025` 电子信息学院有哪些课程？
  - LLM SQL：`SELECT course_id, course_name, credit, teacher_id, semester, capacity FROM courses WHERE college = '电子信息学院'`
- ❌ `D1-026` 机械工程学院有哪些课程？
  - LLM SQL：`SELECT course_id, course_name, credit, teacher_id, semester, capacity FROM courses WHERE college = '机械工程学院'`
- ❌ `D1-027` 经济管理学院有哪些课程？
  - LLM SQL：`SELECT course_id, course_name, credit, teacher_id, semester, capacity FROM courses WHERE college = '经济管理学院'`
- ❌ `D1-028` 外国语学院有哪些课程？
  - LLM SQL：`SELECT course_id, course_name, credit, teacher_id, semester, capacity FROM courses WHERE college = '外国语学院'`
- ❌ `D1-029` 理学院有哪些课程？
  - LLM SQL：`SELECT course_id, course_name, credit, teacher_id, semester FROM courses WHERE college = '理学院'`
- ❌ `D1-030` 2023年秋季计算机学院的平均成绩是多少？
  - LLM SQL：`SELECT AVG(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '计算机学院' AND e.semester = '2023秋' AND e.score IS NOT NULL;`
- ❌ `D1-031` 2024年春季软件学院的平均成绩是多少？
  - LLM SQL：`SELECT AVG(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '软件学院' AND e.semester = '2024春季' AND e.score IS NOT NULL;`
- ❌ `D1-032` 2024年秋季经济管理学院的平均成绩是多少？
  - LLM SQL：`SELECT AVG(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE e.semester = '2024秋季' AND c.college = '经济管理学院' AND e.score IS NOT NULL;`
- ❌ `D1-033` 2025年春季理学院的平均成绩是多少？
  - LLM SQL：`SELECT AVG(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '理学院' AND e.semester = '2025春季' AND e.score IS NOT NULL;`
- ❌ `D1-034` 2023年秋季文学院的平均成绩是多少？
  - LLM SQL：`SELECT AVG(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '文学院' AND e.semester = '2023秋季' AND e.score IS NOT NULL;`
- ❌ `D1-035` 2024年秋季机械工程学院的平均成绩是多少？
  - LLM SQL：`SELECT AVG(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '机械工程学院' AND e.semester = '2024秋季' AND e.score IS NOT NULL;`
- ❌ `D1-036` 2025年春季外国语学院的平均成绩是多少？
  - LLM SQL：`SELECT AVG(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '外国语学院' AND e.semester = '2025年春季' AND e.score IS NOT NULL;`
- ❌ `D1-037` 2024年春季电子信息学院的平均成绩是多少？
  - LLM SQL：`SELECT AVG(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '电子信息学院' AND e.semester = '2024春季' AND e.score IS NOT NULL`
- ❌ `D1-038` 计算机学院2022级有多少学生？
  - LLM SQL：`SELECT COUNT(*) FROM students WHERE college = '计算机学院' AND grade = '2022级';`
- ❌ `D1-039` 计算机学院2023级有多少学生？
  - LLM SQL：`SELECT COUNT(*) FROM students WHERE college = '计算机学院' AND grade = '2023级'`
- ❌ `D1-040` 软件学院2024级有多少学生？
  - LLM SQL：`SELECT COUNT(*) FROM students WHERE college = '软件学院' AND grade = '2024级';`
- ❌ `D1-041` 软件学院2025级有多少学生？
  - LLM SQL：`SELECT COUNT(*) FROM students WHERE college = '软件学院' AND grade = '2025级'`
- ❌ `D1-042` 经济管理学院2022级有多少学生？
  - LLM SQL：`SELECT COUNT(*) FROM students WHERE college = '经济管理学院' AND grade = '2022级';`
- ❌ `D1-043` 经济管理学院2023级有多少学生？
  - LLM SQL：`SELECT COUNT(*) FROM students WHERE college = '经济管理学院' AND grade = '2023级';`
- ❌ `D1-044` 文学院2024级有多少学生？
  - LLM SQL：`SELECT COUNT(*) FROM students WHERE college = '文学院' AND grade = '2024级';`
- ✅ `D1-045` 文学院2025级有多少学生？
  - LLM SQL：`SELECT COUNT(*) FROM students WHERE college = '文学院' AND grade = '2025';`
- ✅ `D1-046` 计算机学院的最高分是多少？
  - LLM SQL：`SELECT MAX(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '计算机学院';`
- ✅ `D1-047` 计算机学院的最低分是多少？
  - LLM SQL：`SELECT MIN(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '计算机学院' AND e.score IS NOT NULL;`
- ✅ `D1-048` 软件学院的最高分是多少？
  - LLM SQL：`SELECT MAX(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '软件学院';`
- ✅ `D1-049` 软件学院的最低分是多少？
  - LLM SQL：`SELECT MIN(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '软件学院' AND e.score IS NOT NULL;`
- ✅ `D1-050` 经济管理学院的最高分是多少？
  - LLM SQL：`SELECT MAX(e.score) FROM enrollments e JOIN students s ON e.student_id = s.student_id WHERE s.college = '经济管理学院';`
- ✅ `D1-051` 经济管理学院的最低分是多少？
  - LLM SQL：`SELECT MIN(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '经济管理学院' AND e.score IS NOT NULL;`
- ✅ `D1-052` 理学院的最高分是多少？
  - LLM SQL：`SELECT MAX(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '理学院';`
- ✅ `D1-053` 理学院的最低分是多少？
  - LLM SQL：`SELECT MIN(e.score) FROM enrollments e JOIN courses c ON e.course_id = c.course_id WHERE c.college = '理学院' AND e.score IS NOT NULL;`
- ✅ `D1-054` 每个学院的学生人数分布
  - LLM SQL：`SELECT college, COUNT(*) AS student_count FROM students GROUP BY college;`
- ✅ `D1-055` 各学院人数分布
  - LLM SQL：`SELECT college, COUNT(*) AS student_count FROM students GROUP BY college`
- ✅ `D1-056` 各学院的平均绩点排名
  - LLM SQL：`SELECT s.college, AVG(e.score) AS avg_score
FROM enrollments e
JOIN students s ON e.student_id = s.student_id
WHERE e.score IS NOT NULL
GROUP BY s.college
ORDER BY avg_score DESC;`
- ✅ `D1-057` 每个学院的平均绩点排名
  - LLM SQL：`SELECT s.college, AVG(e.score) AS avg_score
FROM enrollments e
JOIN students s ON e.student_id = s.student_id
WHERE e.score IS NOT NULL
GROUP BY s.college
ORDER BY avg_score DESC;`
- ✅ `D1-058` 计算机学院有哪些老师？
  - LLM SQL：`SELECT name, title FROM teachers WHERE college = '计算机学院'`
- ✅ `D1-059` 软件学院有哪些老师？
  - LLM SQL：`SELECT name, title FROM teachers WHERE college = '软件学院'`
- ✅ `D1-060` 经济管理学院有哪些老师？
  - LLM SQL：`SELECT name, title FROM teachers WHERE college = '经济管理学院'`
- ❌ `D1-061` 理学院有哪些老师？
  - LLM SQL：`SELECT name FROM teachers WHERE college = '理学院'`
- ❌ `D1-062` 计算机学院的奖学金有多少种？
  - LLM SQL：`SELECT COUNT(DISTINCT s.name) FROM scholarships s JOIN students st ON s.student_id = st.student_id WHERE st.college = '计算机学院'`
- ❌ `D1-063` 软件学院的奖学金有多少种？
  - LLM SQL：`SELECT COUNT(DISTINCT s.name) FROM scholarships s JOIN students st ON s.student_id = st.student_id WHERE st.college = '软件学院'`
- ✅ `D1-064` 全校有多少本借阅图书逾期未还？
  - LLM SQL：`SELECT COUNT(*) FROM library_borrow WHERE return_date IS NULL AND due_date < date('now');`
- ✅ `D1-065` 有多少本借阅的图书逾期未还？
  - LLM SQL：`SELECT COUNT(*) FROM library_borrow WHERE return_date IS NULL AND due_date < DATE('now');`
- ✅ `D1-066` 全校校园卡消费总额是多少？
  - LLM SQL：`SELECT SUM(amount) FROM card_transactions;`
- ✅ `D1-067` 所有校园卡消费的总额是多少？
  - LLM SQL：`SELECT SUM(amount) FROM card_transactions;`

## RAG 用例（LLM 合成回答抽样）

- ✅ `D6-001` 国家奖学金的评定标准是什么？（期望关键词 ['奖学金']）
  - LLM 回答：`根据政策片段，国家奖学金的评定标准为：申请学生的学年综合测评成绩应排名在专业前5%以内，且无不及格科目（见《奖学金评定办法》第3段）。

片段中未提供国家奖学金的其他评定标准。`
- ✅ `D6-002` 学生考试作弊会受到什么处分？（期望关键词 ['考试']）
  - LLM 回答：`根据检索到的政策片段，只能确认以下内容：

- 《奖学金评定办法》第7段规定：学生在评定学年内有违纪处分或考试作弊记录的，不得参评各类奖学金。

关于考试作弊具体会受到何种处分（如警告、记过、留校察看、开除学籍等），片段中《考试纪律与处分条例》和《违纪处分实施细则》仅出现标题，未包含具体处分条款，因此**未找到相关政策**，无法给出具体处分种类。建议查阅上述两份文件的完整内容。`
- ✅ `D6-003` 图书馆图书逾期未还要收多少钱？（期望关键词 ['借阅']）
  - LLM 回答：`根据《图书馆借阅规则》第4段，逾期未还的图书按每天每册 0.1 元收取逾期费。

另外，第5段提到图书遗失的，应按书价的一定倍数赔偿，或购买相同版本图书赔偿（该条针对遗失，非逾期）。`
- ✅ `D6-004` 申请休学需要什么条件？（期望关键词 ['休学']）
  - LLM 回答：`根据政策片段，申请休学的条件为：学生因**身体原因、家庭原因或其他特殊原因**需要暂时中断学业的，可以申请休学。（依据“休学与复学规定 · 第2段”）

片段中未提及其他具体条件或限制，也未找到更细化的条件说明。`
- ✅ `D6-005` 转专业的流程是什么？（期望关键词 ['转专业']）
  - LLM 回答：`根据现有政策片段，只能确认以下几点，**完整的转专业流程未找到相关政策**：

1. **申请前提**：学生一般应在入学时确定的专业完成学业，确有特殊困难的可以申请转专业（第2段）。
2. **成绩要求**：申请转专业的学生，第一学年各门课程成绩应当合格（第3段）。

片段中未涉及具体办理步骤（如申请时间、提交材料、审批环节等），建议向教务部门进一步咨询。`
- ✅ `D6-006` 校园卡遗失后应该怎么办？（期望关键词 ['校园卡']）
  - LLM 回答：`根据政策片段，校园卡遗失后应当立即通过服务渠道挂失；挂失生效前的损失由持卡人自行承担。（依据：校园卡使用规定·第3段）

片段中未提及补办流程等其他内容，未找到相关政策。`
