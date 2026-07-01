"""配置管理"""

import yaml
import os
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

CONFIG_PATH = Path(__file__).parent / "config.yaml"

# 启动时加载 .env 文件
load_dotenv(Path(__file__).parent / ".env")

# 环境变量映射
ENV_MAPPINGS = {
    "AGENT_DEEPSEEK_KEY": ("providers", "deepseek", "api_key"),
    "AGENT_OPENROUTER_KEY": ("providers", "openrouter", "api_key"),
    "AGENT_DEFAULT_MODEL": ("default_model",),
    "AGENT_DB_PATH": ("database", "path"),
    "AGENT_HOST": ("server", "host"),
    "AGENT_PORT": ("server", "port"),
}

DEFAULT_CONFIG = {
    "providers": {
        "deepseek": {
            "api_key": "",
            "base_url": "https://api.deepseek.com",
            "models": ["deepseek-chat", "deepseek-v4-flash", "deepseek-v4-pro"],
        },
        "openrouter": {
            "api_key": "",
            "base_url": "https://openrouter.ai/api/v1",
            "models": ["google/gemini-2.0-flash-001"],
        },
    },
    "default_model": "deepseek-chat",
    "database": {
        "path": "data/agent.db",
    },
    "server": {
        "host": "0.0.0.0",
        "port": 8000,
    },
    "agent": {
        "max_steps": 10,
        "max_tokens": 4096,
        "temperature": 0.7,
        "system_prompt": "你是一个智能助手，可以通过调用工具来完成用户的任务。先理解用户意图，再选择合适的工具。",
    },
}


def _deep_get(d: dict, keys: tuple) -> Any:
    """深层获取字典值"""
    for key in keys:
        if isinstance(d, dict):
            d = d.get(key)
        else:
            return None
    return d


def _deep_set(d: dict, keys: tuple, value: Any):
    """深层设置字典值"""
    for key in keys[:-1]:
        d = d.setdefault(key, {})
    d[keys[-1]] = value


def load_config(config_path: str | None = None) -> dict:
    """加载配置（环境变量 > 配置文件 > 默认值）"""
    config = DEFAULT_CONFIG.copy()

    # 读取配置文件
    path = Path(config_path) if config_path else CONFIG_PATH
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            file_config = yaml.safe_load(f)
            if isinstance(file_config, dict):
                config = _merge(config, file_config)

    # 环境变量覆盖
    for env_key, config_path_tuple in ENV_MAPPINGS.items():
        env_val = os.environ.get(env_key)
        if env_val:
            _deep_set(config, config_path_tuple, env_val)

    return config


def _merge(base: dict, override: dict) -> dict:
    """深度合并字典"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def save_config(config: dict, config_path: str | None = None):
    """保存配置到文件"""
    path = Path(config_path) if config_path else CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
