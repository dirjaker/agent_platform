"""FastAPI Web 服务 — 支持 Dify 风格的应用管理、知识库、工作流、流式 SSE"""

import json
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
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


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

@app.get("/", response_class=HTMLResponse)
async def web_ui():
    return HTML_PAGE


def run_server(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")


# ==================== 内嵌 HTML（Dify 风格） ====================

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Agent LLM 应用平台</title>
<script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
/* ========== CSS Variables & Themes ========== */
:root {
  --bg: #f5f5f5; --surface: #ffffff; --surface2: #fafafa; --border: #e5e5e5;
  --text: #1a1a1a; --text2: #666; --text3: #999;
  --primary: #1677ff; --primary-hover: #4096ff; --primary-bg: #e6f4ff;
  --success: #52c41a; --error: #ff4d4f; --warning: #faad14;
  --radius: 8px; --shadow: 0 2px 8px rgba(0,0,0,0.06);
  --sidebar-w: 240px; --header-h: 56px;
}
[data-theme="dark"] {
  --bg: #0d0d0d; --surface: #1a1a1a; --surface2: #222; --border: #333;
  --text: #e0e0e0; --text2: #888; --text3: #666;
  --primary: #4a9eff; --primary-hover: #6bb3ff; --primary-bg: #1a2a3a;
  --success: #4caf50; --error: #f44336; --warning: #ff9800;
  --shadow: 0 2px 8px rgba(0,0,0,0.3);
}
[data-theme="warm"] {
  --bg: #faf6f1; --surface: #fffdf8; --surface2: #f8f4ee; --border: #e8ddd0;
  --text: #3d3428; --text2: #8a7b68; --text3: #b5a898;
  --primary: #c0762e; --primary-hover: #d4893e; --primary-bg: #fdf3e8;
  --success: #6b8e3a; --error: #c04020; --warning: #d4a020;
}
[data-theme="mint"] {
  --bg: #f0f7f4; --surface: #f8fcfa; --surface2: #eef5f1; --border: #d0e8dc;
  --text: #1a3a2a; --text2: #4a7a60; --text3: #7aaa90;
  --primary: #2d8a5e; --primary-hover: #3da070; --primary-bg: #e0f5ea;
  --success: #3a9a5a; --error: #c04040; --warning: #c0a030;
}
[data-theme="violet"] {
  --bg: #f5f0fa; --surface: #faf7fd; --surface2: #f0eaf6; --border: #ddd0ee;
  --text: #2a1a3d; --text2: #6a50a0; --text3: #9a88c0;
  --primary: #7c3aed; --primary-hover: #8b5cf6; --primary-bg: #ede9fe;
  --success: #52c41a; --error: #ef4444; --warning: #f59e0b;
}

* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); height: 100vh; overflow: hidden; }

/* Layout */
.app-layout { display: flex; height: 100vh; }
.sidebar { width: var(--sidebar-w); background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; flex-shrink: 0; }
.main-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.page-header { height: var(--header-h); padding: 0 24px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--border); background: var(--surface); flex-shrink: 0; }
.page-content { flex: 1; overflow-y: auto; padding: 24px; }

/* Sidebar */
.sidebar-logo { padding: 16px 20px; font-size: 18px; font-weight: 700; color: var(--primary); border-bottom: 1px solid var(--border); }
.sidebar-nav { flex: 1; padding: 12px 8px; overflow-y: auto; }
.nav-item { padding: 10px 16px; border-radius: var(--radius); cursor: pointer; font-size: 14px; color: var(--text2); transition: all 0.2s; display: flex; align-items: center; gap: 10px; margin-bottom: 2px; }
.nav-item:hover { background: var(--primary-bg); color: var(--primary); }
.nav-item.active { background: var(--primary-bg); color: var(--primary); font-weight: 600; }
.sidebar-footer { padding: 12px 16px; border-top: 1px solid var(--border); font-size: 12px; color: var(--text3); }

