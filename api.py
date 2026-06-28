"""FastAPI Web 服务 — 支持 Dify 风格的应用管理、知识库、工作流、流式 SSE"""

import json
import os
import uuid
import logging
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from config import load_config
from models import Conversation, Message
from database import AgentDatabase
from tool_registry import registry
from model_router import ModelRouter
from agent import AgentOrchestrator
from tool_loader import load_tools_from_directory
from app_manager import AppManager
from knowledge_manager import KnowledgeManager
from workflow_engine import WorkflowEngine

# 加载扩展工具
load_tools_from_directory()

logger = logging.getLogger(__name__)

# 初始化
config = load_config()
db = AgentDatabase(config["database"]["path"])
router = ModelRouter(config)
orchestrator = AgentOrchestrator(router, registry, config)
orchestrator.set_database(db)
app_manager = AppManager(db)
knowledge_manager = KnowledgeManager(db)
workflow_engine = WorkflowEngine(db, router, knowledge_manager, registry)

app = FastAPI(title="Agent LLM 应用平台", version="3.0.0")
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost,http://127.0.0.1").split(",")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


# ==================== Request/Response Models ====================

class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    model: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    messages: list[dict]
    model: str


class AppCreateRequest(BaseModel):
    name: str
    description: str = ""
    app_type: str = "chat"
    icon: str = "🤖"
    model_cfg: dict = Field(default_factory=lambda: {
        "provider": "deepseek", "model": "deepseek-chat",
        "temperature": 0.7, "max_tokens": 4096,
    }, alias="model_config")
    system_prompt: str = "你是一个智能助手。"
    tool_ids: list[str] = Field(default_factory=list)
    dataset_ids: list[str] = Field(default_factory=list)
    workflow_config: dict = Field(default_factory=dict)

    def model_dump(self, **kwargs):
        d = super().model_dump(**kwargs)
        if "model_cfg" in d:
            d["model_config"] = d.pop("model_cfg")
        return d

    class Config:
        populate_by_name = True


class AppUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    app_type: str | None = None
    icon: str | None = None
    model_cfg: dict | None = Field(default=None, alias="model_config")
    system_prompt: str | None = None
    tool_ids: list[str] | None = None
    dataset_ids: list[str] | None = None
    workflow_config: dict | None = None
    status: str | None = None


class DatasetCreateRequest(BaseModel):
    name: str
    description: str = ""
    embedding_model: str = "tfidf"
    chunk_size: int = 500
    chunk_overlap: int = 50


class DatasetUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    status: str | None = None


class DocumentUploadRequest(BaseModel):
    name: str
    content: str
    file_type: str = "txt"


class SearchRequest(BaseModel):
    dataset_ids: list[str]
    query: str
    top_k: int = 5


class ModelProviderCreateRequest(BaseModel):
    name: str
    provider_type: str
    api_key: str = ""
    base_url: str = ""
    models: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)
    enabled: bool = True


class ModelTestRequest(BaseModel):
    provider_type: str = "deepseek"
    model: str = "deepseek-chat"
    api_key: str = ""
    base_url: str = ""
    test_message: str = "Hello, say hi in 5 words."


class ToolCreateRequest(BaseModel):
    name: str
    description: str = ""
    tool_type: str = "api"
    api_config: dict = Field(default_factory=dict)
    code: str = ""
    parameters: list[dict] = Field(default_factory=list)
    enabled: bool = True


class ToolUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    tool_type: str | None = None
    api_config: dict | None = None
    code: str | None = None
    parameters: list[dict] | None = None
    enabled: bool | None = None


class WorkflowRunRequest(BaseModel):
    inputs: dict = Field(default_factory=dict)
    conversation_id: str | None = None


class AppChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


# ==================== 原有 API 路由（保持不变）====================

