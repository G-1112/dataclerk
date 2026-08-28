"""FastAPI 服务（FR-A08 接口定义）。

启动：uvicorn agent.serve:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from .graph import CampusAgent

app = FastAPI(title="问数官 DataClerk", description="双通道校园智能助手（Text-to-SQL + 政策 RAG）", version="0.1.0")

agent = CampusAgent()


class ChatRequest(BaseModel):
    question: str
    role: str = "admin"  # student / teacher / admin
    session_id: str = "default"
    user_id: str = ""  # student 角色下的当前学号


class ResetRequest(BaseModel):
    session_id: str = "default"


@app.post("/chat")
async def chat(req: ChatRequest) -> dict:
    return agent.chat(req.question, role=req.role, session_id=req.session_id, user_id=req.user_id)


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
