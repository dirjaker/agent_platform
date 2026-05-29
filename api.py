"""FastAPI Web 服务 — 支持真正的流式 SSE"""

import json
import uuid
import logging
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import load_config
from models import Conversation, Message
from database import AgentDatabase
from tool_registry import registry
from model_router import ModelRouter
from agent import AgentOrchestrator
from tool_loader import load_tools_from_directory

# 加载扩展工具
load_tools_from_directory()

logger = logging.getLogger(__name__)

# 初始化
config = load_config()
db = AgentDatabase(config["database"]["path"])
router = ModelRouter(config)
orchestrator = AgentOrchestrator(router, registry, config)
orchestrator.set_database(db)  # 注入数据库用于工具调用日志

app = FastAPI(title="Agent 工具调用平台", version="2.0.0")
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
    conv_id = request.conversation_id
    history = []

    if conv_id:
        conv = db.get_conversation(conv_id)
        if conv:
            history = db.get_messages(conv_id)
    else:
        conv_id = str(uuid.uuid4())
        # 自动生成标题
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
        # 异步生成标题（在后台执行）
        title = await orchestrator.generate_title(request.message, request.model)
        conv = Conversation(id=conv_id, title=title, model=model)
        db.create_conversation(conv)

    async def event_generator():
        """SSE 事件生成器 — 逐 token 发送"""
        full_response = ""

        async for msg in orchestrator.run_stream(
            user_input=request.message,
            conversation_id=conv_id,
            model=request.model,
            history=history,
        ):
            msg_type = msg.metadata.get("type", "")

            if msg_type == "token":
                # 流式 token
                full_response += msg.content
                data = json.dumps({
                    "type": "token",
                    "content": msg.content,
                    "conversation_id": conv_id,
                }, ensure_ascii=False)
                yield f"data: {data}\n\n"

            elif msg_type == "tool_call":
                # 工具调用请求
                data = json.dumps({
                    "type": "tool_call",
                    "tool_call": msg.tool_call,
                    "conversation_id": conv_id,
                }, ensure_ascii=False)
                yield f"data: {data}\n\n"

            elif msg_type == "tool_result":
                # 工具执行结果
                data = json.dumps({
                    "type": "tool_result",
                    "tool_result": msg.tool_result,
                    "conversation_id": conv_id,
                }, ensure_ascii=False)
                yield f"data: {data}\n\n"

            elif msg_type == "done":
                # 对话完成
                # 保存完整消息到数据库
                final_msg = Message(
                    role="assistant",
                    content=full_response,
                    metadata=msg.metadata,
                )
                db.save_message(conv_id, final_msg)

                data = json.dumps({
                    "type": "done",
                    "conversation_id": conv_id,
                    "metadata": msg.metadata,
                }, ensure_ascii=False)
                yield f"data: {data}\n\n"

            elif msg_type == "max_steps_reached":
                # 达到最大步数
                data = json.dumps({
                    "type": "error",
                    "content": msg.content,
                    "conversation_id": conv_id,
                }, ensure_ascii=False)
                yield f"data: {data}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


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


@app.get("/api/tool-logs/{conv_id}")
async def get_tool_logs(conv_id: str):
    """获取对话的工具调用日志"""
    with db._get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tool_logs WHERE conversation_id = ? ORDER BY created_at ASC",
            (conv_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ==================== Web UI ====================

@app.get("/", response_class=HTMLResponse)
async def web_ui():
    """Web 聊天界面"""
    return HTML_PAGE


def run_server(host: str = "0.0.0.0", port: int = 8000):
    """启动服务"""
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")


# ==================== 内嵌 HTML ====================

HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Agent 工具调用平台 v2.0</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
:root {
  --bg: #0f0f0f; --surface: #1a1a1a; --border: #2a2a2a;
  --text: #e0e0e0; --text2: #888; --accent: #4a9eff;
  --green: #4caf50; --red: #f44336; --yellow: #ffc107;
  --code-bg: #1e1e2e;
}
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); height: 100vh; display: flex; }

