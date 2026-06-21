# 开发环境搭建指南

本文档介绍如何搭建 Agent Platform 的本地开发环境。

---

## 一、环境要求

| 依赖 | 版本要求 | 说明 |
|------|---------|------|
| Python | >= 3.10 | 推荐 3.12 |
| pip | 最新版 | 用于安装依赖 |
| Git | 任意版本 | 版本管理 |
| Conda（可选） | 任意版本 | 虚拟环境管理 |

> **注意**：本项目无需 Docker、Redis、PostgreSQL 等外部服务，仅需 Python 环境即可运行。

---

## 二、快速搭建

### 2.1 克隆项目

```bash
git clone https://github.com/dirjaker/agent_platform.git
cd agent_platform
git checkout dev
```

### 2.2 创建虚拟环境

**方式一：Conda（推荐）**

```bash
conda create -n agent_platform python=3.12 -y
conda activate agent_platform
```

**方式二：venv**

```bash
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows
```

### 2.3 安装依赖

```bash
pip install -r requirements.txt
```

核心依赖：
- `fastapi` — Web 框架
- `uvicorn` — ASGI 服务器
- `httpx` — HTTP 客户端（调用模型 API）
- `pydantic` — 数据模型验证
- `pyyaml` — 配置文件解析
- `rich` — CLI 终端美化

### 2.4 配置 API Key

**方式一：环境变量（推荐）**

```bash
export AGENT_DEEPSEEK_KEY="sk-your-deepseek-api-key"
# 或
export AGENT_OPENROUTER_KEY="sk-your-openrouter-api-key"
```

**方式二：编辑配置文件**

编辑 `config.yaml`：

```yaml
providers:
  deepseek:
    api_key: "sk-your-deepseek-api-key"
    base_url: "https://api.deepseek.com"
    models:
      - "deepseek-chat"
      - "deepseek-v4-flash"
      - "deepseek-v4-pro"
```

### 2.5 验证安装

```bash
# 查看可用工具
python cli.py tools

# 查看可用模型
python cli.py models

# 测试对话
python cli.py ask "你好，介绍一下你自己"
```

---

## 三、启动方式

### 3.1 Web 服务（推荐）

```bash
python api.py
# 或
python cli.py server --port 10002
```

访问 http://localhost:10002 打开管理界面。

### 3.2 CLI 交互模式

```bash
python cli.py chat
```

### 3.3 Web Dashboard（独立管理面板）

```bash
python src/web/app.py
# 默认端口 8003
```

### 3.4 macOS 桌面应用

```bash
python src/macos/app.py
```

---

## 四、项目结构详解

```
agent_platform/
├── agent.py              # Agent 编排器（ReAct 循环核心）
│                         #   - AgentOrchestrator 类
│                         #   - run() 同步执行
│                         #   - run_stream() 流式执行
│                         #   - generate_title() 标题生成
│
├── api.py                # FastAPI Web 服务（主入口）
│                         #   - 所有 REST API 路由
│                         #   - SSE 流式对话接口
│                         #   - 内嵌 Vue3 前端（约 900 行 HTML/CSS/JS）
│                         #   - run_server() 启动函数
│
├── cli.py                # CLI 命令行工具
│                         #   - chat: 交互式对话
│                         #   - ask: 单次提问
│                         #   - tools/models/stats: 查看信息
│                         #   - server: 启动 Web 服务
│
├── config.py             # 配置管理
│                         #   - load_config(): 三层合并（环境变量>文件>默认值）
│                         #   - save_config(): 保存配置
│                         #   - DEFAULT_CONFIG: 默认配置
│
├── config.yaml           # 配置文件（用户编辑）
│
├── database.py           # SQLite 数据库管理
│                         #   - AgentDatabase 类
│                         #   - 12 张数据表自动建表
│                         #   - 完整 CRUD 操作
│
├── models.py             # Pydantic 数据模型
│                         #   - ToolDefinition, ToolParameter, ToolResult
│                         #   - Message, Conversation
│                         #   - ModelResponse
│                         #   - ParameterType (enum)
│
├── tool_registry.py      # 工具注册中心
│                         #   - ToolRegistry 类
│                         #   - @register() 装饰器
│                         #   - to_function_calls() 转 OpenAI 格式
│                         #   - execute() 异步执行
│                         #   - safe_exec/safe_eval 安全沙箱
│                         #   - 内置工具（get_current_time, calculate 等）
│
├── tool_loader.py        # 工具热加载器
│                         #   - load_tools_from_directory()
│                         #   - reload_tools()
│
├── model_router.py       # 多模型路由器
│                         #   - ModelRouter 类
│                         #   - _get_adapter() 模型查找
│                         #   - chat()/chat_stream() 同步/流式
│                         #   - 故障转移机制
│
├── app_manager.py        # 应用管理器
│                         #   - AppManager 类
│                         #   - 应用 CRUD
│                         #   - build_chat_config() 构建聊天配置
│
├── knowledge_manager.py  # 知识库管理器
│                         #   - KnowledgeManager 类
│                         #   - TextChunker 文本分块器
│                         #   - TFIDFVectorizer 向量化器
│                         #   - search() 语义搜索
│                         #   - upload_document() 文档上传+分块
│
├── workflow_engine.py    # DAG 工作流引擎
│                         #   - WorkflowEngine 类
│                         #   - topological_sort() 拓扑排序
│                         #   - interpolate() 变量插值
│                         #   - 6 种节点执行器
│                         #   - run_workflow() 执行工作流
│
├── adapters/
│   └── __init__.py       # 模型适配器
│                         #   - BaseModelAdapter (ABC)
│                         #   - DeepSeekAdapter（真正流式 SSE）
│                         #   - OpenRouterAdapter（真正流式 SSE）
│                         #   - OpenAICompatibleAdapter（vLLM 等）
│
├── tools/
│   ├── __init__.py       # 包初始化
│   ├── builtin_tools.py  # 扩展工具集（HTTP/JSON/文本/加密/知识库）
│   └── README.md         # 自定义工具开发指南
│
├── src/
│   ├── web/
│   │   ├── app.py        # Web Dashboard 管理面板（独立服务）
│   │   └── static/
│   │       └── index.html
│   └── macos/
│       └── app.py        # macOS 桌面应用（tkinter GUI）
│
├── data/
│   └── agent.db          # SQLite 数据库（自动生成）
│
├── assets/
│   └── banner.svg        # README Banner 图片
│
├── docs/
│   ├── 技术文档.md        # 技术设计文档
│   ├── 项目总规划.md      # 项目规划与路线图
│   ├── DEVELOPMENT.md    # 开发环境搭建指南（本文件）
│   └── CHANGELOG.md      # 版本更新日志
│
├── requirements.txt      # Python 依赖
├── run.sh                # 启动脚本
├── .gitignore            # Git 忽略规则
├── LICENSE               # MIT 许可证
└── REVIEW.md             # 代码审查报告
```

