# 代码审查报告 — agent_platform

**项目路径:** `/home/dirjaker/myprojects/agent_platform`
**审查日期:** 2026-06-21
**文件数量:** 21 个 Python 文件
**项目类型:** FastAPI + SQLite 的 LLM Agent 应用平台

---

## 🔴 致命问题

### 1. CORS 完全开放
- **文件:** `api.py` 第 40 行, `src/web/app.py` 第 29-33 行
- **问题:** `allow_origins=["*"]` 允许任意来源访问 API，攻击者可从任何域名发起跨域请求，窃取用户数据或执行恶意操作。
- **修复:** 限制为前端实际部署域名，如 `allow_origins=["http://localhost:10002"]`，生产环境使用具体域名。

### 2. `exec()` 执行用户代码 — 任意代码执行漏洞
- **文件:** `workflow_engine.py` 第 191 行 (`CodeNodeExecutor.execute`), `tool_registry.py` 第 299 行 (`python_execute`)
- **问题:** 通过 `exec(code, {"__builtins__": safe_builtins}, local_ns)` 执行用户提交的 Python 代码。所谓的 "safe_builtins" 沙箱极其容易绕过（如通过 `__class__.__mro__` 获取 `object` 基类，再调用 `__subclasses__()` 获取 `os` 模块），等同于无沙箱。
- **修复:** 使用隔离的容器或进程执行用户代码（如 Docker、gVisor），或使用 RestrictedPython 等经过审计的沙箱库，绝不可在主进程内 exec。

### 3. `eval()` 计算数学表达式 — 潜在代码执行
- **文件:** `tool_registry.py` 第 194 行 (`calculate` 工具)
- **问题:** `eval(expression, {"__builtins__": {}}, allowed)` 虽然限制了 builtins，但通过 `getattr` 等内置函数仍可能逃逸沙箱。LLM 可被诱导构造恶意表达式。
- **修复:** 使用 `ast.literal_eval` 或专用数学表达式解析库（如 `simpleeval`、`numexpr`）。

### 4. 无任何身份认证和授权
- **文件:** `api.py` 全文, `src/web/app.py` 全文
- **问题:** 所有 API 端点（包括创建/删除应用、管理模型密钥、执行工作流）均无任何认证机制。任何人都可以访问并操作所有功能，包括读取存储的 API Key。
- **修复:** 添加 API Key / JWT Token 认证中间件，敏感操作需要管理员权限。

### 5. SSRF — 服务端请求伪造
- **文件:** `workflow_engine.py` 第 251-289 行 (`HTTPNodeExecutor`)
- **问题:** 工作流 HTTP 节点可向任意 URL 发起请求，攻击者可借此访问内网服务（如 `http://169.254.169.254` 获取云元数据、`http://localhost:8000` 访问自身 API）。
- **修复:** 实现 URL 白名单或黑名单机制，禁止访问内网 IP 和元数据地址。

---

## 🟡 警告问题

### 6. 文件操作工具无路径限制 — 路径穿越
- **文件:** `tool_registry.py` 第 226-250 行 (`read_file`, `write_file`)
- **问题:** `read_file` 和 `write_file` 工具接受任意路径参数，LLM 或攻击者可读取 `/etc/passwd`、写入任意文件（如 `~/.ssh/authorized_keys`）。
- **修复:** 限制文件操作在特定沙箱目录内，使用 `Path.resolve()` 检查路径前缀。

### 7. API 密钥明文存储
- **文件:** `config.yaml`, `config.py`, `database.py` 第 69-80 行 (`model_providers` 表)
- **问题:** API Key 以明文存储在配置文件和数据库中。`model_providers` 表的 `api_key` 字段也是明文。如果数据库文件泄露，所有 API 密钥随之暴露。
- **修复:** 使用环境变量或加密存储（如 `keyring` 库），数据库中的密钥应加密后存储。

### 8. 服务器绑定 0.0.0.0
- **文件:** `config.py` 第 39 行, `api.py` 第 699 行
- **问题:** 默认监听 `0.0.0.0`，暴露在所有网络接口上，在非容器环境中可能导致未授权访问。
- **修复:** 开发环境默认使用 `127.0.0.1`，生产环境通过配置指定。

### 9. 未处理的异常 — 工作流错误后继续执行
- **文件:** `workflow_engine.py` 第 472-479 行
- **问题:** 节点执行失败后仅记录日志但继续执行后续节点，可能导致级联错误或数据不一致。
- **修复:** 提供可配置的错误策略（fail-fast 或 continue），默认应为 fail-fast。

### 10. 死代码
- **文件:** `knowledge_manager.py` 第 299-302 行
- **问题:** `self.db.create_segment({...}) if False else None` 是永远不执行的死代码，影响可读性。
- **修复:** 删除死代码。

### 11. 嵌入式 HTML 过大
- **文件:** `api.py` 第 714-1600 行（约 900 行 HTML/CSS/JS）
- **问题:** 整个前端 HTML 内联在 Python 文件中，导致文件过大（67KB），难以维护和版本控制。
- **修复:** 将 HTML 提取到独立的 `static/index.html` 文件，通过 FastAPI 的 `StaticFiles` 或 `HTMLResponse` 提供服务。

### 12. requirements.txt 包含未使用的依赖
- **文件:** `requirements.txt`
- **问题:** 声明了 `numpy>=1.24.0` 和 `scikit-learn>=1.3.0`，但代码中使用的是自定义 `TFIDFVectorizer`，并未使用这两个库。
- **修复:** 删除未使用的依赖，减少攻击面和安装体积。

### 13. 模块级副作用
- **文件:** `tool_registry.py` 第 161 行, `api.py` 第 25 行
- **问题:** 模块导入时自动执行 `_auto_load_tools()` 和 `load_tools_from_directory()`，产生副作用，使单元测试困难。
- **修复:** 使用延迟初始化或显式初始化函数。

---

## 🔵 建议

### 14. CLI 重复导入
- **文件:** `cli.py` 第 10-11 行
- **问题:** `sys.path.insert(0, str(Path(__file__).parent))` 连续出现了两次。
- **修复:** 删除重复行。

### 15. 重复代码 — 流式对话逻辑
- **文件:** `api.py` 第 226-276 行 vs 第 403-467 行
- **问题:** `/api/chat/stream` 和 `/api/apps/{app_id}/chat` 的流式生成器逻辑高度相似（约 50 行），存在重复。
- **修复:** 提取公共的流式生成逻辑为独立函数。

### 16. 缺少请求频率限制
- **文件:** `api.py` 全文
- **问题:** 无 rate limiting，容易被 DDoS 或资源耗尽。
- **修复:** 使用 `slowapi` 或 Nginx 反向代理进行限流。

### 17. 依赖版本范围过宽
- **文件:** `requirements.txt`
- **问题:** 所有依赖使用 `>=` 范围，可能导致不同时间安装的版本不兼容。
- **修复:** 使用 `pip freeze` 生成精确版本锁定文件。

---

## 总结评分

| 维度 | 得分 | 说明 |
|------|------|------|
| **安全** | 3/10 | 存在任意代码执行、无认证、CORS 全开、SSRF 等多个致命漏洞 |
| **质量** | 6/10 | 代码结构清晰，有类型注解和文档字符串，但有死代码和重复逻辑 |
| **架构** | 5/10 | 模块划分合理，但 api.py 过大（1600行），嵌入式 HTML 不利于维护 |

**综合评分: 4.7/10**