@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """同步对话接口"""
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

    return ChatResponse(
        conversation_id=conv_id,
        messages=[m.model_dump(mode="json") for m in messages],
        model=request.model or config.get("default_model", "deepseek-chat"),
    )


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    """真正的流式对话接口（SSE）— 逐 token 输出"""
    conv_id = request.conversation_id or str(uuid.uuid4())
    history = []

    if request.conversation_id:
        conv = db.get_conversation(conv_id)
        if conv:
            history = db.get_messages(conv_id)
    else:
        model = request.model or config.get("default_model", "deepseek-chat")
        title = await orchestrator.generate_title(request.message, request.model)
        conv = Conversation(id=conv_id, title=title, model=model)
        db.create_conversation(conv)

    async def event_generator():
        full_response = ""
        async for msg in orchestrator.run_stream(
            user_input=request.message,
            conversation_id=conv_id,
            model=request.model,
            history=history,
        ):
            msg_type = msg.metadata.get("type", "")

            if msg_type == "token":
                full_response += msg.content
                data = json.dumps({
                    "type": "token", "content": msg.content,
                    "conversation_id": conv_id,
                }, ensure_ascii=False)
                yield f"data: {data}\n\n"

            elif msg_type == "tool_call":
                data = json.dumps({
                    "type": "tool_call", "tool_call": msg.tool_call,
                    "conversation_id": conv_id,
                }, ensure_ascii=False)
                yield f"data: {data}\n\n"

            elif msg_type == "tool_result":
                data = json.dumps({
                    "type": "tool_result", "tool_result": msg.tool_result,
                    "conversation_id": conv_id,
                }, ensure_ascii=False)
                yield f"data: {data}\n\n"

            elif msg_type == "done":
                final_msg = Message(role="assistant", content=full_response, metadata=msg.metadata)
                db.save_message(conv_id, final_msg)
                data = json.dumps({
                    "type": "done", "conversation_id": conv_id,
                    "metadata": msg.metadata,
                }, ensure_ascii=False)
                yield f"data: {data}\n\n"

            elif msg_type == "max_steps_reached":
                data = json.dumps({
                    "type": "error", "content": msg.content,
                    "conversation_id": conv_id,
                }, ensure_ascii=False)
                yield f"data: {data}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/conversations")
async def list_conversations():
    return db.list_conversations()


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    messages = db.get_messages(conv_id)
    return {**conv, "messages": [m.model_dump(mode="json") for m in messages]}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    with db._get_conn() as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    return {"status": "deleted"}


@app.get("/api/tools")
async def list_tools():
    tools = registry.list_tools()
    return [t.model_dump() for t in tools]


@app.get("/api/models")
async def list_models():
    return router.list_models()


@app.get("/api/stats")
async def get_stats():
    return db.get_stats()


