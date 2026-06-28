# AI 工作流编排平台 — 设计方案

> 参考 Dify 开源项目架构，设计一个节点拖拽式工作流编排平台

## 一、核心架构

### 1.1 技术选型

| 层 | 方案 | 理由 |
|----|------|------|
| **画布引擎** | React Flow (reactflow) | Dify 同款，成熟稳定，支持自定义节点/边 |
| **前端框架** | Vue 3 + Composition API | 与现有项目一致 |
| **状态管理** | Pinia (canvas state) + VueUse (drag/resize) | 工作流状态复杂，需要响应式 store |
| **后端** | FastAPI + SQLAlchemy async | 与现有架构一致 |
| **工作流引擎** | 自研 DAG 调度器 | 轻量，不需要 Celery |

### 1.2 页面布局

```
┌─────────────────────────────────────────────────────────┐
│  Header: 工作流名称 | 发布/保存 | 运行/测试 ▼           │
├────────┬───────────────────────────────────┬────────────┤
│        │                                   │            │
│  Node  │     React Flow Canvas            │  Config    │
│ Picker │                                   │   Panel    │
│        │   [Start]──→[LLM]──→[Code]──→[End]│            │
│        │                                   │  节点名称   │
│  Start │                                   │  模型选择   │
│  LLM   │                                   │  Prompt    │
│  Code  │                                   │  变量绑定   │
│  HTTP  │                                   │            │
│  IfElse│                                   │            │
│  Tool  │                                   │            │
│  Loop  │                                   │            │
│        │                                   │            │
├────────┴───────────────────────────────────┴────────────┤
│  Status Bar: ✓ 已保存 | 12 节点 | 运行时 234ms          │
└─────────────────────────────────────────────────────────┘
```

### 1.3 节点类型

| 分类 | 节点 | 图标 | 输入 | 输出 |
|------|------|------|------|------|
| **基础** | Start | ▶ | - | 触发变量 |
| | End | ⏹ | 任意 | - |
| | Answer | 💬 | 文本 | 最终回复 |
| **AI** | LLM | 🧠 | Prompt + 变量 | 文本/JSON |
| | Knowledge | 📚 | 查询 | 检索结果 |
| | Agent | 🤖 | 任务描述 | 执行结果 |
| **逻辑** | If/Else | 🔀 | 条件 | true/false 分支 |
| | Loop | 🔄 | 数组 | 逐项输出 |
| **处理** | Code | 💻 | 变量 | 执行结果 |
| | HTTP | 🌐 | URL + Body | 响应 |
| | Template | 📝 | 变量 | 格式化文本 |
| **工具** | Tool | 🔧 | 参数 | 工具输出 |
| | HumanInput | 👤 | 提示 | 用户输入 |

### 1.4 核心交互

1. **添加节点**: 从左侧面板拖拽或点击节点到画布
2. **连接节点**: 从节点输出 Handle 拖线到目标节点输入 Handle
3. **配置节点**: 点击节点 → 右侧面板显示配置表单
4. **删除节点**: 选中 → Delete 键 / 右键菜单
5. **画布操作**: 滚轮缩放、拖拽平移、框选多节点

## 二、数据库设计

```sql
-- 工作流主表
CREATE TABLE workflows (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'draft',  -- draft/published/archived
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

-- 节点表
CREATE TABLE workflow_nodes (
    id TEXT PRIMARY KEY,
    workflow_id TEXT REFERENCES workflows(id),
    type TEXT NOT NULL,        -- start/llm/code/http/ifelse/tool/end
    position_x REAL,
    position_y REAL,
    config JSON,               -- 节点配置 (model, prompt, code...)
    created_at TIMESTAMP
);

-- 边表
CREATE TABLE workflow_edges (
    id TEXT PRIMARY KEY,
    workflow_id TEXT REFERENCES workflows(id),
    source_node_id TEXT,
    target_node_id TEXT,
    source_handle TEXT,        -- 源节点输出端口
    target_handle TEXT,        -- 目标节点输入端口
    label TEXT                 -- 条件分支标签 (if/else)
);

-- 执行记录
CREATE TABLE workflow_runs (
    id TEXT PRIMARY KEY,
    workflow_id TEXT REFERENCES workflows(id),
    status TEXT,               -- running/completed/failed
    input JSON,
    output JSON,
    started_at TIMESTAMP,
    finished_at TIMESTAMP
);
```

## 三、API 设计

```
GET    /api/workflows              # 列表
POST   /api/workflows              # 创建
GET    /api/workflows/:id          # 详情
PUT    /api/workflows/:id          # 更新
DELETE /api/workflows/:id          # 删除
POST   /api/workflows/:id/run      # 执行
GET    /api/workflows/:id/runs     # 执行历史

# 节点单独 API (可选，通常随 workflow 一起保存)
PUT    /api/workflows/:id/nodes    # 批量更新节点位置
```

## 四、两个设计方案

### 方案 A: Dify-Style（经典三栏）

- 左侧：节点选择器（分类折叠 + 搜索）
- 中间：React Flow 画布（网格背景 + 小地图）
- 右侧：节点配置面板（动态表单）
- 顶部：工具栏（保存/发布/运行/撤销/重做）
- 底部：状态栏

**适合**: 功能完整的专业平台

### 方案 B: Minimal Canvas（极简浮窗）

- 全屏画布
- 浮动节点选择器（搜索 + 快捷添加）
- 点击节点弹出配置浮窗（非固定面板）
- 顶部轻量工具栏
- 底部小地图

**适合**: 高频操作、减少视觉干扰
