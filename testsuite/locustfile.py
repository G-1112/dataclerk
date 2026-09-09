"""Locust 性能/负载测试（性能维度）。

用法（先启动服务）：
    uvicorn agent.serve:app --port 8000
    locust -f testsuite/locustfile.py --host http://127.0.0.1:8000

默认混合三类请求：数据统计（SQL 通道）、政策问答（RAG 通道）、越权攻击（安全通道）。
"""

from __future__ import annotations

from locust import HttpUser, between, task

_QUESTIONS = [
    ("admin", "计算机学院有多少学生？", ""),
    ("admin", "软件学院的平均绩点是多少？", ""),
    ("admin", "奖学金评定需要什么条件？", ""),
    ("student", "查询学号 S00002 的成绩", "S00001"),
    ("teacher", "本院系学生平均绩点是多少？", ""),
]


class CampusUser(HttpUser):
    wait_time = between(0.5, 2.0)

    @task(3)
    def sql_query(self):
        role, q, uid = _QUESTIONS[0]
        self.client.post("/chat", json={"question": q, "role": role, "user_id": uid})

    @task(2)
    def rag_query(self):
        _, q, uid = _QUESTIONS[2]
        self.client.post("/chat", json={"question": q, "role": "admin", "user_id": uid})

    @task(1)
    def adversarial_query(self):
        _, q, uid = _QUESTIONS[3]
        self.client.post("/chat", json={"question": q, "role": "student", "user_id": uid})
