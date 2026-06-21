"""
Agent 工具调用平台 - Web Dashboard 管理界面
FastAPI 服务，提供 Agent 平台管理的 REST API

注意: 核心 API 已在 api.py 中实现。
本模块提供独立的管理 Dashboard 层。
"""

import sys
import os
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ============================================================
# 初始化
# ============================================================

app = FastAPI(title="Agent 平台管理面板", version="1.0.0")
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost,http://127.0.0.1").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# 尝试导入核心组件
try:
    from config import load_config
    from models import Conversation, Message
    from database import AgentDatabase
    from tool_registry import registry
    from model_router import ModelRouter
    from agent import AgentOrchestrator
    from tool_loader import load_tools_from_directory

    load_tools_from_directory()
    config = load_config()
    db = AgentDatabase(config["database"]["path"])
    router = ModelRouter(config)
    orchestrator = AgentOrchestrator(router, registry, config)
    orchestrator.set_database(db)
    HAS_CORE = True
except ImportError as e:
    HAS_CORE = False
    _import_error = str(e)


# ============================================================
# 请求模型
# ============================================================

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    model: Optional[str] = None


# ============================================================
# API 路由
# ============================================================

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "has_core": HAS_CORE,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/stats")
async def get_stats():
    """获取平台统计"""
    if not HAS_CORE:
        return {"error": "核心模块未加载", "detail": _import_error}
    return db.get_stats()


@app.get("/api/conversations")
async def list_conversations():
    """列出所有对话"""
    if not HAS_CORE:
        return []
    return db.list_conversations()


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    """获取对话详情"""
    if not HAS_CORE:
        raise HTTPException(500, "核心模块未加载")
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    messages = db.get_messages(conv_id)
    return {**conv, "messages": [m.model_dump(mode="json") for m in messages]}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    """删除对话"""
    if not HAS_CORE:
        raise HTTPException(500, "核心模块未加载")
    with db._get_conn() as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    return {"status": "deleted"}


@app.post("/api/chat")
async def chat(request: ChatRequest):
    """同步对话"""
    if not HAS_CORE:
        raise HTTPException(500, "核心模块未加载")
    import uuid

    conv_id = request.conversation_id
    history = []

    if conv_id:
        conv = db.get_conversation(conv_id)
        if conv:
            history = db.get_messages(conv_id)
    else:
        conv_id = str(uuid.uuid4())
        title = await orchestrator.generate_title(request.message, request.model)
        conv = Conversation(id=conv_id, title=title, model=request.model or "deepseek-chat")
        db.create_conversation(conv)

    messages = await orchestrator.run(
        user_input=request.message,
        conversation_id=conv_id,
        model=request.model,
        history=history,
    )

    for msg in messages:
        db.save_message(conv_id, msg)

    return {
        "conversation_id": conv_id,
        "messages": [m.model_dump(mode="json") for m in messages],
        "model": request.model or config.get("default_model", "deepseek-chat"),
    }


@app.get("/api/tools")
async def list_tools():
    """列出所有工具"""
    if not HAS_CORE:
        return []
    tools = registry.list_tools()
    return [t.model_dump() for t in tools]


@app.get("/api/models")
async def list_models():
    """列出可用模型"""
    if not HAS_CORE:
        return []
    return router.list_models()


@app.get("/api/tool-logs/{conv_id}")
async def get_tool_logs(conv_id: str):
    """获取工具调用日志"""
    if not HAS_CORE:
        raise HTTPException(500, "核心模块未加载")
    with db._get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tool_logs WHERE conversation_id = ? ORDER BY created_at ASC",
            (conv_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ============================================================
# Web UI
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def web_ui():
    html_path = Path(__file__).parent / "static" / "index.html"
    return html_path.read_text(encoding="utf-8")


# ============================================================
# 启动入口
# ============================================================

def run_server(host: str = "0.0.0.0", port: int = 8003):
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_server()
