"""工具热加载器 — 从 tools/ 目录动态加载工具模块"""

import importlib.util
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_tools_from_directory(tools_dir: str = None):
    """从目录加载所有工具模块"""
    if tools_dir is None:
        tools_dir = str(Path(__file__).parent / "tools")

    tools_path = Path(tools_dir)
    if not tools_path.exists():
        logger.warning(f"工具目录不存在: {tools_dir}")
        return []

    loaded = []
    for py_file in tools_path.glob("*.py"):
        if py_file.name.startswith("_"):
            continue

        module_name = f"tools.{py_file.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            loaded.append(py_file.name)
            logger.info(f"加载工具模块: {py_file.name}")
        except Exception as e:
            logger.error(f"加载工具模块 {py_file.name} 失败: {e}")

    logger.info(f"共加载 {len(loaded)} 个工具模块")
    return loaded


def reload_tools():
    """重新加载所有工具模块"""
    from tool_registry import registry
    # 清空现有工具（保留内置工具）
    registry._tools.clear()
    registry._executors.clear()
    # 重新加载
    load_tools_from_directory()
    return len(registry._tools)
