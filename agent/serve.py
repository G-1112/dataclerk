"""FastAPI 服务（FR-A08 接口定义）。

启动：uvicorn agent.serve:app --reload --port 8000
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from .graph import CampusAgent
from .ratelimit import MAX_QUESTION_CHARS, chat_limiter, client_ip

app = FastAPI(title="问数官 DataClerk", description="双通道校园智能助手（Text-to-SQL + 政策 RAG）", version="0.1.0")

agent = CampusAgent()


class ChatRequest(BaseModel):
    question: str
    role: str = "student"  # 默认最小权限；admin 仅服务端显式开启（见 _resolve_role）
    session_id: str = "default"
    user_id: str = ""  # student 角色下的当前学号


class ResetRequest(BaseModel):
    session_id: str = "default"


def _resolve_role(requested: str) -> str:
    """角色由服务端解析，客户端不得自提 admin。

    admin（生成标准答案用）仅当环境变量 DATACLERK_ADMIN=1 时可用，
    否则任何 admin 请求降级为 student，避免权限层被一键绕过。
    """
    if requested == "admin" and os.getenv("DATACLERK_ADMIN") != "1":
        return "student"
    if requested not in ("student", "teacher", "admin"):
        return "student"
    return requested


@app.post("/chat")
async def chat(req: ChatRequest, request: Request) -> dict:
    # 限流（第四轮新增）：/chat 每次触发 LLM + DB，此前无任何速率限制，
    # 实测连发 40 次全部 200，可被用来耗尽 LLM 配额。
    ip = client_ip(request)
    if not chat_limiter.allow(ip):
        raise HTTPException(
            status_code=429,
            detail=f"请求过于频繁，请 {chat_limiter.retry_after(ip)} 秒后重试",
            headers={"Retry-After": str(chat_limiter.retry_after(ip))},
        )

    # 输入长度上限（第四轮新增）：超长问题会被拼进 prompt，
    # 放大 token 成本并可能掩盖注入意图（实测 44000 字可被接受）。
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question 不能为空")
    if len(question) > MAX_QUESTION_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"问题过长（{len(question)} 字），上限 {MAX_QUESTION_CHARS} 字",
        )

    role = _resolve_role(req.role)
    # 异步执行，避免同步 LLM/DB 阻塞事件循环，支撑并发负载（性能维度）
    return await agent.achat(question, role=role, session_id=req.session_id, user_id=req.user_id)


@app.get("/trace/{trace_id}")
async def trace(trace_id: str) -> dict:
    return {"trace_id": trace_id, "steps": agent.tracer.get(trace_id)}


@app.post("/reset")
async def reset(req: ResetRequest) -> dict:
    agent.reset(req.session_id)
    return {"ok": True}


@app.get("/health")
async def health() -> dict:
    from . import llm

    return {"status": "ok", "llm_enabled": llm.llm_enabled()}
