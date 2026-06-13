#!/usr/bin/env python
"""启动 Web-to-Markdown 服务"""
import sys
import os

# 修复 Windows 终端中文乱码
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    # 设置环境变量确保子进程也使用 UTF-8
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# 修复 Windows 上 Playwright 的 NotImplementedError:
# asyncio.create_subprocess_exec 在默认 SelectorEventLoop 中不可用
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# 将项目根目录加入 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8080,
        reload=False,  # Windows reload 子进程不继承事件循环策略，导致 Playwright 报 NotImplementedError
        log_level="info",
    )
