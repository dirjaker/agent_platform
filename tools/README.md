# 自定义工具开发指南

## 创建工具文件

在 `tools/` 目录下创建 `.py` 文件，使用 `@registry.register` 装饰器注册工具。

## 示例

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

- `STRING` — 字符串
- `INTEGER` — 整数
- `FLOAT` — 浮点数
- `BOOLEAN` — 布尔值
- `ARRAY` — 数组
- `OBJECT` — 对象

## 支持异步

```python
@registry.register(name="async_tool", description="异步工具", category="网络")
async def async_tool(url: str) -> str:
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        return resp.text[:500]
```

## 工具自动加载

启动时自动加载 `tools/` 目录下所有 `.py` 文件（以 `_` 开头的除外）。
