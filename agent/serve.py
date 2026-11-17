"""FastAPI 服务（FR-A08 接口定义）。

启动：uvicorn agent.serve:app --reload --port 8000
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
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
    token: str = ""  # 开启 DATACLERK_AUTH_SECRET 后必带（HMAC 身份签名）


class ResetRequest(BaseModel):
    session_id: str = "default"
    user_id: str = ""  # 第十二轮新增：/reset 只清自己的槽位
    token: str = ""


def _auth_secret() -> str:
    return os.getenv("DATACLERK_AUTH_SECRET", "")


def expected_token(user_id: str, role: str, session_id: str, secret: str | None = None) -> str:
    """身份签名 = HMAC-SHA256(secret, "user_id|role|session_id")。

    供客户端/测试按同一算法计算 token（见 README「身份签名」一节）。
    """
    secret = _auth_secret() if secret is None else secret
    return hmac.new(
        secret.encode("utf-8"),
        f"{user_id}|{role}|{session_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _verify_identity(user_id: str, role: str, session_id: str, token: str) -> None:
    """第十二轮修复（实证 P0）：原实现直接信任请求体里的 user_id/role——

    攻击者自称受害者即可读取其成绩/消费明细（`detect_foreign_student_id`
    比较的两个值都来自客户端，自称即绕过）。修复：设置 DATACLERK_AUTH_SECRET 后，
    user_id 必须配 HMAC 签名（服务端签发/分发，客户端无法伪造他人签名）。
    未设置 secret 时为本地演示模式（信任模型已在 README 显式声明）。
    """
    if not _auth_secret():
        return
    if not user_id or not hmac.compare_digest(token or "", expected_token(user_id, role, session_id)):
        raise HTTPException(status_code=401, detail="身份校验失败：缺少或错误的 token")


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
    _verify_identity(req.user_id, role, req.session_id, req.token)
    # 异步执行，避免同步 LLM/DB 阻塞事件循环，支撑并发负载（性能维度）
    return await agent.achat(question, role=role, session_id=req.session_id, user_id=req.user_id)


@app.get("/trace/{trace_id}")
async def trace(trace_id: str) -> dict:
    return {"trace_id": trace_id, "steps": agent.tracer.get(trace_id)}


@app.post("/reset")
async def reset(req: ResetRequest) -> dict:
    # 第十二轮修复：/reset 此前无鉴权且清整个会话（可清空他人记忆）。
    # 现在只清 (session_id, user_id) 自己的槽位；开启签名鉴权后需带 token。
    _verify_identity(req.user_id, "student", req.session_id, req.token)
    agent.reset(req.session_id, req.user_id or None)
    return {"ok": True}


@app.get("/health")
async def health() -> dict:
    from . import llm

    return {
        "status": "ok",
        "llm_enabled": llm.llm_enabled(),
        "auth_enabled": bool(_auth_secret()),
    }


# ---------------------------------------------------------------- 根路由 / 控制台
_WEB_INDEX = Path(__file__).resolve().parent / "web" / "index.html"


@app.get("/")
async def root() -> dict:
    """服务入口：给浏览器/评委一个明确的端点清单，而不是冷冰冰的 404。"""
    return {
        "service": "问数官 DataClerk",
        "version": app.version,
        "description": "双通道校园智能体（Text-to-SQL + 政策 RAG）+ 545 条用例的评测体系",
        "endpoints": {
            "POST /chat": "提问，返回 {answer, channel, trace_id}；channel ∈ sql | rag | deny | mixed",
            "GET /trace/{trace_id}": "取该次执行的逐步轨迹（工具、思考、生成的 SQL）",
            "POST /reset": "清空指定会话记忆（只清自己的槽位）",
            "GET /health": "健康检查（LLM 是否启用、鉴权是否开启）",
            "GET /ui": "可视化控制台（网页里直接提问并查看执行过程）",
            "GET /docs": "OpenAPI 交互文档（Swagger）",
        },
        "ui": "/ui",
        "docs": "/docs",
        "demo_identity": {"role": "student", "user_id": "S00001"},
    }


@app.get("/ui", response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    """极简控制台：单文件、零构建、零额外依赖。

    为什么用原生 HTML 而不是再引一个前端框架：
    本项目的评审重点是智能体与测试体系，前端只需证明"接口能被真实调用"，
    引入构建链会让 `10 分钟复现` 多一步，得不偿失。
    """
    if not _WEB_INDEX.exists():  # pragma: no cover - 打包异常时的兜底
        raise HTTPException(status_code=500, detail="控制台页面缺失：agent/web/index.html")
    return HTMLResponse(_WEB_INDEX.read_text(encoding="utf-8"))