/* Cards */
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; cursor: pointer; transition: all 0.2s; }
.card:hover { box-shadow: var(--shadow); border-color: var(--primary); }
.card-title { font-size: 16px; font-weight: 600; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
.card-desc { font-size: 13px; color: var(--text2); line-height: 1.5; }
.card-meta { margin-top: 12px; font-size: 12px; color: var(--text3); display: flex; gap: 16px; }

/* Buttons */
.btn { padding: 8px 16px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--surface); color: var(--text); cursor: pointer; font-size: 13px; transition: all 0.15s; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover { border-color: var(--primary); color: var(--primary); }
.btn-primary { background: var(--primary); color: #fff; border-color: var(--primary); }
.btn-primary:hover { background: var(--primary-hover); border-color: var(--primary-hover); color: #fff; }
.btn-danger { color: var(--error); border-color: var(--error); }
.btn-danger:hover { background: var(--error); color: #fff; }
.btn-sm { padding: 4px 10px; font-size: 12px; }

/* Inputs */
.input { padding: 8px 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); color: var(--text); font-size: 14px; outline: none; width: 100%; transition: border-color 0.2s; }
.input:focus { border-color: var(--primary); }
.textarea { min-height: 80px; resize: vertical; }

/* Chat */
.chat-container { display: flex; flex-direction: column; height: 100%; }
.chat-messages { flex: 1; overflow-y: auto; padding: 20px; }
.chat-msg { margin-bottom: 16px; display: flex; gap: 12px; max-width: 800px; animation: fadeIn 0.3s ease; }
.chat-msg.user { margin-left: auto; flex-direction: row-reverse; }
.chat-msg .avatar { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 18px; flex-shrink: 0; }
.chat-msg.user .avatar { background: var(--primary); }
.chat-msg.assistant .avatar { background: var(--success); }
.chat-msg .bubble { padding: 12px 16px; border-radius: 12px; font-size: 14px; line-height: 1.7; white-space: pre-wrap; word-break: break-word; max-width: 600px; }
.chat-msg.user .bubble { background: var(--primary); color: #fff; border-bottom-right-radius: 4px; }
.chat-msg.assistant .bubble { background: var(--surface2); border: 1px solid var(--border); border-bottom-left-radius: 4px; }
.chat-input-area { padding: 16px 20px; border-top: 1px solid var(--border); background: var(--surface); }
.chat-input-row { display: flex; gap: 10px; max-width: 800px; margin: 0 auto; }
.chat-input-row textarea { flex: 1; }
.cursor { display: inline-block; width: 2px; height: 14px; background: var(--primary); animation: blink 1s infinite; vertical-align: middle; margin-left: 2px; }
@keyframes blink { 0%,50% { opacity: 1; } 51%,100% { opacity: 0; } }
@keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }

/* Markdown in chat */
.bubble code { background: var(--border); padding: 2px 6px; border-radius: 4px; font-size: 13px; }
.bubble pre { background: var(--surface); padding: 12px; border-radius: 8px; overflow-x: auto; margin: 8px 0; border: 1px solid var(--border); }
.bubble pre code { background: none; padding: 0; }
.bubble p { margin: 6px 0; }

/* Modal */
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; z-index: 1000; }
.modal { background: var(--surface); border-radius: 12px; padding: 24px; width: 520px; max-height: 80vh; overflow-y: auto; box-shadow: 0 8px 32px rgba(0,0,0,0.15); }
.modal h3 { margin-bottom: 16px; font-size: 18px; }
.form-group { margin-bottom: 16px; }
.form-group label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px; color: var(--text2); }

/* Workflow canvas */
.workflow-canvas { width: 100%; height: calc(100vh - var(--header-h) - 48px); border: 1px solid var(--border); border-radius: var(--radius); background: var(--bg); overflow: hidden; position: relative; }
.wf-node { cursor: move; }
.wf-node rect { fill: var(--surface); stroke: var(--primary); stroke-width: 2; rx: 8; }
.wf-node text { fill: var(--text); font-size: 12px; }
.wf-link { fill: none; stroke: var(--primary); stroke-width: 2; marker-end: url(#arrowhead); }

/* Theme selector */
.theme-switcher { display: flex; gap: 6px; }
.theme-dot { width: 20px; height: 20px; border-radius: 50%; cursor: pointer; border: 2px solid transparent; transition: all 0.15s; }
.theme-dot.active { border-color: var(--primary); transform: scale(1.15); }
.theme-dot[data-t="default"] { background: #1677ff; }
.theme-dot[data-t="dark"] { background: #1a1a1a; border-color: #555; }
.theme-dot[data-t="warm"] { background: #c0762e; }
.theme-dot[data-t="mint"] { background: #2d8a5e; }
.theme-dot[data-t="violet"] { background: #7c3aed; }

/* Empty state */
.empty-state { text-align: center; padding: 60px 20px; color: var(--text3); }
.empty-state .icon { font-size: 48px; margin-bottom: 16px; }
.empty-state h3 { font-size: 18px; color: var(--text2); margin-bottom: 8px; }

/* Tabs */
.tabs { display: flex; gap: 0; border-bottom: 1px solid var(--border); margin-bottom: 20px; }
.tab { padding: 10px 20px; cursor: pointer; font-size: 14px; color: var(--text2); border-bottom: 2px solid transparent; transition: all 0.2s; }
.tab:hover { color: var(--primary); }
.tab.active { color: var(--primary); border-bottom-color: var(--primary); font-weight: 600; }

/* Tag */
.tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; background: var(--primary-bg); color: var(--primary); }

/* Table */
.table { width: 100%; border-collapse: collapse; }
.table th, .table td { padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--border); font-size: 13px; }
.table th { background: var(--surface2); font-weight: 600; color: var(--text2); font-size: 12px; }
.table tr:hover td { background: var(--surface2); }
</style>
</head>
<body>
<div id="app">
  <div class="app-layout" :data-theme="theme">
    <!-- Sidebar -->
    <div class="sidebar">
      <div class="sidebar-logo">🤖 Agent 平台</div>
      <div class="sidebar-nav">
        <div class="nav-item" :class="{active: page==='apps'}" @click="page='apps'">📱 应用</div>
        <div class="nav-item" :class="{active: page==='knowledge'}" @click="page='knowledge'">📚 知识库</div>
        <div class="nav-item" :class="{active: page==='tools'}" @click="page='tools'">🔧 工具</div>
        <div class="nav-item" :class="{active: page==='models'}" @click="page='models'">🧠 模型</div>
        <div class="nav-item" :class="{active: page==='settings'}" @click="page='settings'">⚙️ 设置</div>
      </div>
      <div class="sidebar-footer">
        <div class="theme-switcher">
          <div v-for="t in themes" :key="t" class="theme-dot" :data-t="t"
               :class="{active: theme===t}" @click="setTheme(t)"></div>
        </div>
      </div>
    </div>

    <!-- Main -->
    <div class="main-area">
      <!-- Apps Page -->
      <template v-if="page==='apps' && !selectedApp">
        <div class="page-header">
          <h2>应用管理</h2>
          <button class="btn btn-primary" @click="showCreateApp=true">+ 创建应用</button>
        </div>
        <div class="page-content">
          <div v-if="apps.length===0" class="empty-state">
            <div class="icon">📱</div>
            <h3>还没有应用</h3>
            <p>创建你的第一个 LLM 应用</p>
          </div>
          <div class="card-grid" v-else>
            <div class="card" v-for="a in apps" :key="a.id" @click="openApp(a)">
              <div class="card-title">{{a.icon}} {{a.name}} <span class="tag">{{a.app_type}}</span></div>
              <div class="card-desc">{{a.description || '暂无描述'}}</div>
              <div class="card-meta">
                <span>模型: {{a.model_config?.model || 'default'}}</span>
                <span>{{formatDate(a.created_at)}}</span>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- App Detail -->
      <template v-if="page==='apps' && selectedApp">
        <div class="page-header">
          <div style="display:flex;align-items:center;gap:12px">
            <button class="btn btn-sm" @click="selectedApp=null">← 返回</button>
            <h2>{{selectedApp.icon}} {{selectedApp.name}}</h2>
          </div>
          <div style="display:flex;gap:8px">
            <button class="btn btn-primary" @click="openAppChat(selectedApp)">💬 对话</button>
            <button class="btn" @click="showEditApp=true">✏️ 编辑</button>
            <button class="btn btn-danger" @click="deleteApp(selectedApp.id)">🗑️ 删除</button>
          </div>
        </div>
        <div class="page-content">
          <div class="tabs">
            <div class="tab" :class="{active: appTab==='overview'}" @click="appTab='overview'">概览</div>
            <div class="tab" :class="{active: appTab==='workflow'}" @click="appTab='workflow'">工作流</div>
            <div class="tab" :class="{active: appTab==='datasets'}" @click="appTab='datasets'">知识库</div>
          </div>
          <div v-if="appTab==='overview'">
            <p style="color:var(--text2);margin-bottom:16px">{{selectedApp.description || '暂无描述'}}</p>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
              <div><strong>类型:</strong> {{selectedApp.app_type}}</div>
              <div><strong>模型:</strong> {{selectedApp.model_config?.model || 'default'}}</div>
              <div><strong>温度:</strong> {{selectedApp.model_config?.temperature || 0.7}}</div>
              <div><strong>最大Token:</strong> {{selectedApp.model_config?.max_tokens || 4096}}</div>
            </div>
            <div style="margin-top:16px">
              <strong>系统提示:</strong>
              <pre style="background:var(--surface2);padding:12px;border-radius:8px;margin-top:8px;white-space:pre-wrap;font-size:13px">{{selectedApp.system_prompt}}</pre>
            </div>
          </div>
          <div v-if="appTab==='workflow'">
            <div style="margin-bottom:12px">
              <button class="btn btn-primary btn-sm" @click="runWorkflow">▶️ 运行工作流</button>
            </div>
            <div class="workflow-canvas" ref="wfCanvas">
              <svg width="100%" height="100%">
                <defs>
                  <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
                    <polygon points="0 0, 10 3.5, 0 7" fill="var(--primary)"/>
                  </marker>
                </defs>
                <g ref="wfGroup"></g>
              </svg>
            </div>
          </div>
          <div v-if="appTab==='datasets'">
            <p style="color:var(--text2)">关联知识库: {{selectedApp.datasets?.length || 0}} 个</p>
          </div>
        </div>
      </template>

      <!-- Chat Playground -->
      <template v-if="page==='chat'">
        <div class="page-header">
          <div style="display:flex;align-items:center;gap:12px">
            <button class="btn btn-sm" @click="page='apps'">← 返回</button>
            <h2>{{chatApp?.icon}} {{chatApp?.name}} - 对话</h2>
          </div>
          <button class="btn btn-sm" @click="clearChat">清空</button>
        </div>
        <div class="chat-container">
          <div class="chat-messages" ref="chatMsgs">
            <div v-for="m in chatMessages" :key="m.id" class="chat-msg" :class="m.role">
              <div class="avatar">{{m.role==='user'?'👤':'🤖'}}</div>
              <div class="bubble" v-html="renderMd(m.content)"></div>
            </div>
            <div v-if="chatMessages.length===0" class="empty-state">
              <div class="icon">💬</div>
              <h3>开始对话</h3>
            </div>
          </div>
          <div class="chat-input-area">
            <div class="chat-input-row">
              <textarea class="input textarea" v-model="chatInput" placeholder="输入消息... (Enter 发送)"
                        @keydown.enter.exact.prevent="sendChat" rows="1" style="min-height:42px;max-height:120px"></textarea>
              <button class="btn btn-primary" @click="sendChat" :disabled="chatSending">
                {{chatSending ? '发送中...' : '发送'}}
              </button>
            </div>
          </div>
        </div>
      </template>

      <!-- Knowledge Page -->
      <template v-if="page==='knowledge'">
        <div class="page-header">
          <h2>知识库管理</h2>
          <button class="btn btn-primary" @click="showCreateDataset=true">+ 创建知识库</button>
        </div>
        <div class="page-content">
          <div v-if="datasets.length===0" class="empty-state">
            <div class="icon">📚</div>
            <h3>还没有知识库</h3>
            <p>创建知识库并上传文档</p>
          </div>
          <div class="card-grid" v-else>
            <div class="card" v-for="d in datasets" :key="d.id" @click="openDataset(d)">
              <div class="card-title">📄 {{d.name}}</div>
              <div class="card-desc">{{d.description || '暂无描述'}}</div>
              <div class="card-meta">
                <span>文档: {{d.document_count}}</span>
                <span>片段: {{d.segment_count}}</span>
                <span>{{formatDate(d.created_at)}}</span>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- Dataset Detail -->
      <template v-if="page==='dataset-detail'">
        <div class="page-header">
          <div style="display:flex;align-items:center;gap:12px">
            <button class="btn btn-sm" @click="page='knowledge'">← 返回</button>
            <h2>📄 {{selectedDataset?.name}}</h2>
          </div>
          <div style="display:flex;gap:8px">
            <button class="btn btn-primary" @click="showUploadDoc=true">📄 上传文档</button>
            <button class="btn btn-danger" @click="deleteDataset(selectedDataset.id)">🗑️ 删除</button>
          </div>
        </div>
        <div class="page-content">
          <div class="tabs">
            <div class="tab" :class="{active: dsTab==='docs'}" @click="dsTab='docs'">文档</div>
            <div class="tab" :class="{active: dsTab==='segments'}" @click="dsTab='segments'">片段</div>
            <div class="tab" :class="{active: dsTab==='search'}" @click="dsTab='search'">搜索测试</div>
          </div>
          <div v-if="dsTab==='docs'">
            <table class="table" v-if="documents.length">
              <thead><tr><th>名称</th><th>类型</th><th>片段数</th><th>状态</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="doc in documents" :key="doc.id">
                  <td>{{doc.name}}</td>
                  <td>{{doc.file_type}}</td>
                  <td>{{doc.segment_count}}</td>
                  <td><span class="tag" :style="{background: doc.status==='completed'?'var(--primary-bg)':'#fff3e0',color: doc.status==='completed'?'var(--primary)':'var(--warning)'}">{{doc.status}}</span></td>
                  <td><button class="btn btn-sm btn-danger" @click="deleteDocument(doc.id)">删除</button></td>
                </tr>
              </tbody>
            </table>
            <div v-else class="empty-state"><div class="icon">📄</div><h3>暂无文档</h3></div>
          </div>
          <div v-if="dsTab==='search'">
            <div style="display:flex;gap:10px;margin-bottom:16px">
              <input class="input" v-model="searchQuery" placeholder="输入搜索内容..." style="flex:1">
              <button class="btn btn-primary" @click="searchKnowledge">搜索</button>
            </div>
            <div v-for="r in searchResults" :key="r.segment_id" class="card" style="margin-bottom:8px;cursor:default">
              <div style="display:flex;justify-content:space-between;margin-bottom:8px">
                <span class="tag">相似度: {{r.score}}</span>
                <span style="font-size:12px;color:var(--text3)">{{r.document_id}}</span>
              </div>
              <div style="font-size:13px;line-height:1.6;white-space:pre-wrap">{{r.content}}</div>
            </div>
          </div>
        </div>
      </template>

      <!-- Tools Page -->
      <template v-if="page==='tools'">
        <div class="page-header">
          <h2>工具管理</h2>
          <button class="btn btn-primary" @click="showCreateTool=true">+ 创建工具</button>
        </div>
        <div class="page-content">
          <h3 style="margin-bottom:12px;font-size:14px;color:var(--text2)">内置工具</h3>
          <table class="table" v-if="builtinTools.length">
            <thead><tr><th>名称</th><th>描述</th><th>分类</th></tr></thead>
            <tbody>
              <tr v-for="t in builtinTools" :key="t.name">
                <td><strong>{{t.name}}</strong></td><td>{{t.description}}</td><td><span class="tag">{{t.category}}</span></td>
              </tr>
            </tbody>
          </table>
          <h3 style="margin:20px 0 12px;font-size:14px;color:var(--text2)">自定义工具</h3>
          <table class="table" v-if="customTools.length">
            <thead><tr><th>名称</th><th>描述</th><th>类型</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="t in customTools" :key="t.id">
                <td><strong>{{t.name}}</strong></td><td>{{t.description}}</td><td><span class="tag">{{t.tool_type}}</span></td>
                <td><button class="btn btn-sm btn-danger" @click="deleteCustomTool(t.id)">删除</button></td>
              </tr>
            </tbody>
          </table>
          <div v-if="!customTools.length" class="empty-state" style="padding:30px"><p>暂无自定义工具</p></div>
        </div>
      </template>

      <!-- Models Page -->
      <template v-if="page==='models'">
        <div class="page-header">
          <h2>模型管理</h2>
          <div style="display:flex;gap:8px">
            <button class="btn btn-primary" @click="showCreateProvider=true">+ 添加提供商</button>
            <button class="btn" @click="showTestModel=true">🧪 测试模型</button>
          </div>
        </div>
        <div class="page-content">
          <table class="table" v-if="modelProviders.length">
            <thead><tr><th>名称</th><th>类型</th><th>模型</th><th>状态</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="p in modelProviders" :key="p.id">
                <td><strong>{{p.name}}</strong></td>
                <td>{{p.provider_type}}</td>
                <td>{{(p.models||[]).join(', ')}}</td>
                <td><span class="tag">{{p.enabled?'启用':'禁用'}}</span></td>
                <td><button class="btn btn-sm btn-danger" @click="deleteProvider(p.id)">删除</button></td>
              </tr>
            </tbody>
          </table>
          <div v-else class="empty-state"><div class="icon">🧠</div><h3>暂无模型提供商</h3></div>
        </div>
      </template>

      <!-- Settings Page -->
      <template v-if="page==='settings'">
        <div class="page-header"><h2>设置</h2></div>
        <div class="page-content">
          <div style="max-width:600px">
            <h3 style="margin-bottom:16px">主题设置</h3>
            <div class="form-group">
              <label>选择主题</label>
              <div style="display:flex;gap:12px;margin-top:8px">
                <div v-for="t in themes" :key="t" @click="setTheme(t)"
                     style="padding:12px 20px;border-radius:8px;cursor:pointer;border:2px solid var(--border)"
                     :style="{'border-color': theme===t?'var(--primary)':'var(--border)'}">
                  {{t}}
                </div>
              </div>
            </div>
            <h3 style="margin:24px 0 16px">平台统计</h3>
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px">
              <div class="card" style="cursor:default;text-align:center">
                <div style="font-size:24px;font-weight:700;color:var(--primary)">{{stats.apps||0}}</div>
                <div style="font-size:12px;color:var(--text2)">应用</div>
              </div>
              <div class="card" style="cursor:default;text-align:center">
                <div style="font-size:24px;font-weight:700;color:var(--primary)">{{stats.datasets||0}}</div>
                <div style="font-size:12px;color:var(--text2)">知识库</div>
              </div>
              <div class="card" style="cursor:default;text-align:center">
                <div style="font-size:24px;font-weight:700;color:var(--primary)">{{stats.conversations||0}}</div>
                <div style="font-size:12px;color:var(--text2)">对话</div>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>

  <!-- Create App Modal -->
  <div class="modal-overlay" v-if="showCreateApp" @click.self="showCreateApp=false">
    <div class="modal">
      <h3>创建应用</h3>
      <div class="form-group"><label>名称</label><input class="input" v-model="newApp.name" placeholder="应用名称"></div>
      <div class="form-group"><label>描述</label><textarea class="input textarea" v-model="newApp.description" placeholder="应用描述"></textarea></div>
      <div class="form-group"><label>类型</label>
        <select class="input" v-model="newApp.app_type"><option value="chat">对话</option><option value="workflow">工作流</option></select>
      </div>
      <div class="form-group"><label>模型</label><input class="input" v-model="newApp.model_config.model" placeholder="deepseek-chat"></div>
      <div class="form-group"><label>系统提示</label><textarea class="input textarea" v-model="newApp.system_prompt" placeholder="系统提示词..." rows="4"></textarea></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:20px">
        <button class="btn" @click="showCreateApp=false">取消</button>
        <button class="btn btn-primary" @click="createApp">创建</button>
      </div>
    </div>
  </div>

  <!-- Edit App Modal -->
  <div class="modal-overlay" v-if="showEditApp" @click.self="showEditApp=false">
    <div class="modal">
      <h3>编辑应用</h3>
      <div class="form-group"><label>名称</label><input class="input" v-model="editAppData.name"></div>
      <div class="form-group"><label>描述</label><textarea class="input textarea" v-model="editAppData.description"></textarea></div>
      <div class="form-group"><label>系统提示</label><textarea class="input textarea" v-model="editAppData.system_prompt" rows="4"></textarea></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:20px">
        <button class="btn" @click="showEditApp=false">取消</button>
        <button class="btn btn-primary" @click="updateApp">保存</button>
      </div>
    </div>
  </div>

  <!-- Create Dataset Modal -->
  <div class="modal-overlay" v-if="showCreateDataset" @click.self="showCreateDataset=false">
    <div class="modal">
      <h3>创建知识库</h3>
      <div class="form-group"><label>名称</label><input class="input" v-model="newDataset.name" placeholder="知识库名称"></div>
      <div class="form-group"><label>描述</label><textarea class="input textarea" v-model="newDataset.description" placeholder="知识库描述"></textarea></div>
      <div class="form-group"><label>分块大小</label><input class="input" type="number" v-model.number="newDataset.chunk_size"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:20px">
        <button class="btn" @click="showCreateDataset=false">取消</button>
        <button class="btn btn-primary" @click="createDataset">创建</button>
      </div>
    </div>
  </div>

  <!-- Upload Document Modal -->
  <div class="modal-overlay" v-if="showUploadDoc" @click.self="showUploadDoc=false">
    <div class="modal">
      <h3>上传文档</h3>
      <div class="form-group"><label>文档名称</label><input class="input" v-model="newDoc.name" placeholder="文档名称"></div>
      <div class="form-group"><label>内容</label><textarea class="input textarea" v-model="newDoc.content" placeholder="粘贴文档内容..." rows="8"></textarea></div>
      <div class="form-group"><label>类型</label><input class="input" v-model="newDoc.file_type" placeholder="txt"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:20px">
        <button class="btn" @click="showUploadDoc=false">取消</button>
        <button class="btn btn-primary" @click="uploadDocument">上传</button>
      </div>
    </div>
  </div>

  <!-- Create Provider Modal -->
  <div class="modal-overlay" v-if="showCreateProvider" @click.self="showCreateProvider=false">
    <div class="modal">
      <h3>添加模型提供商</h3>
      <div class="form-group"><label>名称</label><input class="input" v-model="newProvider.name" placeholder="如: DeepSeek"></div>
      <div class="form-group"><label>类型</label>
        <select class="input" v-model="newProvider.provider_type"><option value="deepseek">DeepSeek</option><option value="openai">OpenAI</option><option value="openrouter">OpenRouter</option><option value="local">本地模型</option></select>
      </div>
      <div class="form-group"><label>API Key</label><input class="input" v-model="newProvider.api_key" type="password" placeholder="sk-..."></div>
      <div class="form-group"><label>Base URL</label><input class="input" v-model="newProvider.base_url" placeholder="https://api.deepseek.com"></div>
      <div class="form-group"><label>模型列表（逗号分隔）</label><input class="input" v-model="newProvider.modelsStr" placeholder="deepseek-chat,deepseek-v4-flash"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:20px">
        <button class="btn" @click="showCreateProvider=false">取消</button>
        <button class="btn btn-primary" @click="createProvider">添加</button>
      </div>
    </div>
  </div>

  <!-- Test Model Modal -->
  <div class="modal-overlay" v-if="showTestModel" @click.self="showTestModel=false">
    <div class="modal">
      <h3>测试模型</h3>
      <div class="form-group"><label>提供商类型</label>
        <select class="input" v-model="testModel.provider_type"><option value="deepseek">DeepSeek</option><option value="openai">OpenAI</option><option value="openrouter">OpenRouter</option></select>
      </div>
      <div class="form-group"><label>模型</label><input class="input" v-model="testModel.model" placeholder="deepseek-chat"></div>
      <div class="form-group"><label>API Key</label><input class="input" v-model="testModel.api_key" type="password"></div>
      <div class="form-group"><label>Base URL</label><input class="input" v-model="testModel.base_url"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:20px">
        <button class="btn" @click="showTestModel=false">关闭</button>
        <button class="btn btn-primary" @click="testModelFn" :disabled="testModel.testing">
          {{testModel.testing?'测试中...':'测试'}}
        </button>
      </div>
      <div v-if="testModel.result" style="margin-top:16px;padding:12px;background:var(--surface2);border-radius:8px;font-size:13px">
        <pre style="white-space:pre-wrap">{{JSON.stringify(testModel.result, null, 2)}}</pre>
      </div>
    </div>
  </div>

  <!-- Create Tool Modal -->
  <div class="modal-overlay" v-if="showCreateTool" @click.self="showCreateTool=false">
    <div class="modal">
      <h3>创建工具</h3>
      <div class="form-group"><label>名称</label><input class="input" v-model="newTool.name" placeholder="工具名称"></div>
      <div class="form-group"><label>描述</label><input class="input" v-model="newTool.description" placeholder="工具描述"></div>
      <div class="form-group"><label>类型</label>
        <select class="input" v-model="newTool.tool_type"><option value="api">API</option><option value="code">代码</option></select>
      </div>
      <div class="form-group" v-if="newTool.tool_type==='code'"><label>代码</label><textarea class="input textarea" v-model="newTool.code" rows="6" placeholder="Python 代码..."></textarea></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:20px">
        <button class="btn" @click="showCreateTool=false">取消</button>
        <button class="btn btn-primary" @click="createTool">创建</button>
      </div>
    </div>
  </div>
</div>

<script>
const { createApp, ref, reactive, onMounted, nextTick, watch } = Vue;

createApp({
  setup() {
    const page = ref('apps');
    const theme = ref(localStorage.getItem('theme') || 'default');
    const themes = ['default', 'dark', 'warm', 'mint', 'violet'];
    const stats = ref({});

    // Apps
    const apps = ref([]);
    const selectedApp = ref(null);
    const appTab = ref('overview');
    const showCreateApp = ref(false);
    const showEditApp = ref(false);
    const newApp = reactive({name:'',description:'',app_type:'chat',model_config:{model:'deepseek-chat',temperature:0.7,max_tokens:4096},system_prompt:'你是一个智能助手。'});
    const editAppData = reactive({});

    // Chat
    const chatApp = ref(null);
    const chatMessages = ref([]);
    const chatInput = ref('');
    const chatSending = ref(false);
    const chatConvId = ref(null);

    // Knowledge
    const datasets = ref([]);
    const selectedDataset = ref(null);
    const dsTab = ref('docs');
    const showCreateDataset = ref(false);
    const newDataset = reactive({name:'',description:'',chunk_size:500,chunk_overlap:50});
    const documents = ref([]);
    const showUploadDoc = ref(false);
    const newDoc = reactive({name:'',content:'',file_type:'txt'});
    const searchQuery = ref('');
    const searchResults = ref([]);

    // Tools
    const builtinTools = ref([]);
    const customTools = ref([]);
    const showCreateTool = ref(false);
    const newTool = reactive({name:'',description:'',tool_type:'api',code:'',parameters:[]});

    // Models
    const modelProviders = ref([]);
    const showCreateProvider = ref(false);
    const newProvider = reactive({name:'',provider_type:'deepseek',api_key:'',base_url:'',modelsStr:''});
    const showTestModel = ref(false);
    const testModel = reactive({provider_type:'deepseek',model:'deepseek-chat',api_key:'',base_url:'',testing:false,result:null});

    // Refs
    const chatMsgs = ref(null);
    const wfCanvas = ref(null);
    const wfGroup = ref(null);

    // Theme
    function setTheme(t) { theme.value = t; localStorage.setItem('theme', t); document.documentElement.setAttribute('data-theme', t); }

    function formatDate(d) { if(!d) return ''; return new Date(d).toLocaleDateString('zh-CN'); }
    function renderMd(s) { try { return marked.parse(s||''); } catch(e) { return s; } }

    // API helpers
    async function api(path, opts={}) {
      const res = await fetch('/api/' + path, {headers:{'Content-Type':'application/json'}, ...opts});
      return res.json();
    }

    // Apps
    async function loadApps() { apps.value = await api('apps'); }
    async function createApp() {
      await api('apps', {method:'POST',body:JSON.stringify(newApp)});
      showCreateApp.value = false;
      await loadApps();
    }
    async function openApp(a) {
      selectedApp.value = a;
      appTab.value = 'overview';
      // Reload with datasets
      const full = await api('apps/' + a.id);
      selectedApp.value = full;
    }
    async function updateApp() {
      await api('apps/' + selectedApp.value.id, {method:'PUT',body:JSON.stringify(editAppData)});
      showEditApp.value = false;
      await openApp(selectedApp.value);
    }
    async function deleteApp(id) {
      if(!confirm('确定删除?')) return;
      await api('apps/'+id, {method:'DELETE'});
      selectedApp.value = null;
      await loadApps();
    }
    function openAppChat(a) {
      chatApp.value = a;
      chatMessages.value = [];
      chatConvId.value = null;
      page.value = 'chat';
    }
    async function sendChat() {
      if(!chatInput.value.trim() || chatSending.value) return;
      const msg = chatInput.value.trim();
      chatInput.value = '';
      chatMessages.value.push({id:Date.now(),role:'user',content:msg});
      chatSending.value = true;
      await nextTick(); scrollChat();

      try {
        const resp = await fetch('/api/apps/' + chatApp.value.id + '/chat', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({message:msg, conversation_id:chatConvId.value})
        });
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '', assistantContent = '', assistantMsg = {id:Date.now()+1,role:'assistant',content:''};
        chatMessages.value.push(assistantMsg);

        while(true) {
          const {done,value} = await reader.read();
          if(done) break;
          buffer += decoder.decode(value,{stream:true});
          const lines = buffer.split('\n');
          buffer = lines.pop()||'';
          for(const line of lines) {
            if(!line.startsWith('data: ')) continue;
            const ds = line.slice(6).trim();
            if(ds==='[DONE]') continue;
            try {
              const data = JSON.parse(ds);
              if(data.type==='token') {
                assistantContent += data.content;
                assistantMsg.content = assistantContent;
                await nextTick(); scrollChat();
              }
              if(data.type==='done') {
                chatConvId.value = data.conversation_id;
              }
            } catch(e) {}
          }
        }
      } catch(e) {
        chatMessages.value.push({id:Date.now()+2,role:'assistant',content:'错误: '+e.message});
      }
      chatSending.value = false;
      await nextTick(); scrollChat();
    }
    function clearChat() { chatMessages.value = []; chatConvId.value = null; }
    function scrollChat() { if(chatMsgs.value) chatMsgs.value.scrollTop = chatMsgs.value.scrollHeight; }

    // Datasets
    async function loadDatasets() { datasets.value = await api('knowledge/datasets'); }
    async function createDataset() {
      await api('knowledge/datasets', {method:'POST',body:JSON.stringify(newDataset)});
      showCreateDataset.value = false;
      await loadDatasets();
    }
    async function openDataset(d) {
      selectedDataset.value = d;
      dsTab.value = 'docs';
      page.value = 'dataset-detail';
      documents.value = await api('knowledge/datasets/'+d.id+'/documents');
    }
    async function deleteDataset(id) {
      if(!confirm('确定删除?')) return;
      await api('knowledge/datasets/'+id, {method:'DELETE'});
      page.value = 'knowledge';
      await loadDatasets();
    }
    async function uploadDocument() {
      await api('knowledge/datasets/'+selectedDataset.value.id+'/documents', {
        method:'POST', body:JSON.stringify(newDoc)
      });
      showUploadDoc.value = false;
      newDoc.name = ''; newDoc.content = '';
      documents.value = await api('knowledge/datasets/'+selectedDataset.value.id+'/documents');
    }
    async function deleteDocument(id) {
      await api('knowledge/documents/'+id, {method:'DELETE'});
      documents.value = await api('knowledge/datasets/'+selectedDataset.value.id+'/documents');
    }
    async function searchKnowledge() {
      if(!searchQuery.value.trim()) return;
      const res = await api('knowledge/search', {
        method:'POST',
        body:JSON.stringify({dataset_ids:[selectedDataset.value.id], query:searchQuery.value, top_k:5})
      });
      searchResults.value = res.results || [];
    }

    // Tools
    async function loadTools() {
      builtinTools.value = await api('tools');
      customTools.value = await api('tools/custom');
    }
    async function createTool() {
      await api('tools/custom', {method:'POST',body:JSON.stringify(newTool)});
      showCreateTool.value = false;
      await loadTools();
    }
    async function deleteCustomTool(id) {
      await api('tools/custom/'+id, {method:'DELETE'});
      await loadTools();
    }

    // Models
    async function loadProviders() { modelProviders.value = await api('models/providers'); }
    async function createProvider() {
      const data = {...newProvider, models: newProvider.modelsStr.split(',').map(s=>s.trim()).filter(Boolean)};
      delete data.modelsStr;
      await api('models/providers', {method:'POST',body:JSON.stringify(data)});
      showCreateProvider.value = false;
      await loadProviders();
    }
    async function deleteProvider(id) {
      await api('models/providers/'+id, {method:'DELETE'});
      await loadProviders();
    }
    async function testModelFn() {
      testModel.testing = true; testModel.result = null;
      try {
        testModel.result = await api('models/test', {method:'POST',body:JSON.stringify(testModel)});
      } catch(e) { testModel.result = {status:'error',error:e.message}; }
      testModel.testing = false;
    }

    // Stats
    async function loadStats() { stats.value = await api('stats'); }

    // Workflow
    function runWorkflow() {
      if(!selectedApp.value) return;
      const wfConfig = selectedApp.value.workflow_config;
      if(!wfConfig || !wfConfig.nodes || !wfConfig.nodes.length) {
        alert('请先配置工作流');
        return;
      }
      api('apps/'+selectedApp.value.id+'/workflow/run', {method:'POST',body:'{}'}).then(r => {
        alert('工作流运行完成: ' + JSON.stringify(r.status));
      });
    }

    function drawWorkflow() {
      if(!selectedApp.value || appTab.value !== 'workflow') return;
      nextTick(() => {
        const canvas = wfCanvas.value;
        const group = wfGroup.value;
        if(!canvas || !group) return;
        const svg = d3.select(canvas).select('svg');
        const g = d3.select(group);
        g.selectAll('*').remove();

        const wf = selectedApp.value.workflow_config || {};
        const nodes = wf.nodes || [];
        const edges = wf.edges || [];
        if(!nodes.length) return;

        const W = canvas.clientWidth, H = canvas.clientHeight;
        const nodeW = 140, nodeH = 50;
        const cols = Math.ceil(Math.sqrt(nodes.length));
        nodes.forEach((n, i) => {
          n.x = 60 + (i % cols) * (nodeW + 60);
          n.y = 60 + Math.floor(i / cols) * (nodeH + 60);
        });
        const nodeMap = {};
        nodes.forEach(n => nodeMap[n.id] = n);

        // Edges
        g.selectAll('.wf-link').data(edges).join('path').attr('class','wf-link')
          .attr('d', d => {
            const s = nodeMap[d.source], t = nodeMap[d.target];
            if(!s||!t) return '';
            return `M${s.x+nodeW/2},${s.y+nodeH} C${s.x+nodeW/2},${s.y+nodeH+40} ${t.x+nodeW/2},${t.y-40} ${t.x+nodeW/2},${t.y}`;
          });

        // Nodes
        const nodeG = g.selectAll('.wf-node').data(nodes).join('g').attr('class','wf-node')
          .attr('transform', d => `translate(${d.x},${d.y})`);
        nodeG.append('rect').attr('width', nodeW).attr('height', nodeH);
        nodeG.append('text').attr('x', nodeW/2).attr('y', nodeH/2+4).attr('text-anchor','middle')
          .text(d => (d.type||'').substring(0,12) + ': ' + (d.id||'').substring(0,8));
      });
    }

    watch(appTab, () => { if(appTab.value==='workflow') drawWorkflow(); });

    // Init
    onMounted(async () => {
      document.documentElement.setAttribute('data-theme', theme.value);
      await Promise.all([loadApps(), loadDatasets(), loadTools(), loadProviders(), loadStats()]);
    });

    return {
      page, theme, themes, stats, formatDate, renderMd, setTheme,
      apps, selectedApp, appTab, showCreateApp, showEditApp, newApp, editAppData,
      createApp, openApp, updateApp, deleteApp,
      chatApp, chatMessages, chatInput, chatSending, chatMsgs,
      openAppChat, sendChat, clearChat,
      datasets, selectedDataset, dsTab, showCreateDataset, newDataset,
      documents, showUploadDoc, newDoc,
      searchQuery, searchResults, searchKnowledge,
      loadDatasets, createDataset, openDataset, deleteDataset,
      uploadDocument, deleteDocument,
      builtinTools, customTools, showCreateTool, newTool,
      loadTools, createTool, deleteCustomTool,
      modelProviders, showCreateProvider, newProvider,
      showTestModel, testModel,
      loadProviders, createProvider, deleteProvider, testModelFn,
      wfCanvas, wfGroup, runWorkflow,
    };
  }
}).mount('#app');
</script>
</body>
</html>"""
