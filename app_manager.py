"""应用管理器 — 创建、编辑、删除应用，管理应用配置"""

import uuid
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class AppManager:
    """应用管理器"""

    def __init__(self, db):
        self.db = db

    def create_app(self, data: dict) -> dict:
        """创建应用"""
        app_id = str(uuid.uuid4())[:12]
        app = {
            "id": app_id,
            "name": data.get("name", "未命名应用"),
            "description": data.get("description", ""),
            "app_type": data.get("app_type", "chat"),
            "icon": data.get("icon", "🤖"),
            "model_config": data.get("model_config", {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "temperature": 0.7,
                "max_tokens": 4096,
            }),
            "system_prompt": data.get("system_prompt", "你是一个智能助手。"),
            "tool_ids": data.get("tool_ids", []),
            "dataset_ids": data.get("dataset_ids", []),
            "workflow_config": data.get("workflow_config", {}),
            "status": "active",
        }
        self.db.create_app(app)

        # 绑定数据集
        for ds_id in app["dataset_ids"]:
            try:
                self.db.bind_app_dataset(app_id, ds_id)
            except Exception as e:
                logger.warning(f"绑定数据集 {ds_id} 失败: {e}")

        logger.info(f"创建应用: {app['name']} ({app_id})")
        return self.db.get_app(app_id)

    def get_app(self, app_id: str) -> dict | None:
        """获取应用详情"""
        return self.db.get_app(app_id)

    def list_apps(self, limit: int = 100) -> list[dict]:
        """列出所有应用"""
        return self.db.list_apps(limit)

    def update_app(self, app_id: str, data: dict) -> dict | None:
        """更新应用"""
        app = self.db.get_app(app_id)
        if not app:
            return None

        # 处理数据集绑定
        if "dataset_ids" in data:
            new_ids = set(data["dataset_ids"])
            old_ids = set(app["dataset_ids"])
            for ds_id in new_ids - old_ids:
                try:
                    self.db.bind_app_dataset(app_id, ds_id)
                except Exception:
                    pass
            for ds_id in old_ids - new_ids:
                self.db.unbind_app_dataset(app_id, ds_id)

        self.db.update_app(app_id, data)
        return self.db.get_app(app_id)

    def delete_app(self, app_id: str) -> bool:
        """删除应用"""
        return self.db.delete_app(app_id)

    def get_app_with_context(self, app_id: str) -> dict | None:
        """获取应用及其关联的知识库信息"""
        app = self.db.get_app(app_id)
        if not app:
            return None
        app["datasets"] = self.db.get_app_datasets(app_id)
        return app

    def build_chat_config(self, app_id: str) -> dict | None:
        """构建聊天配置（用于 chat endpoint）"""
        app = self.db.get_app(app_id)
        if not app:
            return None
        datasets = self.db.get_app_datasets(app_id)
        return {
            "app": app,
            "model_config": app["model_config"],
            "system_prompt": app["system_prompt"],
            "tool_ids": app["tool_ids"],
            "datasets": datasets,
        }
