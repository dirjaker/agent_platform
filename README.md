<div align="center">

# 🤖 Agent Platform

### Python AI Agent 开发平台

[![模块](https://img.shields.io/badge/模块-7-blue?style=flat-square)]()
[![工具](https://img.shields.io/badge/工具-10+-green?style=flat-square)]()
[![框架](https://img.shields.io/badge/框架-FastAPI-orange?style=flat-square)]()
[![更新](https://img.shields.io/badge/更新-2025.06-red?style=flat-square)]()

*模块化 AI Agent 平台 · 工具注册 · 技能系统 · 多模型支持*

</div>

---

# Agent 工具调用平台 v2.0

一个支持自定义工具注册、多模型调度、多轮对话的智能 Agent 平台。

## ✨ 特性

- 🛠️ **18 个内置工具** — 系统、文件、网络、数据、搜索、开发
- 🔄 **真正的流式输出** — token 级别的 SSE 流式响应
- 🤖 **多模型支持** — DeepSeek、OpenRouter、本地模型，自动故障转移
- 📊 **工具调用日志** — 记录每次调用的参数、结果、耗时
- 🎨 **现代 Web UI** — 支持 Markdown 渲染、代码高亮、流式动画
- 🔌 **插件化架构** — 装饰器注册工具，支持热加载

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key
# 编辑 config.yaml，填入你的 API Key

# 3. 启动服务
python api.py

# 4. 访问 Web UI
# http://localhost:8000
```

## 📖 API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/chat` | POST | 同步对话 |
| `/api/chat/stream` | POST | 流式对话（SSE） |
| `/api/tools` | GET | 列出所有工具 |
| `/api/models` | GET | 列出可用模型 |
| `/api/conversations` | GET | 列出对话历史 |
| `/api/stats` | GET | 获取统计信息 |
| `/api/tool-logs/{conv_id}` | GET | 获取工具调用日志 |

## 🛠️ 内置工具

### 系统工具
- `get_current_time` — 获取当前时间
- `calculate` — 数学计算

### 文件工具
- `read_file` — 读取文件
- `write_file` — 写入文件
- `list_directory` — 列出目录

### 网络工具
- `http_get` — HTTP GET 请求
- `http_post` — HTTP POST 请求

### 数据工具
- `json_parse` — JSON 解析
- `json_query` — JSON 字段提取
- `text_count` — 文本统计
- `text_extract` — 正则提取
- `text_summarize` — 文本摘要

### 工具类
- `hash_text` — 哈希计算（MD5/SHA256）
- `base64_encode` — Base64 编解码
- `timestamp_convert` — 时间戳转换

### 搜索工具
- `search_web` — 网页搜索
- `knowledge_query` — 知识库查询

### 开发工具
- `python_execute` — Python 代码执行（沙箱）

## 🏗️ 架构

```
用户层 (Web UI / API / CLI)
    ↓
网关层 (CORS / 路由 / 日志)
    ↓
Agent 核心层
    ├── ReAct 循环引擎
    ├── 流式输出引擎
    ├── 工具调度器
    └── 模型路由器
    ↓
服务层
    ├── 模型服务 (DeepSeek / OpenRouter / 本地)
    ├── 工具服务 (18 个内置工具)
    └── 存储服务 (SQLite)
```

## 📝 技术文档

详细技术设计请参考 [技术文档](../project_list/03-AI-Agent工具调用平台-技术文档.md)

## 📄 License

MIT

