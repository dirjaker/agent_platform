# 🤖 Agent 工具调用平台

一个支持自定义工具注册、多模型调度、多轮对话的智能 Agent 平台。

## 功能特性

- 🔧 **工具注册系统** — 装饰器注册、自动推断参数、OpenAI Function Calling 格式
- 🤖 **ReAct 循环** — Think-Act-Observe 多步推理
- 🔀 **多模型路由** — DeepSeek / OpenRouter / 本地模型，故障转移
- 💬 **多轮对话** — 上下文管理、历史记录
- 📊 **Web API** — FastAPI RESTful 接口
- 🖥️ **CLI 工具** — 交互式对话、单次调用

## 快速开始

### 1. 配置 API Key

编辑 `config.yaml`：

```yaml
providers:
  deepseek:
    api_key: "sk-xxxxxxxx"  # 填入你的 API Key
```

或设置环境变量：

```bash
export AGENT_DEEPSEEK_KEY="sk-xxxxxxxx"
```

### 2. 使用 CLI

```bash
# 交互式对话
python cli.py chat

# 单次对话
python cli.py ask "现在几点了？"

# 列出工具
python cli.py tools

# 列出模型
python cli.py models

# 查看统计
python cli.py stats
```

### 3. 启动 Web 服务

```bash
python cli.py server --port 8000
```

API 接口：

```bash
# 对话
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "帮我算一下 123 * 456"}'

# 列出工具
curl http://localhost:8000/api/tools

# 列出模型
curl http://localhost:8000/api/models

# 统计
curl http://localhost:8000/api/stats
```

## 内置工具

| 工具 | 分类 | 说明 |
|------|------|------|
| `get_current_time` | 系统 | 获取当前时间 |
| `calculate` | 工具 | 数学计算 |
| `search_web` | 搜索 | 网页搜索（模拟） |
| `read_file` | 文件 | 读取文件 |
| `write_file` | 文件 | 写入文件 |
| `list_directory` | 文件 | 列出目录 |
| `python_execute` | 开发 | 执行 Python 代码 |

## 自定义工具

```python
from tool_registry import registry
from models import ToolParameter, ParameterType

@registry.register(
    name="my_tool",
    description="我的自定义工具",
    category="自定义",
    parameters=[
        ToolParameter(name="input", type=ParameterType.STRING, description="输入", required=True),
    ],
)
def my_tool(input: str) -> str:
    return f"处理结果: {input}"
```

## 项目结构

```
agent_platform/
├── cli.py              # CLI 入口
├── api.py              # FastAPI Web 服务
├── agent.py            # Agent 编排器（ReAct 循环）
├── tool_registry.py    # 工具注册中心
├── model_router.py     # 多模型路由器
├── models.py           # 数据模型
├── database.py         # SQLite 数据库
├── config.py           # 配置管理
├── config.yaml         # 配置文件
├── adapters/           # 模型适配器
│   └── __init__.py     # DeepSeek/OpenRouter/本地模型
└── data/               # 数据目录
```

## 技术栈

| 技术 | 用途 |
|------|------|
| FastAPI | Web 框架 |
| httpx | 异步 HTTP 客户端 |
| Pydantic | 数据校验 |
| SQLite | 数据存储 |
| Rich | 终端美化 |
