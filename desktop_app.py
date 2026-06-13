#!/usr/bin/env python
"""桌面应用启动器 - 使用 pywebview 将 Web 应用包装为原生桌面窗口

依赖 Windows Edge WebView2 Runtime（Win10+ 基本已预装）。
"""

import sys
import os
import threading
import time

# ---- Windows 兼容修复 ----
if sys.platform == "win32":
    # 修复终端中文乱码
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    # 修复 Playwright 的 NotImplementedError
    # 必须在任何 asyncio 操作前设置
    import asyncio as _asyncio
    _asyncio.set_event_loop_policy(_asyncio.WindowsProactorEventLoopPolicy())

# 将项目根目录加入 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import webview
import uvicorn
import urllib.request
import urllib.error

HOST = "127.0.0.1"
PORT = 8080
SERVER_URL = f"http://{HOST}:{PORT}"


def start_server():
    """在后台线程中启动 FastAPI 服务"""
    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
    )


def wait_for_server(timeout: float = 15.0):
    """轮询等待服务器就绪，超时则退出"""
    print(f"等待服务器启动 {SERVER_URL} ...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.urlopen(
                f"{SERVER_URL}/api/v1/health", timeout=1
            )
            if req.status == 200:
                print("服务器就绪")
                return True
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
            pass
        time.sleep(0.5)
    return False


def main():
    print("=" * 50)
    print("  Web to Markdown - 抓屏工具 (桌面版)")
    print("=" * 50)

    # 启动后端服务
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    if not wait_for_server():
        print("错误: 服务器启动超时，请检查端口是否被占用")
        sys.exit(1)

    print(f"打开桌面窗口 -> {SERVER_URL}")

    # 创建原生桌面窗口
    webview.create_window(
        title="Web to Markdown - 抓屏工具",
        url=SERVER_URL,
        width=1400,
        height=900,
        min_size=(900, 600),
        resizable=True,
        text_select=True,
        confirm_close=True,
    )

    webview.start(gui="edgechromium")
    print("应用已退出")


if __name__ == "__main__":
    main()
