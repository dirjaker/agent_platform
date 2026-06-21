# 自定义工具开发指南

本文档介绍如何为 Agent Platform 开发自定义工具。

---

## 快速开始

在 `tools/` 目录下创建 `.py` 文件，使用 `@registry.register` 装饰器注册工具。启动时自动加载，无需手动注册。

## 基本示例

```python
from tool_registry import registry
from models import ToolParameter, ParameterType

@registry.register(
    name="my_tool",
    description="工具描述（模型通过这个理解工具用途）",
    category="自定义",
    parameters=[
        ToolParameter(
            name="input",
            type=ParameterType.STRING,
            description="参数说明",
            required=True,
        ),
    ],
)
def my_tool(input: str) -> str:
    """工具实现"""
    return f"结果: {input}"
```

## 参数类型

| 类型 | 枚举值 | Python 类型 | 说明 |
|------|--------|------------|------|
| 字符串 | `ParameterType.STRING` | `str` | 默认类型 |
| 整数 | `ParameterType.INTEGER` | `int` | 整数 |
| 浮点数 | `ParameterType.FLOAT` | `float` | 小数 |
| 布尔值 | `ParameterType.BOOLEAN` | `bool` | true/false |
| 数组 | `ParameterType.ARRAY` | `list` | 列表 |
| 对象 | `ParameterType.OBJECT` | `dict` | 字典 |

## 参数自动推断

如果不手动指定 `parameters`，注册中心会从函数签名自动推断：

```python
@registry.register(name="auto_tool", description="自动推断参数")
def auto_tool(name: str, count: int = 10) -> str:
    # 自动推断：name 为 STRING 必填，count 为 INTEGER 可选（默认值 10）
    return f"{name} x {count}"
```

## 支持异步

```python
@registry.register(name="async_tool", description="异步工具", category="网络")
async def async_tool(url: str) -> str:
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        return resp.text[:500]
```

## 返回值

工具返回值会被自动包装为 `ToolResult`：
- 成功：`ToolResult(success=True, data=返回值, execution_time=耗时)`
- 异常：`ToolResult(success=False, error=错误信息, execution_time=耗时)`

建议返回字符串、字典或列表等可 JSON 序列化的类型。

## 分类建议

| 分类 | 适用场景 | 示例 |
|------|---------|------|
| `系统` | 时间、计算等基础工具 | get_current_time |
| `文件` | 文件读写、目录操作 | read_file, write_file |
| `网络` | HTTP 请求、API 调用 | http_get, http_post |
| `数据` | JSON 处理、数据转换 | json_parse, json_query |
| `文本` | 文本分析、摘要、提取 | text_count, text_summarize |
| `搜索` | 搜索引擎、知识库查询 | search_web, knowledge_query |
| `开发` | 代码执行、调试 | python_execute |
| `工具` | 哈希、编码、时间转换 | hash_text, base64_encode |
| `自定义` | 用户自定义工具 | - |

## 工具自动加载

- 启动时自动加载 `tools/` 目录下所有 `.py` 文件
- 以 `_` 开头的文件会被跳过（如 `_utils.py`）
- 加载失败会输出警告日志，不影响其他工具
- 也可通过 `tool_loader.reload_tools()` 运行时重新加载

## 注意事项

1. **工具名唯一**：`name` 参数必须全局唯一，重复注册会覆盖
2. **描述要清晰**：模型通过 `description` 理解工具用途，写清楚工具做什么、什么时候用
3. **参数描述**：每个参数的 `description` 帮助模型理解如何填写
4. **超时控制**：默认 30 秒超时，可通过 `timeout` 参数调整
5. **安全性**：避免在工具中执行危险操作，文件操作建议限制路径范围