@app.get("/api/tool-logs/{conv_id}")
async def get_tool_logs(conv_id: str):
    with db._get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tool_logs WHERE conversation_id = ? ORDER BY created_at ASC",
            (conv_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ==================== 新增 API: Apps ====================

@app.get("/api/apps")
async def list_apps():
    return app_manager.list_apps()


@app.post("/api/apps")
async def create_app_route(request: AppCreateRequest):
    app_data = app_manager.create_app(request.model_dump())
    return app_data


@app.get("/api/apps/{app_id}")
async def get_app_route(app_id: str):
    app_data = app_manager.get_app_with_context(app_id)
    if not app_data:
        raise HTTPException(404, "应用不存在")
    return app_data


@app.put("/api/apps/{app_id}")
async def update_app_route(app_id: str, request: AppUpdateRequest):
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    app_data = app_manager.update_app(app_id, updates)
    if not app_data:
        raise HTTPException(404, "应用不存在")
    return app_data


@app.delete("/api/apps/{app_id}")
async def delete_app_route(app_id: str):
    if not app_manager.delete_app(app_id):
        raise HTTPException(404, "应用不存在")
    return {"status": "deleted"}


@app.post("/api/apps/{app_id}/chat")
async def app_chat_stream(app_id: str, request: AppChatRequest):
    """应用聊天 — SSE 流式输出，集成知识库检索"""
    chat_config = app_manager.build_chat_config(app_id)
    if not chat_config:
        raise HTTPException(404, "应用不存在")

    app_data = chat_config["app"]
    model_config = chat_config["model_config"]
    system_prompt = chat_config["system_prompt"]
    datasets = chat_config["datasets"]

    conv_id = request.conversation_id or str(uuid.uuid4())
    history = []

    if request.conversation_id:
        conv = db.get_conversation(conv_id)
        if conv:
            history = db.get_messages(conv_id)
    else:
        title = await orchestrator.generate_title(request.message, model_config.get("model"))
        conv = Conversation(id=conv_id, title=title, model=model_config.get("model", "deepseek-chat"))
        db.create_conversation(conv)

    # RAG: 知识库检索
    context_text = ""
    if datasets:
        dataset_ids = [d["id"] for d in datasets]
        search_results = knowledge_manager.search(dataset_ids, request.message, top_k=3)
        if search_results:
            context_text = "\n\n## 知识库参考\n" + "\n\n---\n\n".join(
                r["content"] for r in search_results
            )

    # 构建系统提示
    full_system_prompt = system_prompt
    if context_text:
        full_system_prompt += f"\n\n请参考以下知识库内容回答用户问题:{context_text}"

    async def event_generator():
        full_response = ""
        model = model_config.get("model", "deepseek-chat")
        messages = []

        # 系统提示
        messages.append({"role": "system", "content": full_system_prompt})

        # 历史消息
        for msg in history[-20:]:
            if msg.role == "user":
                messages.append({"role": "user", "content": msg.content})
            elif msg.role == "assistant":
                messages.append({"role": "assistant", "content": msg.content})

        messages.append({"role": "user", "content": request.message})

        tool_schemas = registry.to_function_calls()
        max_steps = 5

        for step in range(max_steps):
            chunk_content = ""
            tool_calls_data = []
            usage_data = {}

            async for chunk in router.chat_stream(
                messages=messages,
                model=model,
                tools=tool_schemas if tool_schemas else None,
                temperature=model_config.get("temperature", 0.7),
                max_tokens=model_config.get("max_tokens", 4096),
            ):
                if chunk["type"] == "token":
                    chunk_content += chunk["content"]
                    full_response += chunk["content"]
                    data = json.dumps({
                        "type": "token", "content": chunk["content"],
                        "conversation_id": conv_id,
                    }, ensure_ascii=False)
                    yield f"data: {data}\n\n"
                elif chunk["type"] == "done":
                    response_data = chunk["response"]
                    tool_calls_data = response_data.get("tool_calls") or []
                    usage_data = response_data.get("usage", {})

            if tool_calls_data:
                yield f"data: {json.dumps({'type': 'tool_call', 'tool_call': tool_calls_data[0], 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"
                messages.append({"role": "assistant", "content": chunk_content, "tool_calls": tool_calls_data})

                for tc in tool_calls_data:
                    func = tc["function"]
                    tool_args = json.loads(func["arguments"])
                    result = await registry.execute(func["name"], tool_args)
                    result_data = {"tool_call_id": tc["id"], "name": func["name"], "result": result.model_dump()}
                    yield f"data: {json.dumps({'type': 'tool_result', 'tool_result': result_data, 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result.model_dump(), ensure_ascii=False, default=str)})
            else:
                final_msg = Message(role="assistant", content=full_response, metadata={"type": "done", "steps": step + 1, "model": model, "usage": usage_data})
                db.save_message(conv_id, final_msg)
                yield f"data: {json.dumps({'type': 'done', 'conversation_id': conv_id, 'metadata': final_msg.metadata}, ensure_ascii=False)}\n\n"
                break

        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/apps/{app_id}/workflow/run")
async def run_app_workflow(app_id: str, request: WorkflowRunRequest):
    """运行应用的工作流"""
    app_data = db.get_app(app_id)
    if not app_data:
        raise HTTPException(404, "应用不存在")

    workflow_config = app_data.get("workflow_config", {})
    if not workflow_config.get("nodes"):
        raise HTTPException(400, "应用未配置工作流")

    result = await workflow_engine.run_workflow(
        workflow_config=workflow_config,
        inputs=request.inputs,
        app_id=app_id,
        conversation_id=request.conversation_id,
    )
    return result


# ==================== 新增 API: Knowledge ====================

@app.get("/api/knowledge/datasets")
async def list_datasets():
    return knowledge_manager.list_datasets()


@app.post("/api/knowledge/datasets")
async def create_dataset(request: DatasetCreateRequest):
    return knowledge_manager.create_dataset(request.model_dump())


@app.get("/api/knowledge/datasets/{ds_id}")
async def get_dataset(ds_id: str):
    ds = knowledge_manager.get_dataset(ds_id)
    if not ds:
        raise HTTPException(404, "知识库不存在")
    return ds


@app.put("/api/knowledge/datasets/{ds_id}")
async def update_dataset(ds_id: str, request: DatasetUpdateRequest):
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    ds = knowledge_manager.update_dataset(ds_id, updates)
    if not ds:
        raise HTTPException(404, "知识库不存在")
    return ds


@app.delete("/api/knowledge/datasets/{ds_id}")
async def delete_dataset(ds_id: str):
    if not knowledge_manager.delete_dataset(ds_id):
        raise HTTPException(404, "知识库不存在")
    return {"status": "deleted"}


@app.get("/api/knowledge/datasets/{ds_id}/documents")
async def list_documents(ds_id: str):
    return knowledge_manager.list_documents(ds_id)


@app.post("/api/knowledge/datasets/{ds_id}/documents")
async def upload_document(ds_id: str, request: DocumentUploadRequest):
    try:
        doc = knowledge_manager.upload_document(
            dataset_id=ds_id,
            name=request.name,
            content=request.content,
            file_type=request.file_type,
        )
        return doc
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/knowledge/documents/{doc_id}")
async def get_document(doc_id: str):
    doc = knowledge_manager.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "文档不存在")
    return doc


@app.delete("/api/knowledge/documents/{doc_id}")
async def delete_document(doc_id: str):
    if not knowledge_manager.delete_document(doc_id):
        raise HTTPException(404, "文档不存在")
    return {"status": "deleted"}


