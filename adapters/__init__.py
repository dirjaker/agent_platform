"""模型适配器 — 统一接口对接不同模型提供商"""

import httpx
import json
import logging
from abc import ABC, abstractmethod
from models import ModelResponse

logger = logging.getLogger(__name__)


class BaseModelAdapter(ABC):
    """模型适配器基类"""

    def __init__(self, name: str, api_key: str, base_url: str):
        self.name = name
        self.api_key = api_key
        self.base_url = base_url

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> ModelResponse:
        pass


class DeepSeekAdapter(BaseModelAdapter):
    """DeepSeek 模型适配器"""

    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com"):
        super().__init__("deepseek", api_key, base_url)

    async def chat(
        self,
        messages: list[dict],
        model: str = "deepseek-chat",
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> ModelResponse:
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json=body, headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]

        return ModelResponse(
            content=message.get("content"),
            tool_calls=message.get("tool_calls"),
            usage=data.get("usage", {}),
            model=data.get("model", model),
            finish_reason=choice.get("finish_reason", ""),
        )


class OpenRouterAdapter(BaseModelAdapter):
    """OpenRouter 模型适配器"""

    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api/v1"):
        super().__init__("openrouter", api_key, base_url)

    async def chat(
        self,
        messages: list[dict],
        model: str = "google/gemini-2.0-flash-001",
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> ModelResponse:
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://agent-platform.local",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json=body, headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]

        return ModelResponse(
            content=message.get("content"),
            tool_calls=message.get("tool_calls"),
            usage=data.get("usage", {}),
            model=data.get("model", model),
            finish_reason=choice.get("finish_reason", ""),
        )


class OpenAICompatibleAdapter(BaseModelAdapter):
    """通用 OpenAI 兼容适配器（适用于本地模型、vLLM 等）"""

    def __init__(self, name: str, api_key: str = "EMPTY", base_url: str = "http://localhost:8000"):
        super().__init__(name, api_key, base_url)

    async def chat(
        self,
        messages: list[dict],
        model: str = "default",
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> ModelResponse:
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.base_url}/v1/chat/completions",
                json=body, headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]

        return ModelResponse(
            content=message.get("content"),
            tool_calls=message.get("tool_calls"),
            usage=data.get("usage", {}),
            model=data.get("model", model),
            finish_reason=choice.get("finish_reason", ""),
        )