/* 侧边栏 */
.sidebar { width: 280px; background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; }
.sidebar-header { padding: 16px; border-bottom: 1px solid var(--border); }
.sidebar-header h2 { font-size: 16px; color: var(--accent); margin-bottom: 10px; }
.new-chat { width: 100%; padding: 10px; background: var(--accent); color: #fff; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; transition: opacity 0.2s; }
.new-chat:hover { opacity: 0.9; }
.conv-list { flex: 1; overflow-y: auto; padding: 8px; }
.conv-item { padding: 10px 12px; border-radius: 8px; cursor: pointer; margin-bottom: 4px; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; transition: background 0.2s; }
.conv-item:hover { background: var(--border); }
.conv-item.active { background: var(--accent); color: #fff; }
.sidebar-footer { padding: 12px 16px; border-top: 1px solid var(--border); font-size: 12px; color: var(--text2); }

/* 主区域 */
.main { flex: 1; display: flex; flex-direction: column; }
.chat-header { padding: 16px 20px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 12px; }
.chat-header .model-select { background: var(--surface); color: var(--text); border: 1px solid var(--border); padding: 6px 12px; border-radius: 6px; font-size: 13px; cursor: pointer; }
.messages { flex: 1; overflow-y: auto; padding: 20px; scroll-behavior: smooth; }

/* 消息样式 */
.msg { margin-bottom: 16px; display: flex; gap: 12px; max-width: 800px; animation: fadeIn 0.3s ease; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
.msg.user { margin-left: auto; flex-direction: row-reverse; }
.msg-avatar { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 18px; flex-shrink: 0; }
.msg.user .msg-avatar { background: var(--accent); }
.msg.assistant .msg-avatar { background: var(--green); }
.msg.tool .msg-avatar { background: var(--yellow); }
.msg-content { padding: 12px 16px; border-radius: 12px; font-size: 14px; line-height: 1.7; white-space: pre-wrap; word-break: break-word; }
.msg.user .msg-content { background: var(--accent); color: #fff; border-bottom-right-radius: 4px; }
.msg.assistant .msg-content { background: var(--surface); border: 1px solid var(--border); border-bottom-left-radius: 4px; }
.msg.tool .msg-content { background: #1a1a2e; border: 1px solid #2a2a4a; font-size: 12px; color: var(--text2); }
.msg-meta { font-size: 11px; color: var(--text2); margin-top: 6px; }

/* 工具调用状态 */
.tool-status { background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 8px; padding: 10px 14px; margin: 8px 0; font-size: 13px; }
.tool-status .tool-name { color: var(--yellow); font-weight: bold; }
.tool-status .tool-args { color: var(--text2); font-size: 12px; margin-top: 4px; }
.tool-status.success { border-color: #2a4a2a; }
.tool-status.error { border-color: #4a2a2a; }

/* 输入区 */
.input-area { padding: 16px 20px; border-top: 1px solid var(--border); }
.input-row { display: flex; gap: 10px; max-width: 800px; margin: 0 auto; }
#user-input { flex: 1; background: var(--surface); border: 1px solid var(--border); color: var(--text); padding: 12px 16px; border-radius: 12px; font-size: 14px; resize: none; min-height: 48px; max-height: 150px; outline: none; transition: border-color 0.2s; }
#user-input:focus { border-color: var(--accent); }
#send-btn { background: var(--accent); color: #fff; border: none; padding: 12px 24px; border-radius: 12px; cursor: pointer; font-size: 14px; transition: opacity 0.2s; }
#send-btn:hover { opacity: 0.9; }
#send-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.status { text-align: center; padding: 8px; font-size: 12px; color: var(--text2); }

/* Markdown 渲染 */
.msg-content code { background: #2a2a2a; padding: 2px 6px; border-radius: 4px; font-size: 13px; font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace; }
.msg-content pre { background: var(--code-bg); padding: 14px; border-radius: 8px; overflow-x: auto; margin: 10px 0; border: 1px solid var(--border); }
.msg-content pre code { background: none; padding: 0; font-size: 13px; line-height: 1.5; }
.msg-content strong { color: #fff; }
.msg-content em { color: #aaa; }
.msg-content ul, .msg-content ol { margin: 8px 0; padding-left: 20px; }
.msg-content li { margin: 4px 0; }
.msg-content blockquote { border-left: 3px solid var(--accent); padding-left: 12px; margin: 8px 0; color: var(--text2); }
.msg-content h1, .msg-content h2, .msg-content h3 { margin: 12px 0 8px 0; color: #fff; }
.msg-content h1 { font-size: 18px; }
.msg-content h2 { font-size: 16px; }
.msg-content h3 { font-size: 14px; }
.msg-content a { color: var(--accent); text-decoration: none; }
.msg-content a:hover { text-decoration: underline; }
.msg-content table { border-collapse: collapse; margin: 10px 0; width: 100%; }
.msg-content th, .msg-content td { border: 1px solid var(--border); padding: 8px 12px; text-align: left; }
.msg-content th { background: var(--surface); }

/* 流式光标 */
.cursor { display: inline-block; width: 2px; height: 14px; background: var(--accent); animation: blink 1s infinite; vertical-align: middle; margin-left: 2px; }
@keyframes blink { 0%, 50% { opacity: 1; } 51%, 100% { opacity: 0; } }
</style>
</head>
<body>
<div class="sidebar">
  <div class="sidebar-header">
    <h2>🤖 Agent 平台 v2.0</h2>
    <button class="new-chat" onclick="newChat()">+ 新对话</button>
  </div>
  <div class="conv-list" id="conv-list"></div>
  <div class="sidebar-footer" id="stats">加载中...</div>
</div>
<div class="main">
  <div class="chat-header">
    <select class="model-select" id="model-select"><option>加载中...</option></select>
    <span style="flex:1"></span>
    <span style="font-size:12px;color:var(--text2)">Agent 工具调用平台 · 支持流式输出</span>
  </div>
  <div class="messages" id="messages">
    <div class="status">选择或创建一个对话开始</div>
  </div>
  <div class="input-area">
    <div class="input-row">
      <textarea id="user-input" placeholder="输入消息... (Shift+Enter 换行)" rows="1" onkeydown="handleKey(event)"></textarea>
      <button id="send-btn" onclick="sendMessage()">发送</button>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script>
const API = '';
let currentConv = null;
let conversations = [];

// 配置 marked
marked.setOptions({
  breaks: true,
  gfm: true,
  highlight: function(code, lang) {
    return code;
  }
});

async function init() {
  await loadConversations();
  await loadModels();
  await loadStats();
}

async function loadConversations() {
  const res = await fetch(API + '/api/conversations');
  conversations = await res.json();
  renderConvList();
}

function renderConvList() {
  const el = document.getElementById('conv-list');
  el.innerHTML = conversations.map(c =>
    `<div class="conv-item ${c.id===currentConv?'active':''}" onclick="selectConv('${c.id}')">${c.title||'新对话'}</div>`
  ).join('');
}

async function selectConv(id) {
  currentConv = id;
  renderConvList();
  const res = await fetch(API + '/api/conversations/' + id);
  const data = await res.json();
  renderMessages(data.messages || []);
}

function renderMessages(msgs) {
  const el = document.getElementById('messages');
  el.innerHTML = msgs.map(m => {
    if (m.role === 'user') return `<div class="msg user"><div class="msg-avatar">👤</div><div class="msg-content">${esc(m.content)}</div></div>`;
    if (m.role === 'tool') {
      const info = m.tool_result ? (typeof m.tool_result === 'string' ? JSON.parse(m.tool_result) : m.tool_result) : {};
      const success = info.result?.success;
      return `<div class="tool-status ${success?'success':'error'}">
        <div>🔧 <span class="tool-name">${info.name||'unknown'}</span> ${success?'✅ 执行成功':'❌ 执行失败'}</div>
        ${info.result?.execution_time?`<div class="tool-args">耗时: ${info.result.execution_time.toFixed(2)}s</div>`:''}
      </div>`;
    }
    if (m.role === 'assistant' && m.content) return `<div class="msg assistant"><div class="msg-avatar">🤖</div><div><div class="msg-content">${formatMd(m.content)}</div>${m.metadata?.steps?`<div class="msg-meta">步骤: ${m.metadata.steps} | 模型: ${m.metadata.model||''}</div>`:''}</div></div>`;
    return '';
  }).join('');
  el.scrollTop = el.scrollHeight;
}

async function sendMessage() {
  const input = document.getElementById('user-input');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  input.style.height = 'auto';

  // 显示用户消息
  appendMsg('user', msg);

  const btn = document.getElementById('send-btn');
  btn.disabled = true;
  btn.textContent = '思考中...';

  try {
    const model = document.getElementById('model-select').value;

    // 使用真正的流式接口
    const response = await fetch(API + '/api/chat/stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg, conversation_id: currentConv, model: model}),
    });

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let assistantContent = '';
    let assistantMsgEl = null;
    let toolStatusEl = null;

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, {stream: true});
      const lines = buffer.split('\\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const dataStr = line.slice(6);
        if (dataStr === '[DONE]') continue;

        try {
          const data = JSON.parse(dataStr);

          if (data.type === 'token') {
            // 流式 token
            if (!assistantMsgEl) {
              assistantMsgEl = appendStreamingMsg();
            }
            assistantContent += data.content;
            updateStreamingMsg(assistantMsgEl, assistantContent);
          }
          else if (data.type === 'tool_call') {
            // 工具调用
            const func = data.tool_call?.function;
            if (func) {
              toolStatusEl = appendToolStatus(func.name, func.arguments, 'calling');
            }
          }
          else if (data.type === 'tool_result') {
            // 工具结果
            const info = data.tool_result;
            if (toolStatusEl) {
              updateToolStatus(toolStatusEl, info.name, info.result);
            } else {
              appendToolStatus(info.name, '', info.result?.success ? 'success' : 'error', info.result);
            }
            toolStatusEl = null;
          }
          else if (data.type === 'done') {
            // 完成
            if (assistantMsgEl) {
              finalizeStreamingMsg(assistantMsgEl, assistantContent, data.metadata);
            }
            currentConv = data.conversation_id;
          }
          else if (data.type === 'error') {
            appendMsg('assistant', data.content);
          }
        } catch (e) {
          console.error('Parse error:', e, dataStr);
        }
      }
    }

    await loadConversations();
    await loadStats();
  } catch (e) {
    appendMsg('assistant', '❌ 错误: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '发送';
  }
}

function appendMsg(role, content, meta) {
  const el = document.getElementById('messages');
  if (el.querySelector('.status')) el.innerHTML = '';

  let html = '';
  if (role === 'user') {
    html = `<div class="msg user"><div class="msg-avatar">👤</div><div class="msg-content">${esc(content)}</div></div>`;
  } else {
    const metaHtml = meta?.steps ? `<div class="msg-meta">步骤: ${meta.steps} | 模型: ${meta.model||''}</div>` : '';
    html = `<div class="msg assistant"><div class="msg-avatar">🤖</div><div><div class="msg-content">${formatMd(content)}</div>${metaHtml}</div></div>`;
  }
  el.insertAdjacentHTML('beforeend', html);
  el.scrollTop = el.scrollHeight;
}

function appendStreamingMsg() {
  const el = document.getElementById('messages');
  if (el.querySelector('.status')) el.innerHTML = '';

  const html = `<div class="msg assistant"><div class="msg-avatar">🤖</div><div><div class="msg-content"><span class="cursor"></span></div></div></div>`;
  el.insertAdjacentHTML('beforeend', html);
  el.scrollTop = el.scrollHeight;
  return el.lastElementChild;
}

function updateStreamingMsg(msgEl, content) {
  const contentEl = msgEl.querySelector('.msg-content');
  contentEl.innerHTML = formatMd(content) + '<span class="cursor"></span>';
  const messagesEl = document.getElementById('messages');
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function finalizeStreamingMsg(msgEl, content, metadata) {
  const contentEl = msgEl.querySelector('.msg-content');
  contentEl.innerHTML = formatMd(content);

  if (metadata?.steps) {
    const metaHtml = `<div class="msg-meta">步骤: ${metadata.steps} | 模型: ${metadata.model||''}</div>`;
    contentEl.insertAdjacentHTML('afterend', metaHtml);
  }
}

function appendToolStatus(name, args, status, result) {
  const el = document.getElementById('messages');
  const statusClass = status === 'success' ? 'success' : status === 'error' ? 'error' : '';
  const statusIcon = status === 'success' ? '✅' : status === 'error' ? '❌' : '⏳';
  const statusText = status === 'success' ? '执行成功' : status === 'error' ? '执行失败' : '执行中...';

  let argsHtml = '';
  if (args && typeof args === 'string') {
    try {
      const argsObj = JSON.parse(args);
      argsHtml = `<div class="tool-args">参数: ${JSON.stringify(argsObj)}</div>`;
    } catch(e) {
      argsHtml = `<div class="tool-args">参数: ${args}</div>`;
    }
  }

  let resultHtml = '';
  if (result?.execution_time) {
    resultHtml = `<div class="tool-args">耗时: ${result.execution_time.toFixed(2)}s</div>`;
  }

  const html = `<div class="tool-status ${statusClass}">
    <div>🔧 <span class="tool-name">${name}</span> ${statusIcon} ${statusText}</div>
    ${argsHtml}
    ${resultHtml}
  </div>`;

  el.insertAdjacentHTML('beforeend', html);
  el.scrollTop = el.scrollHeight;
  return el.lastElementChild;
}

function updateToolStatus(el, name, result) {
  const success = result?.success;
  el.className = `tool-status ${success ? 'success' : 'error'}`;
  const icon = success ? '✅' : '❌';
  const text = success ? '执行成功' : '执行失败';
  el.innerHTML = `
    <div>🔧 <span class="tool-name">${name}</span> ${icon} ${text}</div>
    ${result?.execution_time ? `<div class="tool-args">耗时: ${result.execution_time.toFixed(2)}s</div>` : ''}
  `;
}

async function newChat() {
  currentConv = null;
  document.getElementById('messages').innerHTML = '<div class="status">开始新对话</div>';
  renderConvList();
  document.getElementById('user-input').focus();
}

async function loadModels() {
  const res = await fetch(API + '/api/models');
  const models = await res.json();
  const sel = document.getElementById('model-select');
  sel.innerHTML = models.length ?
    models.map(m => `<option value="${m.model}">${m.model} (${m.provider})</option>`).join('') :
    '<option>未配置模型</option>';
}

async function loadStats() {
  const res = await fetch(API + '/api/stats');
  const s = await res.json();
  document.getElementById('stats').textContent = `对话: ${s.conversations} | 消息: ${s.messages} | 工具调用: ${s.tool_calls} | 成功率: ${s.tool_success_rate}`;
}

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  // 自动调整高度
  const input = e.target;
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 150) + 'px';
}

function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\\n/g,'<br>'); }
function formatMd(s) {
  try {
    return marked.parse(s);
  } catch(e) {
    return s.replace(/```([\\s\\S]*?)```/g, '<pre><code>$1</code></pre>')
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>')
            .replace(/\\n/g, '<br>');
  }
}

init();
</script>
</body>
</html>"""
