# Agent Platform 重构计划 — Dify-Style

> 目标：对标 Dify，构建轻量级 LLM 应用开发平台
> 范围：应用管理 · 知识库 RAG · 工作流编排 · ChatFlow 编排
> 原则：单进程部署 · SQLite · Vue3 单文件 · Dify 风格 UI

---

## 一、产品定位

### 1.1 与 Dify 的关系

| 维度 | Dify | Agent Platform (本方案) |
|------|------|------------------------|
| 部署 | Docker 多容器 | 单进程 Python |
| 数据库 | PostgreSQL + Redis | SQLite |
| 前端 | Next.js 独立项目 | Vue3 单 HTML |
| 功能范围 | 完整 SaaS 平台 | 核心四模块 |
| 工具市场 | ✅ | ❌ 不做 |
| 多租户 | ✅ | ❌ 单用户 |
| 插件系统 | ✅ | ❌ 不做 |

### 1.2 四个核心模块

```
┌─────────────────────────────────────────────────────┐
│                  Agent Platform                      │
├─────────────┬─────────────┬─────────────┬────────────┤
│  应用管理    │  知识库 RAG  │  工作流编排  │ ChatFlow   │
│  App Mgmt   │  Knowledge  │  Workflow   │  编排       │
├─────────────┼─────────────┼─────────────┼────────────┤
│ 创建/配置    │ 文档上传     │ 节点拖拽     │ 对话流程    │
│ ChatBot/    │ 自动分块     │ LLM/Code/   │ 开场白/     │
│ Workflow/   │ 向量检索     │ HTTP/IfElse │ 建议问题    │
│ Agent 应用   │ 测试查询     │ /Loop 节点   │ Prompt编排  │
└─────────────┴─────────────┴─────────────┴────────────┘
```

---

## 二、技术架构

### 2.1 技术栈

| 层 | 方案 | 理由 |
|----|------|------|
| 画布引擎 | 自研轻量 Canvas (SVG + drag) | 避免 React Flow 依赖 |
| 前端 | Vue 3 CDN + Pinia | 与现有一致 |
| 后端 | FastAPI + SQLAlchemy async | 与现有一致 |
| 数据库 | SQLite (aiosqlite) | 零依赖部署 |
| 向量存储 | **Milvus Standalone** (远程) | 101.132.81.140:19530，v2.4.0 |
| Embedding | sentence-transformers (本地) | all-MiniLM-L6-v2 (384维) |
| LLM 路由 | 现有 model_router.py | 已支持 DeepSeek 等 |

### 2.2 目录结构

```
agent_platform/
├── api.py              # FastAPI 入口
├── models.py           # SQLAlchemy 模型
├── database.py         # 数据库初始化
├── config.py           # 配置管理
├── model_router.py     # LLM 路由 (已有)
│
├── modules/
│   ├── app_manager.py      # 应用 CRUD
│   ├── knowledge/          # 知识库模块
│   │   ├── __init__.py
│   │   ├── loader.py       # 文档加载 + 分块
│   │   ├── indexer.py      # Milvus 向量化 + 写入
│   │   ├── retriever.py    # Milvus 语义检索
│   │   └── embedding.py    # sentence-transformers 调用
│   ├── workflow/           # 工作流模块
│   │   ├── __init__.py
│   │   ├── engine.py       # DAG 执行引擎
│   │   ├── nodes/          # 节点实现
│   │   │   ├── start.py
│   │   │   ├── llm.py
│   │   │   ├── code.py
│   │   │   ├── http.py
│   │   │   ├── ifelse.py
│   │   │   ├── loop.py
│   │   │   ├── knowledge.py
│   │   │   └── answer.py
│   │   └── executor.py     # 异步执行器
│   └── chatflow/           # ChatFlow 模块
│       ├── __init__.py
│       └── engine.py       # 对话流引擎
│
├── templates/
│   ├── index.html          # 主框架
│   ├── apps.html           # 应用列表
│   ├── workflow.html       # 工作流编辑器
│   ├── knowledge.html      # 知识库管理
│   └── chatflow.html       # ChatFlow 编辑器
│
└── static/
    └── app.js              # 共享 JS
```

