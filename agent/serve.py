"""FastAPI 服务（FR-A08 接口定义）。

启动：uvicorn agent.serve:app --reload --port 8000
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from pydantic import BaseModel

from .graph import CampusAgent

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
async def chat(req: ChatRequest) -> dict:
    role = _resolve_role(req.role)
    return agent.chat(req.question, role=role, session_id=req.session_id, user_id=req.user_id)


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
