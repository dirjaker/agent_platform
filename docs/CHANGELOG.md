# 更新日志

本文件记录 Agent Platform 的版本更新历史。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

---

## [v3.0.0] — 2026-06-21

### 🎉 重大更新：重构为类 Dify LLM 应用平台

#### 新增

- **应用管理系统**（AppManager）
  - 创建/编辑/删除应用，支持聊天助手和工作流两种类型
  - 每个应用独立配置：系统提示词、模型、工具、知识库
  - 应用对话接口（`POST /api/apps/{id}/chat`），集成 RAG 知识库检索

- **知识库 RAG**（KnowledgeManager）
  - 文档上传与自动分块（TextChunker，支持段落/句子分割和重叠）
  - TF-IDF 向量化器（中英文混合分词，L2 归一化）
  - 余弦相似度语义搜索，支持跨数据集检索
  - 完整的知识库/文档/分块 CRUD API

- **DAG 工作流引擎**（WorkflowEngine）
  - 基于拓扑排序的 DAG 执行引擎
  - 变量插值系统（`{{node_id.field}}` 语法）
  - 6 种节点执行器：LLM、代码执行、条件分支、HTTP 请求、工具调用、知识检索
  - 工作流运行记录和节点执行日志

- **多模型提供商管理**
  - 数据库级模型提供商 CRUD（`/api/models/providers`）
  - 模型连接测试功能（`POST /api/models/test`）

- **自定义工具管理**
  - 数据库级自定义工具 CRUD（`/api/tools/custom`）
  - 支持 API 类型和代码类型工具

- **Dify 风格前端界面**
  - Vue 3 CDN 单页应用（内嵌 api.py）
  - 应用管理、对话 Playground、知识库、工具、模型 5 个页面
  - D3.js 工作流 SVG 画布
  - 5 套主题系统（默认/暗黑/暖色/薄荷/紫罗兰）实时切换
  - CSS 变量实现主题，支持暗色模式

- **数据库扩展**
  - 新增 8 张数据表：apps、datasets、documents、segments、workflow_runs、model_providers、tools、app_datasets
  - 完整的 CRUD 操作封装

#### 变更

- API 版本升级至 v3.0.0
- 数据库表结构全面扩展（12 张表）
- 前端从简单 HTML 升级为 Vue 3 SPA

#### 安全

- CORS 限制为本地访问（`http://localhost,http://127.0.0.1`）
- `exec()`/`eval()` 沙箱保护：禁止 import、dunder 访问、危险内置函数
- 代码审查报告（REVIEW.md）记录已知安全问题

---

## [v2.0.0] — 2026-06-15

### 🎉 重大更新：真正的流式输出 + Web UI 升级

#### 新增

- **真正的流式 SSE 输出**
  - DeepSeek 适配器实现逐 Token 流式输出（`chat_stream`）
  - OpenRouter 适配器实现逐 Token 流式输出
  - Agent 编排器 `run_stream()` 方法支持流式 ReAct 循环
  - SSE 事件类型：token、tool_call、tool_result、done

- **Agent 编排器增强**
  - 自动对话标题生成（`generate_title`）
  - 工具调用日志记录（数据库持久化）
  - 系统提示词模板（自动包含可用工具列表）

- **多模型路由器**（ModelRouter）
  - 模型自动发现和路由
  - 故障转移机制（主模型失败自动切换备选）
  - 前缀匹配回退

- **模型适配器系统**（adapters/）
  - BaseModelAdapter 抽象基类
  - DeepSeekAdapter（支持真正的流式 SSE）
  - OpenRouterAdapter（支持真正的流式 SSE）
  - OpenAICompatibleAdapter（vLLM/llama.cpp 等本地模型）

- **扩展工具集**（tools/builtin_tools.py）
  - HTTP 工具：http_get、http_post
  - JSON 工具：json_parse、json_query
  - 文本工具：text_count、text_extract、text_summarize
  - 加密工具：hash_text、base64_encode
  - 时间工具：timestamp_convert
  - 知识库工具：knowledge_query

- **工具热加载**（tool_loader.py）
  - 自动扫描 tools/ 目录加载工具模块
  - 支持运行时重新加载

- **Web API 升级**
  - 流式对话接口（`POST /api/chat/stream`）
  - 对话管理 API（列表、详情、删除）
  - 工具列表 API、模型列表 API、统计 API
  - 工具调用日志 API

#### 变更

- API 版本升级至 v2.0.0
- 配置系统重构：支持环境变量覆盖
- 数据库新增 tool_logs 表

---

## [v1.0.0] — 2026-06-10

### 🎉 首个正式版本

#### 新增

- **Agent 核心引擎**
  - ReAct 循环实现（Think-Act-Observe）
  - Function Calling 协议支持（OpenAI 格式）
  - 多轮对话状态管理

- **工具注册系统**
  - ToolRegistry 工具注册中心
  - 装饰器注册模式（`@registry.register`）
  - 自动参数推断（从函数签名）
  - OpenAI Function Calling 格式转换

- **内置工具**
  - get_current_time：获取当前时间
  - calculate：数学表达式计算
  - search_web：网页搜索（模拟）
  - read_file / write_file / list_directory：文件操作
  - python_execute：安全 Python 代码执行

- **CLI 工具**（cli.py）
  - 交互式对话（Rich 终端美化）
  - 单次提问
  - 工具/模型/统计查看

- **Web 服务**（api.py）
  - FastAPI REST API
  - 同步对话接口
  - 简单 HTML 前端

- **数据库**（database.py）
  - SQLite 存储
  - conversations、messages、tool_logs 表

- **配置系统**（config.py）
  - YAML 配置文件
  - 默认配置

- **数据模型**（models.py）
  - Pydantic v2 数据模型
  - ToolDefinition、ToolParameter、ToolResult
  - Message、Conversation、ModelResponse

---

## [v0.1.0] — 2026-06-01

### 新增

- 项目初始化
- README.md 和技术文档
- SVG Banner 设计
- MIT License
- 基础项目结构
