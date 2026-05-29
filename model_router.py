"""模型路由器 — 多模型调度、故障转移、支持流式输出"""

import logging
from typing import AsyncIterator
from adapters import (
    BaseModelAdapter, DeepSeekAdapter, OpenRouterAdapter, OpenAICompatibleAdapter
)
from models import ModelResponse

logger = logging.getLogger(__name__)


class ModelRouter:
    """多模型路由器"""

    def __init__(self, config: dict):
        self.adapters: dict[str, BaseModelAdapter] = {}
        self.model_map: dict[str, str] = {}  # model_name -> adapter_name
        self._init_adapters(config)

    def _init_adapters(self, config: dict):
        """初始化模型适配器"""
        providers = config.get("providers", {})

        # DeepSeek
        ds_cfg = providers.get("deepseek", {})
        if ds_cfg.get("api_key"):
            adapter = DeepSeekAdapter(ds_cfg["api_key"], ds_cfg.get("base_url", "https://api.deepseek.com"))
            self.adapters["deepseek"] = adapter
            for model in ds_cfg.get("models", []):
                self.model_map[model] = "deepseek"

        # OpenRouter
        or_cfg = providers.get("openrouter", {})
        if or_cfg.get("api_key"):
            adapter = OpenRouterAdapter(or_cfg["api_key"], or_cfg.get("base_url", "https://openrouter.ai/api/v1"))
            self.adapters["openrouter"] = adapter
            for model in or_cfg.get("models", []):
                self.model_map[model] = "openrouter"

        # 本地模型
        local_cfg = providers.get("local", {})
        if local_cfg.get("base_url"):
            adapter = OpenAICompatibleAdapter(
                "local", local_cfg.get("api_key", "EMPTY"), local_cfg["base_url"]
            )
            self.adapters["local"] = adapter
            for model in local_cfg.get("models", []):
                self.model_map[model] = "local"

        logger.info(f"已注册 {len(self.adapters)} 个模型适配器: {list(self.adapters.keys())}")

    def _get_adapter(self, model: str) -> tuple[str, BaseModelAdapter]:
        """查找模型对应的适配器"""
        adapter_name = self.model_map.get(model)
        if not adapter_name:
            # 尝试前缀匹配
            for registered_model, adapter_n in self.model_map.items():
                if model.startswith(registered_model.split("/")[0]):
                    adapter_name = adapter_n
                    break

        if not adapter_name or adapter_name not in self.adapters:
            # 回退到第一个可用的适配器
            if self.adapters:
                adapter_name = list(self.adapters.keys())[0]
                logger.warning(f"模型 {model} 未找到，回退到 {adapter_name}")
            else:
                raise RuntimeError("没有可用的模型适配器，请配置 API Key")

        return adapter_name, self.adapters[adapter_name]

    async def chat(
        self,
        messages: list[dict],
        model: str = "deepseek-chat",
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ModelResponse:
        """同步对话"""
        adapter_name, adapter = self._get_adapter(model)

        try:
            return await adapter.chat(
                messages=messages, model=model, tools=tools,
                temperature=temperature, max_tokens=max_tokens,
            )
        except Exception as e:
            logger.error(f"模型 {model} 调用失败: {e}")
            # 尝试故障转移到其他适配器
            for name, fallback_adapter in self.adapters.items():
                if name != adapter_name:
                    try:
                        logger.info(f"故障转移到 {name}")
                        return await fallback_adapter.chat(
                            messages=messages, model=model, tools=tools,
                            temperature=temperature, max_tokens=max_tokens,
                        )
                    except Exception:
                        continue
            raise RuntimeError(f"所有模型均不可用: {e}")

    async def chat_stream(
        self,
        messages: list[dict],
        model: str = "deepseek-chat",
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[dict]:
        """流式对话 — 返回 token 级别的流"""
        adapter_name, adapter = self._get_adapter(model)

        try:
            async for chunk in adapter.chat_stream(
                messages=messages, model=model, tools=tools,
                temperature=temperature, max_tokens=max_tokens,
            ):
                yield chunk
        except Exception as e:
            logger.error(f"模型 {model} 流式调用失败: {e}")
            # 故障转移：尝试其他适配器
            for name, fallback_adapter in self.adapters.items():
                if name != adapter_name:
                    try:
                        logger.info(f"故障转移到 {name}")
                        async for chunk in fallback_adapter.chat_stream(
                            messages=messages, model=model, tools=tools,
                            temperature=temperature, max_tokens=max_tokens,
                        ):
                            yield chunk
                        return
                    except Exception:
                        continue
            raise RuntimeError(f"所有模型均不可用: {e}")

    def list_models(self) -> list[dict]:
        """列出所有可用模型"""
        models = []
        for adapter_name, adapter in self.adapters.items():
            for model_name, adapter_n in self.model_map.items():
                if adapter_n == adapter_name:
                    models.append({"model": model_name, "provider": adapter_name})
        return models