### 2.3 Milvus 配置（远程 Standalone）

```python
# config.py
MILVUS_URI = "http://101.132.81.140:19530"
MILVUS_DIM = 384  # all-MiniLM-L6-v2

# modules/knowledge/embedding.py
from sentence_transformers import SentenceTransformer
model = SentenceTransformer('all-MiniLM-L6-v2')  # 384维

# modules/knowledge/indexer.py
from pymilvus import MilvusClient
from config import MILVUS_URI

client = MilvusClient(uri=MILVUS_URI)
# 每个知识库 = 一个 Collection
# 写入: client.insert(collection_name, [vectors])
# 检索: client.search(collection_name, data=[query_vec], limit=5, output_fields=['chunk_text','doc_id'])
```

**优势：**
- 独立服务器运行，不占用本机内存
- 支持大规模向量（百万级）
- 已部署 v2.4.0，无需额外运维
- frp 穿透访问，公网可连

### 2.4 数据库模型

```sql
-- 应用
CREATE TABLE apps (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,         -- 'chatbot' | 'workflow' | 'agent'
    description TEXT,
    icon TEXT DEFAULT '💬',
    config JSON,                -- { model, prompt, temperature, ... }
    status TEXT DEFAULT 'draft',
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

-- 知识库
CREATE TABLE knowledge_bases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    chunk_size INTEGER DEFAULT 500,
    created_at TIMESTAMP
);

CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    kb_id TEXT REFERENCES knowledge_bases(id),
    filename TEXT,
    content TEXT,
    chunks JSON,              -- [{text, index}]
    created_at TIMESTAMP
);

-- 工作流
CREATE TABLE workflows (
    id TEXT PRIMARY KEY,
    app_id TEXT REFERENCES apps(id),
    name TEXT NOT NULL,
    nodes JSON,               -- [{id, type, x, y, config}]
    edges JSON,               -- [{source, target, sourceHandle, targetHandle}]
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

-- 对话流 (ChatFlow)
CREATE TABLE chatflows (
    id TEXT PRIMARY KEY,
    app_id TEXT REFERENCES apps(id),
    name TEXT NOT NULL,
    opening_message TEXT,
    suggested_questions JSON,
    prompt_template TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

### 2.5 API 路由

```
# 应用管理
GET    /api/apps                    # 列表
POST   /api/apps                    # 创建
GET    /api/apps/:id                # 详情
PUT    /api/apps/:id                # 更新
DELETE /api/apps/:id                # 删除
POST   /api/apps/:id/chat           # ChatBot 对话

# 知识库
GET    /api/knowledge-bases         # 列表
POST   /api/knowledge-bases         # 创建
GET    /api/knowledge-bases/:id     # 详情
DELETE /api/knowledge-bases/:id     # 删除
POST   /api/knowledge-bases/:id/upload   # 上传文档
POST   /api/knowledge-bases/:id/search   # 检索

# 工作流
GET    /api/workflows               # 列表
POST   /api/workflows               # 创建
GET    /api/workflows/:id           # 详情 (含 nodes + edges)
PUT    /api/workflows/:id           # 保存
POST   /api/workflows/:id/run       # 执行
GET    /api/workflows/:id/runs      # 执行历史