---

## 五、开发规范

### 5.1 代码风格

- 使用 Python 3.10+ 语法（`list[str]`、`dict[str, Any]`、`str | None`）
- 使用 Pydantic v2 进行数据验证
- 函数和类添加 docstring
- 使用 `logging` 模块记录日志（不要用 `print`）

### 5.2 添加新工具

1. 在 `tools/` 目录下创建 `.py` 文件
2. 使用 `@registry.register()` 装饰器注册工具
3. 定义参数（可自动推断或手动指定）
4. 重启服务自动加载

示例：

```python
from tool_registry import registry
from models import ToolParameter, ParameterType

@registry.register(
    name="my_new_tool",
    description="工具描述",
    category="自定义",
    parameters=[
        ToolParameter(name="input", type=ParameterType.STRING, description="输入", required=True),
    ],
)
def my_new_tool(input: str) -> str:
    return f"处理结果: {input}"
```

### 5.3 添加新的模型适配器

1. 在 `adapters/__init__.py` 中继承 `BaseModelAdapter`
2. 实现 `chat()` 和 `chat_stream()` 方法
3. 在 `model_router.py` 的 `_init_adapters()` 中注册

### 5.4 添加新的工作流节点

1. 在 `workflow_engine.py` 中继承 `NodeExecutor`
2. 实现 `execute()` 方法
3. 在 `NODE_EXECUTORS` 字典中注册

### 5.5 数据库迁移

数据库使用 SQLite，建表语句在 `database.py` 的 `_init_db()` 中。如需新增表或字段：

1. 在 `_init_db()` 的 `CREATE TABLE IF NOT EXISTS` 中添加新表
2. 使用 `ALTER TABLE ... ADD COLUMN` 添加新字段（SQLite 不支持 `IF NOT EXISTS`，需用 try/except）

---

## 六、测试

### 6.1 手动测试

```bash
# 测试 CLI
python cli.py ask "现在几点了？"
python cli.py ask "帮我算一下 123 * 456"
python cli.py ask "读取 config.yaml 的内容"

# 测试 Web API
curl http://localhost:10002/api/health
curl http://localhost:10002/api/tools
curl http://localhost:10002/api/models

# 测试流式对话
curl -N -X POST http://localhost:10002/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "你好"}'
```

### 6.2 工作流测试

```bash
# 创建应用
curl -X POST http://localhost:10002/api/apps \
  -H "Content-Type: application/json" \
  -d '{"name": "测试应用", "app_type": "chat", "system_prompt": "你是一个测试助手"}'

# 运行工作流
curl -X POST http://localhost:10002/api/apps/{app_id}/workflow/run \
  -H "Content-Type: application/json" \
  -d '{"inputs": {"query": "测试输入"}}'
```

---

## 七、常见问题

### Q: 启动时报 "没有可用的模型适配器"

A: 请检查 `config.yaml` 中的 `api_key` 是否已配置，或设置环境变量 `AGENT_DEEPSEEK_KEY`。

### Q: 工具加载失败

A: 检查 `tools/` 目录下 `.py` 文件的语法是否正确。启动时会输出加载日志。

### Q: 数据库在哪里？

A: 默认路径 `data/agent.db`，首次启动自动创建。可通过 `config.yaml` 的 `database.path` 修改。

### Q: 如何切换模型？

A: 在 `config.yaml` 中修改 `default_model`，或在对话请求中指定 `model` 参数。支持的模型取决于已配置的 Provider。

### Q: 如何重置数据库？

A: 删除 `data/agent.db` 文件，重启服务会自动重建。
