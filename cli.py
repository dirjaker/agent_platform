#!/usr/bin/env python3
"""Agent 工具调用平台 — CLI 入口"""

import asyncio
import json
import sys
from pathlib import Path

# 确保在项目目录
sys.path.insert(0, str(Path(__file__).parent))

from config import load_config
from models import Conversation
from database import AgentDatabase
from tool_registry import registry
from model_router import ModelRouter
from agent import AgentOrchestrator


async def chat_interactive(config: dict):
    """交互式对话"""
    from rich.console import Console
    from rich.panel import Panel
    from rich.markdown import Markdown

    console = Console()
    db = AgentDatabase(config["database"]["path"])
    router = ModelRouter(config)
    orchestrator = AgentOrchestrator(router, registry, config)
    model = config.get("default_model", "deepseek-chat")

    console.print(Panel("[bold blue]🤖 Agent 工具调用平台[/bold blue]", subtitle="输入 'quit' 退出"))
    console.print(f"[dim]模型: {model} | 工具: {len(registry.list_tools())} 个[/dim]\n")

    # 创建对话
    conv = Conversation(title="交互式对话", model=model)
    db.create_conversation(conv)
    history = []

    while True:
        try:
            user_input = console.input("[bold green]你: [/bold green]").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            break

        # 执行 Agent
        with console.status("[bold blue]思考中...[/bold blue]"):
            messages = await orchestrator.run(
                user_input=user_input,
                conversation_id=conv.id,
                model=model,
                history=history,
            )

        # 输出结果
        for msg in messages:
            if msg.role == "assistant" and msg.content:
                console.print(Panel(Markdown(msg.content), title="🤖 助手", border_style="blue"))
            elif msg.role == "tool":
                tool_result = msg.tool_result or {}
                tool_name = tool_result.get("name", "unknown")
                success = tool_result.get("result", {}).get("success", False)
                status = "✅" if success else "❌"
                console.print(f"[dim]{status} 工具 {tool_name}[/dim]")

            # 保存消息
            db.save_message(conv.id, msg)

        history.extend(messages)
        console.print()


async def chat_once(config: dict, message: str, model: str = None):
    """单次对话"""
    db = AgentDatabase(config["database"]["path"])
    router = ModelRouter(config)
    orchestrator = AgentOrchestrator(router, registry, config)
    model = model or config.get("default_model", "deepseek-chat")

    conv = Conversation(title=message[:50], model=model)
    db.create_conversation(conv)

    messages = await orchestrator.run(
        user_input=message,
        conversation_id=conv.id,
        model=model,
    )

    for msg in messages:
        if msg.role == "assistant" and msg.content:
            print(msg.content)
        db.save_message(conv.id, msg)


def cmd_tools(config: dict):
    """列出所有工具"""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    tools = registry.list_tools()

    table = Table(title="🔧 可用工具")
    table.add_column("名称", style="cyan")
    table.add_column("分类", style="green")
    table.add_column("描述")

    for t in tools:
        table.add_row(t.name, t.category, t.description[:60])

    console.print(table)


def cmd_models(config: dict):
    """列出可用模型"""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    router = ModelRouter(config)
    models = router.list_models()

    table = Table(title="🤖 可用模型")
    table.add_column("模型", style="cyan")
    table.add_column("提供商", style="green")

    for m in models:
        table.add_row(m["model"], m["provider"])

    if not models:
        console.print("[yellow]未配置任何模型，请在 config.yaml 中设置 API Key[/yellow]")
    else:
        console.print(table)


def cmd_stats(config: dict):
    """查看统计"""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    db = AgentDatabase(config["database"]["path"])
    stats = db.get_stats()

    table = Table(title="📊 统计信息")
    table.add_column("指标", style="bold")
    table.add_column("数值")

    for k, v in stats.items():
        table.add_row(k, str(v))

    console.print(table)


def cmd_server(config: dict, host: str = None, port: int = None):
    """启动 Web 服务"""
    from api import run_server
    host = host or config.get("server", {}).get("host", "0.0.0.0")
    port = port or config.get("server", {}).get("port", 8000)
    print(f"🚀 启动 Agent 服务: http://{host}:{port}")
    run_server(host, port)


def cmd_help():
    """显示帮助"""
    print("""
🤖 Agent 工具调用平台

用法: python cli.py <command> [options]

命令:
  chat              交互式对话
  ask <message>     单次对话
  tools             列出可用工具
  models            列出可用模型
  stats             查看统计
  server            启动 Web 服务
  help              显示帮助

示例:
  python cli.py chat
  python cli.py ask "现在几点了？"
  python cli.py ask "帮我算一下 123 * 456"
  python cli.py tools
  python cli.py server --port 8000
""")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Agent 工具调用平台")
    subparsers = parser.add_subparsers(dest="command")

    # chat
    subparsers.add_parser("chat", help="交互式对话")

    # ask
    ask_parser = subparsers.add_parser("ask", help="单次对话")
    ask_parser.add_argument("message", help="对话内容")
    ask_parser.add_argument("--model", help="指定模型")

    # tools
    subparsers.add_parser("tools", help="列出工具")

    # models
    subparsers.add_parser("models", help="列出模型")

    # stats
    subparsers.add_parser("stats", help="查看统计")

    # server
    server_parser = subparsers.add_parser("server", help="启动服务")
    server_parser.add_argument("--host", help="监听地址")
    server_parser.add_argument("--port", type=int, help="监听端口")

    # help
    subparsers.add_parser("help", help="显示帮助")

    args = parser.parse_args()
    config = load_config()

    if not args.command or args.command == "help":
        cmd_help()
    elif args.command == "chat":
        asyncio.run(chat_interactive(config))
    elif args.command == "ask":
        asyncio.run(chat_once(config, args.message, args.model))
    elif args.command == "tools":
        cmd_tools(config)
    elif args.command == "models":
        cmd_models(config)
    elif args.command == "stats":
        cmd_stats(config)
    elif args.command == "server":
        cmd_server(config, args.host, args.port)
    else:
        cmd_help()


if __name__ == "__main__":
    main()
