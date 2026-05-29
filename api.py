"""FastAPI Web 服务"""

import json
import uuid
import logging
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import load_config
from models import Conversation, Message
from database import AgentDatabase
from tool_registry import registry
from model_router import ModelRouter
from agent import AgentOrchestrator

logger = logging.getLogger(__name__)

# 初始化
config = load_config()
db = AgentDatabase(config["database"]["path"])
router = ModelRouter(config)
orchestrator = AgentOrchestrator(router, registry, config)

app = FastAPI(title="Agent 工具调用平台", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    model: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    messages: list[dict]
    model: str


@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """同步对话接口"""
    # 获取或创建对话
    conv_id = request.conversation_id
    history = []

    if conv_id:
        conv = db.get_conversation(conv_id)
        if conv:
            history = db.get_messages(conv_id)
    else:
        conv_id = str(uuid.uuid4())
        conv = Conversation(id=conv_id, title=request.message[:50])
        db.create_conversation(conv)

    # 执行 Agent
    messages = await orchestrator.run(
        user_input=request.message,
        conversation_id=conv_id,
        model=request.model,
        history=history,
    )

    # 保存消息
    for msg in messages:
        db.save_message(conv_id, msg)

    return ChatResponse(
        conversation_id=conv_id,
        messages=[m.model_dump(mode="json") for m in messages],
        model=request.model or config.get("default_model", "deepseek-chat"),
    )


@app.get("/api/conversations")
async def list_conversations():
    """列出所有对话"""
    return db.list_conversations()


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    """获取对话详情"""
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    messages = db.get_messages(conv_id)
    return {**conv, "messages": [m.model_dump(mode="json") for m in messages]}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    """删除对话"""
    from pathlib import Path
    with db._get_conn() as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    return {"status": "deleted"}


@app.get("/api/tools")
async def list_tools():
    """列出所有工具"""
    tools = registry.list_tools()
    return [t.model_dump() for t in tools]


@app.get("/api/models")
async def list_models():
    """列出可用模型"""
    return router.list_models()


@app.get("/api/stats")
async def get_stats():
    """获取统计信息"""
    return db.get_stats()


def run_server(host: str = "0.0.0.0", port: int = 8000):
    """启动服务"""
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")