# ChatFlow
GET    /api/chatflows               # 列表
POST   /api/chatflows               # 创建
GET    /api/chatflows/:id           # 详情
PUT    /api/chatflows/:id           # 保存
POST   /api/chatflows/:id/preview   # 预览对话
```

---

## 三、开发阶段

### Phase 1：基础架构 (2-3 天)
- [ ] 数据库模型 + 迁移
- [ ] API 骨架 (FastAPI router)
- [ ] 前端主框架 (侧栏 + 顶栏 + 内容区)
- [ ] 应用 CRUD 完整实现

### Phase 2：应用管理 (1-2 天)
- [ ] 应用列表页（卡片网格）
- [ ] 创建应用弹窗（选类型：ChatBot / Workflow / Agent）
- [ ] 应用配置页（模型选择、Prompt 编辑、参数调节）
- [ ] ChatBot 对话测试面板

### Phase 3：知识库 RAG (2-3 天)
- [ ] 知识库 CRUD
- [ ] 文档上传 + 解析（TXT/MD/PDF）
- [ ] 文本分块 (chunk_size 可配，默认 500)
- [ ] sentence-transformers 向量化 (all-MiniLM-L6-v2, 384维)
- [ ] Milvus Lite 创建 Collection + 写入向量
- [ ] 语义检索 API（Top-K + score 阈值）
- [ ] 前端测试检索界面
- [ ] 在工作流中集成 Knowledge 检索节点

### Phase 4：工作流编排 (4-5 天)
- [ ] 画布引擎（SVG 拖拽节点 + 连线）
- [ ] 左侧节点面板（分类折叠）
- [ ] 右侧节点配置面板
- [ ] 节点类型实现：Start / LLM / Code / HTTP / IfElse / Loop / Knowledge / Answer
- [ ] DAG 执行引擎
- [ ] 运行测试 + 日志

### Phase 5：ChatFlow 编排 (2-3 天)
- [ ] ChatFlow 编辑器（开场白 + 建议问题 + Prompt 模板）
- [ ] 对话预览面板
- [ ] 变量注入（知识库上下文）
- [ ] 对话历史管理

### Phase 6：打磨 (1-2 天)
- [ ] 错误处理 + 加载状态
- [ ] 响应式适配
- [ ] 主题系统（Light / Dark）
- [ ] README + 文档

---

## 四、UI 设计方案

### 4.1 整体布局（Dify-Style）

```
┌──────────────────────────────────────────────────────────┐
│  Header: Logo | 应用管理 | 知识库 | 工作流 | ChatFlow    │
├────────┬───────────────────────────────────┬─────────────┤
│        │                                   │             │
│  侧边栏 │         主内容区                   │   (配置面板) │
│        │                                   │             │
│  · 应用 │  ┌──────┐ ┌──────┐ ┌──────┐     │  节点名称    │
│  · 知识库│  │ App1 │ │ App2 │ │ App3 │     │  模型选择    │
│  · 模型  │  └──────┘ └──────┘ └──────┘     │  Prompt     │
│  · 设置  │                                   │  变量绑定    │
│        │                                   │             │
└────────┴───────────────────────────────────┴─────────────┘
```

### 4.2 配色

| Token | Light | Dark |
|-------|-------|------|
| bg-primary | #f5f6fa | #0f1117 |
| bg-card | #ffffff | #1a1b26 |
| text-primary | #1a1b2e | #e2e4f0 |
| text-secondary | #6b6d80 | #9499b3 |
| accent | #6366f1 | #6c63ff |
| border | #e2e4ea | #2d2e45 |

### 4.3 四个核心页面

1. **应用列表** — 卡片网格 + 顶部统计 + 创建弹窗
2. **知识库管理** — 左侧列表 + 右侧文档上传 + 测试检索
3. **工作流编辑器** — 三栏：节点库 | 画布 | 配置
4. **ChatFlow 编辑器** — 左侧配置 + 右侧对话预览

---

## 五、风险 & 决策点

| 风险 | 缓解 |
|------|------|
| 自研画布引擎复杂 | 先做 MVP（拖拽新位置 + 连线），不追求完美 |
| Embedding 模型下载慢 | 预下载 all-MiniLM-L6-v2 到本地，或支持用户配置 API endpoint |
| SQLite 并发瓶颈 | 单用户场景足够，后续可换 PostgreSQL |
| 工作流执行安全 | Code 节点用 RestrictedPython 沙箱 |
