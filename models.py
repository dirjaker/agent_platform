"""数据模型定义"""

from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum
from typing import Any
import uuid


class ParameterType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


class ToolParameter(BaseModel):
    """工具参数定义"""
    name: str
    type: ParameterType = ParameterType.STRING
    description: str = ""
    required: bool = False
    default: Any = None
    enum: list[str] | None = None


class ToolDefinition(BaseModel):
    """工具完整定义"""
    name: str
    display_name: str = ""
    description: str
    category: str = "general"
    parameters: list[ToolParameter] = []
    return_type: str = "Any"
    examples: list[dict] = []
    tags: list[str] = []
    version: str = "1.0.0"
    enabled: bool = True
    rate_limit: int | None = None
    timeout: int = 30


class ToolResult(BaseModel):
    """工具执行结果"""
    success: bool
    data: Any = None
    error: str | None = None
    execution_time: float = 0.0
    metadata: dict = {}


class Message(BaseModel):
    """对话消息"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    role: str  # user | assistant | system | tool
    content: str
    tool_call: dict | None = None
    tool_result: dict | None = None
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: dict = {}


class Conversation(BaseModel):
    """对话"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    messages: list[Message] = []
    model: str = "deepseek-chat"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    total_tokens: int = 0
    total_cost: float = 0.0


class ModelResponse(BaseModel):
    """模型响应"""
    content: str | None = None
    tool_calls: list[dict] | None = None
    usage: dict = {}
    model: str = ""
    finish_reason: str = ""