@app.get("/api/knowledge/datasets/{ds_id}/segments")
async def list_segments(ds_id: str):
    return db.list_segments(dataset_id=ds_id)


@app.post("/api/knowledge/search")
async def search_knowledge(request: SearchRequest):
    results = knowledge_manager.search(request.dataset_ids, request.query, request.top_k)
    return {"results": results, "total": len(results)}


# ==================== 新增 API: Model Providers ====================

@app.get("/api/models/providers")
async def list_model_providers():
    return db.list_model_providers()


@app.post("/api/models/providers")
async def create_model_provider(request: ModelProviderCreateRequest):
    provider_id = str(uuid.uuid4())[:12]
    provider = {"id": provider_id, **request.model_dump()}
    db.create_model_provider(provider)
    return db.get_model_provider(provider_id)


@app.get("/api/models/providers/{provider_id}")
async def get_model_provider(provider_id: str):
    p = db.get_model_provider(provider_id)
    if not p:
        raise HTTPException(404, "提供商不存在")
    return p


@app.put("/api/models/providers/{provider_id}")
async def update_model_provider(provider_id: str, request: ModelProviderCreateRequest):
    updates = request.model_dump()
    if not db.update_model_provider(provider_id, updates):
        raise HTTPException(404, "提供商不存在")
    return db.get_model_provider(provider_id)


@app.delete("/api/models/providers/{provider_id}")
async def delete_model_provider(provider_id: str):
    if not db.delete_model_provider(provider_id):
        raise HTTPException(404, "提供商不存在")
    return {"status": "deleted"}


@app.post("/api/models/test")
async def test_model(request: ModelTestRequest):
    """测试模型连接"""
    try:
        # 构建临时路由器
        test_config = {
            "providers": {
                request.provider_type: {
                    "api_key": request.api_key,
                    "base_url": request.base_url or f"https://api.deepseek.com",
                    "models": [request.model],
                }
            }
        }
        test_router = ModelRouter(test_config)
        response = await test_router.chat(
            messages=[{"role": "user", "content": request.test_message}],
            model=request.model,
            max_tokens=100,
        )
        return {
            "status": "success",
            "model": response.model,
            "content": response.content,
            "usage": response.usage,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ==================== 新增 API: Tools (DB 级) ====================

@app.get("/api/tools/custom")
async def list_custom_tools():
    return db.list_tools_db()


@app.post("/api/tools/custom")
async def create_custom_tool(request: ToolCreateRequest):
    tool_id = str(uuid.uuid4())[:12]
    tool = {"id": tool_id, **request.model_dump()}
    db.create_tool_db(tool)
    return db.get_tool_db(tool_id)


@app.get("/api/tools/custom/{tool_id}")
async def get_custom_tool(tool_id: str):
    t = db.get_tool_db(tool_id)
    if not t:
        raise HTTPException(404, "工具不存在")
    return t


@app.put("/api/tools/custom/{tool_id}")
async def update_custom_tool(tool_id: str, request: ToolUpdateRequest):
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    if not db.update_tool_db(tool_id, updates):
        raise HTTPException(404, "工具不存在")
    return db.get_tool_db(tool_id)


@app.delete("/api/tools/custom/{tool_id}")
async def delete_custom_tool(tool_id: str):
    if not db.delete_tool_db(tool_id):
        raise HTTPException(404, "工具不存在")
    return {"status": "deleted"}


# ==================== 新增 API: Workflow Runs ====================

@app.get("/api/workflow/runs")
async def list_workflow_runs(app_id: str = Query(None)):
    return db.list_workflow_runs(app_id=app_id)


@app.get("/api/workflow/runs/{run_id}")
async def get_workflow_run(run_id: str):
    run = db.get_workflow_run(run_id)
    if not run:
        raise HTTPException(404, "运行记录不存在")
    return run


# ==================== Web UI ====================

# 加载 HTML 模板（必须在路由定义之前）
from pathlib import Path as _Path
_TPL = _Path(__file__).parent / "templates" / "app.html"
try:
    HTML_PAGE = _TPL.read_text(encoding="utf-8")
except Exception as _e:
    HTML_PAGE = f"<h1>Template Error: {_e}</h1>"

@app.get("/", response_class=HTMLResponse)
async def web_ui():
    return HTML_PAGE


def run_server(host: str = "0.0.0.0", port: int = None):
    import uvicorn
    from config import load_config
    cfg = load_config()
    _host = host or cfg.get("server", {}).get("host", "0.0.0.0")
    _port = port or cfg.get("server", {}).get("port", 10002)
    uvicorn.run(app, host=_host, port=_port, log_level="info")


if __name__ == "__main__":
    run_server()

