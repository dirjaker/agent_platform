"""
Agent 平台 - macOS tkinter GUI 包装器

提供本地桌面窗口，内嵌 Web 视图连接到 FastAPI 后端。
"""

import sys
import threading
import tkinter as tk
from tkinter import ttk
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class AgentPlatformApp:
    """Agent 平台 macOS 桌面应用"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Agent 工具调用平台")
        self.root.geometry("900x640")
        self.root.configure(bg="#0d1117")
        self.root.minsize(800, 500)

        self.server_thread = None
        self.server_running = False
        self.port = 8003

        self._build_ui()

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#0d1117")
        style.configure("TLabel", background="#0d1117", foreground="#c9d1d9", font=("Helvetica", 12))
        style.configure("TButton", font=("Helvetica", 11))
        style.configure("Header.TLabel", font=("Helvetica", 18, "bold"), foreground="#58a6ff")
        style.configure("Status.TLabel", font=("Helvetica", 10), foreground="#8b949e")

        header = ttk.Frame(self.root)
        header.pack(fill=tk.X, padx=20, pady=(20, 10))
        ttk.Label(header, text="Agent 工具调用平台", style="Header.TLabel").pack(side=tk.LEFT)

        status_frame = ttk.Frame(self.root)
        status_frame.pack(fill=tk.X, padx=20, pady=(0, 10))
        self.status_var = tk.StringVar(value="服务未启动")
        ttk.Label(status_frame, textvariable=self.status_var, style="Status.TLabel").pack(side=tk.LEFT)

        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill=tk.X, padx=20, pady=(0, 10))

        self.btn_start = ttk.Button(btn_frame, text="启动 Web 服务", command=self._start_server)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_stop = ttk.Button(btn_frame, text="停止服务", command=self._stop_server, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_open = ttk.Button(btn_frame, text="打开浏览器", command=self._open_browser, state=tk.DISABLED)
        self.btn_open.pack(side=tk.LEFT)

        info_frame = ttk.Frame(self.root)
        info_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))

        info_text = tk.Text(
            info_frame, bg="#161b22", fg="#c9d1d9",
            font=("Menlo", 11), relief=tk.FLAT, padx=16, pady=16,
            insertbackground="#58a6ff", selectbackground="#264f78",
        )
        info_text.pack(fill=tk.BOTH, expand=True)
        info_text.insert(tk.END, self._get_welcome_text())
        info_text.config(state=tk.DISABLED)
        self.info_text = info_text

    def _get_welcome_text(self):
        return """Agent 工具调用平台
====================

功能说明:
  - ReAct 循环引擎（Think-Act-Observe）
  - 18+ 内置工具（文件/网络/数据/搜索/开发）
  - 多模型支持（DeepSeek / OpenRouter）
  - 真正的流式 SSE 输出
  - 工具调用日志记录
  - 插件化工具注册

使用方法:
  1. 点击「启动 Web 服务」开启后台 API
  2. 点击「打开浏览器」访问管理面板
  3. 通过面板管理对话、查看工具和统计

Web API 端口: {port}

命令行模式:
  python api.py              # 启动核心 API 服务（端口 8000）
""".format(port=self.port)

    def _start_server(self):
        if self.server_running:
            return

        def run():
            try:
                from src.web.app import run_server
                run_server(port=self.port)
            except Exception as e:
                self.root.after(0, lambda: self.status_var.set(f"服务启动失败: {e}"))

        self.server_thread = threading.Thread(target=run, daemon=True)
        self.server_thread.start()
        self.server_running = True

        self.status_var.set(f"服务运行中 -- http://localhost:{self.port}")
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.btn_open.config(state=tk.NORMAL)

        self.info_text.config(state=tk.NORMAL)
        self.info_text.insert(tk.END, f"\n[INFO] Web 服务已启动: http://localhost:{self.port}\n")
        self.info_text.see(tk.END)
        self.info_text.config(state=tk.DISABLED)

    def _stop_server(self):
        self.server_running = False
        self.status_var.set("服务已停止")
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.btn_open.config(state=tk.DISABLED)

    def _open_browser(self):
        import webbrowser
        webbrowser.open(f"http://localhost:{self.port}")

    def run(self):
        self.root.mainloop()


def main():
    app = AgentPlatformApp()
    app.run()


if __name__ == "__main__":
    main()
