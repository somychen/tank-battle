from __future__ import annotations

# 修复 Windows 上 Playwright 的 NotImplementedError
# 必须在任何 asyncio 操作前设置，uvicorn reload 子进程不会执行 run.py
import sys as _sys
if _sys.platform == "win32":
    import asyncio as _asyncio
    _asyncio.set_event_loop_policy(_asyncio.WindowsProactorEventLoopPolicy())

import os
import re
from pathlib import Path

from fastapi import FastAPI, Depends, Query, HTTPException
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.models import (
    PasteConvertRequest,
    ConvertRequest, ConvertResponse, ConvertData,
    ErrorResponse, ErrorDetail, HealthResponse,
    ConvertOptions,
    OutlineConvertRequest, OutlineConvertResponse, OutlineConvertData, OutlineConvertOptions,
    FileInfo, FileListResponse, ExportPdfRequest,
    ChatMessageModel, ChatSessionItem, ChatSessionDetail,
    CreateSessionRequest, UpdateSessionRequest, CompressRequest,
)
from app.exceptions import AppException, MissingURLError
from app.utils import validate_url
from app.services.fetcher import Fetcher
from app.services.extractor import Extractor, ExtractOptions
from app.services.converter import Converter
from app.services.storage import Storage
from app.services.image_downloader import ImageDownloader
from app.services.outline_crawler import OutlineCrawler
from app.services.browser_fetcher import BrowserFetcher
from app.services.tencent_doc_extractor import TencentDocExtractor
from app.services.ai.manager import AIManager, get_ai_manager
from app.services.ai.base import ChatMessage
from app.services.ai.chat_history import get_chat_history_manager, estimate_tokens
from app.services.pdf_export import markdown_to_print_html

# ---- 创建应用 ----
app = FastAPI(
    title="Web-to-Markdown",
    description="将网页链接转换为 Markdown 文件的 API 服务",
    version="1.0.0",
)

# 静态文件服务（marked.js 等）
_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# ---- 全局异常处理器 ----
@app.exception_handler(AppException)
async def app_exception_handler(request, exc: AppException):
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=ErrorDetail(
                code=exc.code,
                message=exc.message,
                detail=exc.detail,
            )
        ).model_dump(),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=ErrorDetail(
                code="HTTP_ERROR",
                message=exc.detail or "请求错误",
                detail=str(exc.detail),
            )
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error=ErrorDetail(
                code="INTERNAL_ERROR",
                message="服务器内部错误",
                detail=f"{type(exc).__name__}: {str(exc)[:450]}",
            )
        ).model_dump(),
    )


# ---- 服务实例（单例） ----
_fetcher = Fetcher()
_extractor = Extractor()
_converter = Converter()
_storage = Storage()
_image_downloader = ImageDownloader()
_outline_crawler = OutlineCrawler()
_browser_fetcher = BrowserFetcher()
_tencent_extractor = TencentDocExtractor()


def get_fetcher() -> Fetcher:
    return _fetcher


def get_extractor() -> Extractor:
    return _extractor


def get_converter() -> Converter:
    return _converter


def get_storage() -> Storage:
    return _storage


def get_image_downloader() -> ImageDownloader:
    return _image_downloader


def get_outline_crawler() -> OutlineCrawler:
    return _outline_crawler


def get_browser_fetcher() -> BrowserFetcher:
    return _browser_fetcher


# ---- 启动/关闭事件 ----
@app.on_event("startup")
async def startup():
    os.makedirs(settings.output_dir, exist_ok=True)


@app.on_event("shutdown")
async def shutdown():
    await _browser_fetcher.close()


# ---- 前端页面 ----

@app.get("/favicon.ico")
async def favicon():
    """返回一个简单的 favicon 图标，避免 404"""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="4" fill="#2563eb"/>'
        '<text x="16" y="22" text-anchor="middle" font-size="18" font-family="Arial" fill="white" font-weight="bold">M</text>'
        '</svg>'
    )
    from fastapi.responses import Response
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/", response_class=HTMLResponse)
async def index():
    """前端界面"""
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Web to Markdown</title>
<style>
  :root {
    --bg: #f5f5f5;
    --card-bg: #fff;
    --text: #333;
    --text-secondary: #666;
    --border: #e0e0e0;
    --primary: #2563eb;
    --primary-hover: #1d4ed8;
    --success: #16a34a;
    --error: #dc2626;
    --warning: #f59e0b;
    --radius: 10px;
    --panel-bg: #fafbfc;
    --panel-header-bg: #f3f4f6;
    --panel-border: #e5e7eb;
    --highlight-bg: #eff6ff;
    --highlight-border: #3b82f6;
    --text-muted: #9ca3af;
    --recent-bg: #f0fdf4;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    height: 100vh;
    overflow: hidden;
  }

  /* Three-panel layout */
  .app-layout { display: flex; height: 100vh; overflow: hidden; }

  .panel {
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    border-right: 1px solid var(--panel-border);
  }
  .panel-left  { width: 300px; min-width: 220px; max-width: 500px; background: var(--panel-bg); flex-shrink: 0; }
  .panel-middle { width: 280px; min-width: 200px; max-width: 450px; background: var(--card-bg); flex-shrink: 0; }
  .panel-right { flex: 1; min-width: 300px; background: var(--card-bg); border-right: none; display: flex; flex-direction: row; overflow: hidden; }
  .panel-right .preview-pane { flex: 1; min-width: 200px; max-width: none; display: flex; flex-direction: column; overflow: hidden; }
  .panel-right .ai-chat-pane { width: 420px; min-width: 200px; max-width: 800px; flex-shrink: 0; display: flex; flex-direction: column; border-left: 1px solid var(--panel-border); background: var(--panel-bg); }
  .ai-chat-pane .panel-header { border-bottom: 1px solid var(--panel-border); }
  .ai-chat-pane .panel-body { display: flex; flex-direction: column; padding: 0; }

  /* Drag handle between panels */
  .drag-handle {
    width: 7px;
    cursor: col-resize;
    background: var(--panel-border);
    flex-shrink: 0;
    position: relative;
    transition: background .15s;
    z-index: 10;
  }
  .drag-handle:hover,
  .drag-handle.active {
    background: var(--primary);
  }
  .drag-handle::after {
    content: '';
    position: absolute;
    inset: 0 -6px;
  }

  .panel-header {
    padding: 14px 16px;
    background: var(--panel-header-bg);
    border-bottom: 1px solid var(--panel-border);
    font-weight: 700;
    font-size: 14px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-shrink: 0;
  }
  .panel-body {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
  }

  /* Left panel specific */
  .app-title {
    font-size: 20px;
    font-weight: 700;
    margin-bottom: 2px;
  }
  .app-subtitle {
    font-size: 12px;
    color: var(--text-secondary);
    margin-bottom: 16px;
  }

  .input-group {
    margin-bottom: 14px;
  }
  .input-group label {
    display: block;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
    margin-bottom: 4px;
  }

  input[type="url"], input[type="text"], input[type="number"] {
    width: 100%;
    padding: 8px 10px;
    border: 1.5px solid var(--border);
    border-radius: 6px;
    font-size: 13px;
    outline: none;
    transition: border .2s;
  }
  input[type="url"]:focus, input[type="text"]:focus, input[type="number"]:focus {
    border-color: var(--primary);
  }

  .btn-row {
    display: flex;
    gap: 6px;
  }
  .btn {
    padding: 8px 12px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: all .2s;
    background: var(--card-bg);
    color: var(--text);
    white-space: nowrap;
  }
  .btn:hover { background: var(--panel-header-bg); }
  .btn-primary {
    background: var(--primary);
    color: #fff;
    border-color: var(--primary);
  }
  .btn-primary:hover { background: var(--primary-hover); }
  .btn-large {
    padding: 10px 16px;
    font-size: 14px;
  }

  .checkbox-group {
    margin-bottom: 12px;
  }
  .checkbox-group .chk-item {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 4px;
    font-size: 12px;
  }
  .checkbox-group .chk-item input[type="checkbox"] {
    width: 15px;
    height: 15px;
    accent-color: var(--primary);
    flex-shrink: 0;
  }

  .note {
    font-size: 11px;
    color: var(--text-muted);
    margin-bottom: 12px;
    line-height: 1.5;
  }

  .mode-selector {
    display: flex;
    gap: 0;
    margin-bottom: 12px;
    border-radius: 6px;
    overflow: hidden;
    border: 1.5px solid var(--border);
  }
  .mode-option {
    flex: 1;
    text-align: center;
    padding: 8px 6px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    background: var(--card-bg);
    color: var(--text-secondary);
    transition: all .2s;
    border: none;
    outline: none;
  }
  .mode-option:first-child { border-right: 1.5px solid var(--border); }
  .mode-option.active {
    background: var(--primary);
    color: #fff;
  }

  .recent-section {
    margin-top: auto;
    border-top: 1px solid var(--panel-border);
    padding: 12px 16px;
    background: var(--recent-bg);
  }
  .recent-section .recent-title {
    font-size: 12px;
    font-weight: 700;
    color: var(--success);
    margin-bottom: 8px;
  }
  .recent-item {
    font-size: 11px;
    color: var(--text-secondary);
    padding: 4px 0;
    border-bottom: 1px solid var(--panel-border);
    cursor: pointer;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .recent-item:last-child { border-bottom: none; }
  .recent-item:hover { color: var(--primary); }

  /* Middle panel - file list */
  .file-item {
    display: flex;
    flex-direction: column;
    padding: 10px 12px;
    border-bottom: 1px solid var(--panel-border);
    cursor: pointer;
    transition: background .15s;
  }
  .file-item:hover { background: var(--panel-header-bg); }
  .file-item.active {
    background: var(--highlight-bg);
    border-left: 3px solid var(--highlight-border);
    padding-left: 9px;
  }
  .file-item .file-name {
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .file-item .file-meta {
    font-size: 11px;
    color: var(--text-muted);
    margin-top: 2px;
  }
  .empty-state {
    text-align: center;
    color: var(--text-muted);
    padding: 40px 16px;
    font-size: 13px;
  }

  /* Right panel - markdown preview */
  .markdown-body {
    padding: 20px 24px;
    line-height: 1.75;
    font-size: 14px;
  }
  .markdown-body h1 { font-size: 28px; margin-bottom: 12px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
  .markdown-body h2 { font-size: 22px; margin: 20px 0 10px; }
  .markdown-body h3 { font-size: 18px; margin: 16px 0 8px; }
  .markdown-body h4 { font-size: 15px; margin: 12px 0 6px; }
  .markdown-body p { margin: 10px 0; }
  .markdown-body code {
    background: #f3f3f3;
    padding: 2px 6px;
    border-radius: 3px;
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    font-size: 13px;
  }
  .markdown-body pre {
    background: #2d2d2d;
    color: #f8f8f2;
    padding: 16px;
    border-radius: 6px;
    overflow-x: auto;
    margin: 12px 0;
  }
  .markdown-body pre code {
    background: transparent;
    padding: 0;
    color: inherit;
  }

  /* Raw markdown source view */
  .markdown-source {
    padding: 16px 24px;
    margin: 0;
    white-space: pre-wrap;
    word-break: break-all;
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    font-size: 13px;
    line-height: 1.6;
    color: var(--text-primary);
    background: var(--bg);
    overflow-x: auto;
    max-height: 100%;
  }

  /* Toggle source button active state */
  .btn-toggle-source.active {
    background: var(--primary);
    color: #fff;
  }
  .markdown-body blockquote {
    border-left: 4px solid var(--primary);
    padding: 4px 16px;
    margin: 12px 0;
    color: var(--text-secondary);
    background: var(--panel-bg);
    border-radius: 0 4px 4px 0;
  }
  .markdown-body table {
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
  }
  .markdown-body th, .markdown-body td {
    border: 1px solid var(--border);
    padding: 8px 12px;
    text-align: left;
    font-size: 13px;
  }
  .markdown-body th {
    background: var(--panel-header-bg);
    font-weight: 600;
  }
  .markdown-body ul, .markdown-body ol { padding-left: 24px; margin: 8px 0; }
  .markdown-body a { color: var(--primary); text-decoration: none; }
  .markdown-body a:hover { text-decoration: underline; }
  .markdown-body img { max-width: 100%; border-radius: 4px; }

  .placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--text-muted);
    font-size: 15px;
    text-align: center;
    padding: 40px;
  }

  /* Loading spinner */
  .spinner {
    display: none;
    width: 18px;
    height: 18px;
    border: 2px solid #fff;
    border-top-color: transparent;
    border-radius: 50%;
    animation: spin .6s linear infinite;
    margin: 0 auto;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  button.loading .btn-text { display: none; }
  button.loading .spinner { display: block; }

  .cookie-group {
    display: none;
    background: #fffbe6;
    border: 1px solid #ffe58f;
    border-radius: 6px;
    padding: 10px;
    margin-bottom: 12px;
  }
  .cookie-group.show { display: block; }
  .cookie-group label {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
    margin-bottom: 4px;
  }
  .cookie-group input[type="text"] {
    font-family: monospace;
    font-size: 12px;
  }

  .btn-secondary {
    padding: 6px 10px;
    font-size: 11px;
    font-weight: 500;
    border: 1px solid var(--border);
    border-radius: 6px;
    cursor: pointer;
    transition: all .2s;
    background: transparent;
    color: var(--text-secondary);
    white-space: nowrap;
  }
  .btn-secondary:hover { background: var(--panel-header-bg); color: var(--text); }

  .action-divider {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 10px 0;
    color: var(--text-muted);
    font-size: 10px;
  }
  .action-divider::before,
  .action-divider::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border);
  }

  .collapsible-section {
    border: 1px solid var(--border);
    border-radius: 6px;
    margin-bottom: 12px;
    overflow: hidden;
  }
  .collapsible-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 12px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    user-select: none;
    color: var(--text-secondary);
    background: var(--panel-header-bg);
    transition: background .15s;
  }
  .collapsible-header:hover { background: var(--border); }
  .collapsible-header .arrow {
    font-size: 10px;
    transition: transform .2s;
    color: var(--text-muted);
  }
  .collapsible-section.open .collapsible-header .arrow { transform: rotate(90deg); }
  .collapsible-body {
    display: none;
    padding: 10px 12px;
  }
  .collapsible-section.open .collapsible-body { display: block; }

  .hidden { display: none !important; }

  /* AI Chat Pane */
  .ai-chat-messages {
    flex: 1;
    overflow-y: auto;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .ai-chat-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--text-muted);
    font-size: 14px;
    text-align: center;
    padding: 20px;
  }
  .ai-chat-msg {
    max-width: 90%;
    padding: 10px 14px;
    border-radius: 10px;
    font-size: 13px;
    line-height: 1.6;
    word-break: break-word;
  }
  .ai-chat-msg.user {
    align-self: flex-end;
    background: var(--primary);
    color: #fff;
    border-bottom-right-radius: 4px;
  }
  .ai-chat-msg.assistant {
    align-self: flex-start;
    background: #f0f0f0;
    color: var(--text);
    border-bottom-left-radius: 4px;
  }
  .ai-chat-msg.assistant p { margin: 6px 0; }
  .ai-chat-msg.assistant pre {
    background: #2d2d2d;
    color: #f8f8f2;
    padding: 10px;
    border-radius: 4px;
    overflow-x: auto;
    font-size: 12px;
    margin: 6px 0;
  }
  .ai-chat-msg.assistant code {
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    font-size: 12px;
  }
  .ai-chat-msg .msg-meta {
    font-size: 10px;
    opacity: 0.7;
    margin-top: 4px;
  }

  /* Tool Call 卡片 */
  .tool-call-card {
    align-self: flex-start;
    max-width: 90%;
    background: #f5f0ff;
    border: 1px solid #d4c4f0;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 12px;
    margin: 2px 0;
  }
  .tool-call-card.collapsed .tool-call-body { display: none; }
  .tool-call-header {
    display: flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
    user-select: none;
    font-weight: 600;
    color: #6b3fa0;
  }
  .tool-call-header .tool-icon { font-size: 14px; }
  .tool-call-header .tool-name { flex: 1; }
  .tool-call-header .tool-status {
    font-size: 11px;
    font-weight: 400;
    color: var(--text-muted);
  }
  .tool-call-header .tool-toggle { font-size: 10px; transition: transform 0.2s; }
  .tool-call-card.collapsed .tool-toggle { transform: rotate(-90deg); }
  .tool-call-body {
    margin-top: 6px;
    padding-top: 6px;
    border-top: 1px solid #e8ddf5;
    color: var(--text-secondary);
    font-size: 11px;
    line-height: 1.5;
    max-height: 120px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .tool-call-card.success .tool-status { color: var(--success); }
  .tool-call-card.error .tool-status { color: var(--error); }
  .tool-call-card.error { background: #fff0f0; border-color: #f5c0c0; }
  .tool-call-card.error .tool-call-header { color: #c0392b; }

  .ai-chat-input-area {
    display: flex;
    gap: 8px;
    padding: 10px 12px;
    border-top: 1px solid var(--panel-border);
    background: var(--card-bg);
  }
  .ai-chat-input-area textarea {
    flex: 1;
    resize: none;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 10px;
    font-size: 13px;
    font-family: inherit;
    line-height: 1.4;
    min-height: 36px;
    max-height: 100px;
  }
  .ai-chat-input-area textarea:focus {
    outline: none;
    border-color: var(--primary);
  }
  .ai-chat-input-area button {
    padding: 6px 16px;
    border: none;
    border-radius: 6px;
    background: var(--primary);
    color: #fff;
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    white-space: nowrap;
    align-self: flex-end;
  }
  .ai-chat-input-area button:hover { background: var(--primary-hover); }
  .ai-chat-input-area button:disabled { opacity: 0.5; cursor: not-allowed; }

  .model-selector {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
  }
  .model-selector select {
    font-size: 12px;
    padding: 4px 8px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--card-bg);
    color: var(--text);
    max-width: 180px;
  }
  .model-selector select:focus { outline: none; border-color: var(--primary); }
  .ai-status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
    flex-shrink: 0;
  }
  .ai-status-dot.online { background: var(--success); }
  .ai-status-dot.offline { background: var(--text-muted); }
  .ai-status-dot.checking { background: var(--warning); animation: pulse 1s infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

  .btn-send-content {
    font-size: 11px;
    padding: 4px 10px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--card-bg);
    color: var(--text-secondary);
    cursor: pointer;
    white-space: nowrap;
  }
  .btn-send-content:hover { border-color: var(--primary); color: var(--primary); }
  .btn-send-content:disabled { opacity: 0.4; cursor: not-allowed; }

  /* Settings Modal */
  .modal-overlay {
    display: none;
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.4);
    z-index: 1000;
    align-items: center;
    justify-content: center;
  }
  .modal-overlay.show { display: flex; }
  .modal {
    background: var(--card-bg);
    border-radius: var(--radius);
    width: 520px;
    max-width: 90vw;
    max-height: 80vh;
    overflow-y: auto;
    box-shadow: 0 8px 30px rgba(0,0,0,0.15);
  }
  .modal-header {
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .modal-header h3 { font-size: 16px; font-weight: 600; }
  .modal-close {
    border: none;
    background: none;
    font-size: 20px;
    cursor: pointer;
    color: var(--text-secondary);
    padding: 0 4px;
  }
  .modal-close:hover { color: var(--text); }
  .modal-body { padding: 20px; }
  .modal-body .config-section {
    margin-bottom: 18px;
    padding-bottom: 18px;
    border-bottom: 1px solid var(--border);
  }
  .modal-body .config-section:last-child { border-bottom: none; margin-bottom: 0; }
  .modal-body .config-section h4 {
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 10px;
    color: var(--text);
  }
  .modal-body .config-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-bottom: 10px;
  }
  .modal-body .config-row label {
    font-size: 11px;
    font-weight: 500;
    color: var(--text-secondary);
  }
  .modal-body .config-row input {
    font-size: 12px;
    padding: 6px 10px;
    border: 1px solid var(--border);
    border-radius: 4px;
    font-family: monospace;
  }
  .modal-body .config-row input:focus {
    outline: none;
    border-color: var(--primary);
  }
  .modal-footer {
    padding: 12px 20px;
    border-top: 1px solid var(--border);
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }
  .modal-footer button {
    padding: 8px 18px;
    border-radius: 6px;
    font-size: 13px;
    cursor: pointer;
    border: 1px solid var(--border);
    background: var(--card-bg);
    color: var(--text);
  }
  .modal-footer button.btn-primary {
    background: var(--primary);
    color: #fff;
    border-color: var(--primary);
  }
  .modal-footer button.btn-primary:hover { background: var(--primary-hover); }

  /* Responsive */
  @media (max-width: 900px) {
    .app-layout { flex-direction: column; }
    .panel-left  { width: 100%; min-width: 100%; max-height: 40vh; }
    .panel-middle { width: 100%; min-width: 100%; max-height: 30vh; }
    .panel-right { width: 100%; flex: 1; }
  }

  /* Session management */
  .session-bar {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 0 0 6px 0;
  }
  .session-bar select {
    flex: 1;
    font-size: 11px;
    padding: 3px 6px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--card-bg);
    color: var(--text);
    min-width: 0;
  }
  .session-bar .btn-session {
    font-size: 11px;
    padding: 3px 8px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--card-bg);
    color: var(--text-secondary);
    cursor: pointer;
    white-space: nowrap;
  }
  .session-bar .btn-session:hover { border-color: var(--primary); color: var(--primary); }
  .session-bar .btn-session.danger { color: #e53e3e; border-color: transparent; }
  .session-bar .btn-session.danger:hover { border-color: #e53e3e; }

  /* Compress button */
  .btn-compress {
    font-size: 11px;
    padding: 4px 10px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--card-bg);
    color: var(--text-secondary);
    cursor: pointer;
    white-space: nowrap;
  }
  .btn-compress:hover { border-color: var(--primary); color: var(--primary); }
  .btn-compress.warn {
    border-color: #f6ad55;
    color: #c05621;
    background: #fffaf0;
  }
  .btn-compress.warn:hover { background: #fef0c7; }

  /* Compressed summary message */
  .ai-chat-msg.compressed-summary {
    align-self: stretch;
    background: #f0fff4;
    border: 1px solid #9ae6b4;
    color: var(--text);
    max-width: 100%;
    font-size: 12px;
    cursor: pointer;
  }
  .ai-chat-msg.compressed-summary .summary-preview {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .ai-chat-msg.compressed-summary .summary-full {
    display: none;
    margin-top: 6px;
    white-space: pre-wrap;
  }
  .ai-chat-msg.compressed-summary.expanded .summary-preview { display: none; }
  .ai-chat-msg.compressed-summary.expanded .summary-full { display: block; }
</style>
</head>
<body>
<div class="app-layout">

  <!-- ========== LEFT PANEL: Controls ========== -->
  <div class="panel panel-left">
    <div class="panel-body">
      <div class="app-title">Web to Markdown</div>
      <div class="app-subtitle">将网页转换为 Markdown 文件</div>

      <!-- URL input -->
      <div class="input-group">
        <label>导入网页</label>
        <input type="url" id="urlInput" placeholder="https://example.com/docs" autofocus>
      </div>
      <div class="btn-row" style="margin-bottom:14px">
        <button class="btn" onclick="handleLocalFile()" title="上传 HTML/Markdown 文件，单文件不超过 10MB">本地文件</button>
        <button class="btn" onclick="pasteContent()" title="从剪贴板粘贴 HTML/文字内容转换为 Markdown">粘贴内容</button>
        <button class="btn" onclick="selectSaveFolder()" title="选择保存 Markdown 文件的本地文件夹">选择保存文件夹</button>
      </div>

      <!-- Save destination -->
      <div class="checkbox-group">
        <div class="chk-item">
          <input type="checkbox" id="saveLocal" checked>
          <span>保存到本地</span>
        </div>
        <div class="chk-item">
          <input type="checkbox" id="saveBaidu">
          <span>保存到百度网盘</span>
        </div>
        <div class="chk-item">
          <input type="checkbox" id="saveWeiyun">
          <span>保存到腾讯微云</span>
        </div>
      </div>

      <!-- Mode selector -->
      <div class="mode-selector" id="modeToggle">
        <button class="mode-option" data-mode="outline" onclick="setMode('outline')">无头模式（适合批量处理）</button>
        <button class="mode-option active" data-mode="single" onclick="setMode('single')">普通模式（适合单次处理）</button>
      </div>

      <!-- Concurrency (for outline mode) -->
      <div class="input-group hidden" id="outlineOptions">
        <label>并发加载数（1-8）</label>
        <input type="number" id="concurrency" value="3" min="1" max="8">
      </div>

      <!-- Advanced options (collapsible) -->
      <div class="collapsible-section" id="advancedSection">
        <div class="collapsible-header" onclick="
          var s = document.getElementById('advancedSection');
          s.classList.toggle('open');
        ">
          <span>高级选项</span>
          <span class="arrow">&#9654;</span>
        </div>
        <div class="collapsible-body">
          <div class="checkbox-group">
            <div class="chk-item">
              <input type="checkbox" id="includeImages" checked>
              <span>保留图片</span>
            </div>
            <div class="chk-item">
              <input type="checkbox" id="includeLinks" checked>
              <span>保留链接</span>
            </div>
            <div class="chk-item">
              <input type="checkbox" id="downloadImages" checked>
              <span>下载图片(base64)</span>
            </div>
            <div class="chk-item">
              <input type="checkbox" id="useBrowser" onchange="onBrowserChange()">
              <span title="普通静态网页无需勾选；SPA/JS动态页面、腾讯文档等需勾选">浏览器渲染</span>
              <span id="browserHint" style="display:none;font-size:10px;color:#2563eb;margin-left:2px;font-weight:600">已自动启用</span>
            </div>
          </div>

          <!-- Cookie settings -->
          <div class="cookie-group" id="cookieGroup">
            <div class="chk-item" style="margin-bottom:6px">
              <input type="checkbox" id="headlessMode" checked>
              <span>无头模式（后台静默运行）</span>
            </div>
            <label>Cookies（用于绕过登录验证）</label>
            <div style="display:flex;gap:6px;margin-bottom:6px">
              <button type="button" onclick="copyCookieCode()" style="padding:6px 10px;font-size:11px;background:var(--primary-hover);color:#fff;border:none;border-radius:4px;cursor:pointer;flex:0 0 auto">复制 JS 代码</button>
              <button type="button" onclick="pasteCookies()" style="padding:6px 10px;font-size:11px;background:#6b7280;color:#fff;border:none;border-radius:4px;cursor:pointer;flex:0 0 auto">粘贴</button>
            </div>
            <input type="text" id="cookiesInput" placeholder="Cookie 字符串会自动粘贴到这里..." style="width:100%;padding:8px;border:1.5px solid var(--border);border-radius:4px;font-size:12px;font-family:monospace;outline:none">
            <p style="font-size:10px;color:var(--text-secondary);margin-top:3px">
              点击「复制 JS 代码」&#x2192; 在目标网页按 F12 打开 Console &#x2192; 粘贴并回车 &#x2192; 回到此处点「粘贴」
            </p>
          </div>
        </div>
      </div>

      <!-- Custom filename -->
      <div class="input-group">
        <label>自定义文件名（可选）</label>
        <input type="text" id="filenameInput" placeholder="留空自动生成">
      </div>

      <!-- Action buttons -->
      <button class="btn btn-primary btn-large" id="submitBtn" onclick="doConvert()" style="width:100%">
        <span class="btn-text" id="btnText">开始转换</span>
        <div class="spinner"></div>
      </button>
      <div class="action-divider">辅助操作</div>
      <div class="btn-row" style="margin-bottom:6px">
        <button class="btn btn-secondary" onclick="exportPdf()" style="flex:1">保存至 PDF</button>
        <button class="btn btn-secondary" onclick="exportToFolder()" style="flex:1">导出</button>
      </div>
    </div>

    <!-- Recent files -->
    <div class="recent-section" id="recentSection">
      <div class="recent-title">最近文件</div>
      <div id="recentFiles">
        <div class="recent-item" style="color:var(--text-muted)">暂无文件</div>
      </div>
    </div>
  </div>

  <div class="drag-handle" data-target="left"></div>

  <!-- ========== MIDDLE PANEL: File Browser ========== -->
  <div class="panel panel-middle">
    <div class="panel-header">
      <span id="fileListTitle">文件列表</span>
      <div style="display:flex;gap:4px;align-items:center">
        <button class="btn" onclick="selectLocalFolder()" style="padding:4px 8px;font-size:11px" title="选择本地文件夹浏览 .md 文件">选择文件夹</button>
        <button class="btn" onclick="deleteSelectedFile()" style="padding:4px 8px;font-size:11px" title="删除列表中选中的文件">删除</button>
      </div>
    </div>
    <div class="panel-body" id="fileList" style="padding:0">
      <div class="empty-state" id="fileEmptyState">暂无文件，转换网页后将在此显示</div>
    </div>
  </div>

  <div class="drag-handle" data-target="middle"></div>

  <!-- ========== RIGHT PANEL: Preview + AI Chat ========== -->
  <div class="panel panel-right">
    <!-- Left: Markdown Preview -->
    <div class="preview-pane">
      <div class="panel-header" id="previewHeader">
        <span id="previewTitle">预览</span>
        <button class="btn btn-toggle-source" id="toggleSourceBtn" onclick="toggleSourceView()" style="padding:6px 14px;font-size:13px;display:none" title="切换到Markdown原档格式">原档</button>
      </div>
      <div class="panel-body" id="previewBody" style="padding:0">
        <div class="placeholder" id="previewPlaceholder">
          <div>
            <div style="font-size:48px;margin-bottom:12px">&#x1F448;</div>
            点击左侧文件列表中的文件以预览 Markdown 内容
          </div>
        </div>
        <div class="markdown-body hidden" id="markdownContent"></div>
        <pre class="markdown-source hidden" id="markdownSource"></pre>
      </div>
    </div>

    <!-- Divider between preview and AI chat -->
    <div class="drag-handle drag-handle-v" data-target="ai-chat" title="拖拽调整宽度"></div>

    <!-- Right: AI Chat -->
    <div class="ai-chat-pane" id="aiChatPane">
      <div class="panel-header" style="display:flex;align-items:center;justify-content:space-between;gap:6px;flex-wrap:wrap;padding:10px 12px">
        <!-- Session management bar -->
        <div class="session-bar" style="flex:1 1 auto;padding:0;min-width:180px">
          <button class="btn-session" onclick="createNewSession()" title="新建对话">+ 新建</button>
          <select id="aiSessionSelect" onchange="onSessionChange()">
            <option value="">-- 选择对话 --</option>
          </select>
          <button class="btn-session danger" onclick="deleteCurrentSession()" title="删除当前对话">X</button>
        </div>
        <!-- Action buttons -->
        <div style="display:flex;gap:4px;flex-shrink:0">
          <button class="btn-send-content" id="btnSendContent" onclick="sendContentToAI()" disabled title="将当前文档内容发送给AI">发送内容</button>
          <button class="btn-compress" id="btnCompress" onclick="compressContext()" style="display:none" title="压缩对话上下文以节省Token">压缩</button>
          <button class="btn-send-content" onclick="openAISettings()" title="AI 设置">设置</button>
        </div>
      </div>
      <div class="panel-body">
        <div class="ai-chat-messages" id="aiChatMessages">
          <div class="ai-chat-empty" id="aiChatEmpty">
            <div>
              <div style="font-size:36px;margin-bottom:8px">&#x1F4AC;</div>
              新建或选择对话<br>
              <span style="font-size:12px;color:var(--text-muted)">选择模型后点击「发送内容」开始</span>
            </div>
          </div>
        </div>
        <div class="ai-chat-input-area">
          <div class="model-selector" style="margin-right:6px">
            <span class="ai-status-dot offline" id="aiStatusDot" title="AI 状态"></span>
            <select id="aiModelSelect" onchange="onAIModelChange()">
              <option value="">-- 选择模型 --</option>
            </select>
          </div>
          <textarea id="aiChatInput" rows="1" placeholder="输入消息..." onkeydown="onChatInputKeydown(event)"></textarea>
          <button id="btnSendMsg" onclick="sendAIMessage()">发送</button>
        </div>
      </div>
    </div>
  </div>

</div>

<!-- AI Settings Modal -->
<div class="modal-overlay" id="aiSettingsModal">
  <div class="modal">
    <div class="modal-header">
      <h3>AI 设置</h3>
      <button class="modal-close" onclick="closeAISettings()">&times;</button>
    </div>
    <div class="modal-body" id="aiSettingsBody">
    </div>
    <div class="modal-footer">
      <button onclick="closeAISettings()">取消</button>
      <button class="btn-primary" onclick="saveAISettings()">保存设置</button>
    </div>
  </div>
</div>

<script src="/static/marked.min.js"></script>
<script>
let currentMode = 'single';
let currentFile = null;
let currentRawMarkdown = '';
let sourceViewActive = false;
let localSource = false;
let localFolderHandle = null;
let localFileHandles = {};
let saveFolderHandle = null;  // 用户选择的保存文件夹

// ---- Mode toggle ----
function setMode(mode) {
  currentMode = mode;
  document.querySelectorAll('.mode-option').forEach(function(el) {
    el.classList.toggle('active', el.dataset.mode === mode);
  });
  var outlineOps = document.getElementById('outlineOptions');
  var btnText = document.getElementById('btnText');
  if (mode === 'outline') {
    outlineOps.classList.remove('hidden');
    btnText.innerHTML = '\u5f00\u59cb\u5927\u7eb2\u722c\u53d6';
  } else {
    outlineOps.classList.add('hidden');
    btnText.innerHTML = '\u5f00\u59cb\u8f6c\u6362';
  }
}

// ---- File list ----
function renderFileList(files) {
  var container = document.getElementById('fileList');
  // Save empty state clone before innerHTML destroys the original
  var emptyState = document.getElementById('fileEmptyState');
  var emptyClone = emptyState ? emptyState.cloneNode(true) : null;

  if (!files || files.length === 0) {
    container.innerHTML = '';
    if (emptyClone) {
      emptyClone.style.display = 'block';
      container.appendChild(emptyClone);
    }
    return;
  }

  container.innerHTML = '';
  files.forEach(function(f) {
    var item = document.createElement('div');
    item.className = 'file-item' + (currentFile === f.name ? ' active' : '');
    item.onclick = function() { selectFile(f.name); };
    var sizeStr = formatSize(f.size || 0);
    var dateStr = f.modified || '';
    item.innerHTML = '<div class="file-name">' + escapeHtml(f.name) + '</div>' +
      '<div class="file-meta">' + sizeStr + (dateStr ? ' · ' + dateStr : '') + '</div>';
    container.appendChild(item);
  });
}

async function loadFileList() {
  // If we were browsing a local folder, re-scan it instead of hitting server
  if (localSource && localFolderHandle) {
    await refreshLocalFolder();
    return;
  }

  localSource = false;
  localFolderHandle = null;
  localFileHandles = {};
  document.getElementById('fileListTitle').textContent = '文件列表';
  try {
    var resp = await fetch('/api/v1/files');
    var data = await resp.json();
    renderFileList(data.files);
    updateRecentFiles(data.files);
  } catch (err) {
    console.error('加载文件列表失败:', err);
  }
}

async function refreshLocalFolder() {
  var files = [];
  localFileHandles = {};

  for await (var entry of localFolderHandle) {
    var entryName = entry[0];
    var entryHandle = entry[1];
    if (entryHandle.kind === 'file' && entryName.endsWith('.md')) {
      var file = await entryHandle.getFile();
      files.push({
        name: entryName,
        size: file.size,
        modified: new Date(file.lastModified).toLocaleString('zh-CN'),
      });
      localFileHandles[entryName] = entryHandle;
    }
  }

  files.sort(function(a, b) { return (b.modified || '').localeCompare(a.modified || ''); });
  renderFileList(files);
  document.getElementById('fileListTitle').textContent = '文件列表 (本地文件夹)';
}

async function selectLocalFolder() {
  try {
    var handle = await window.showDirectoryPicker();
    localSource = true;
    localFolderHandle = handle;
    localFileHandles = {};
    var files = [];

    // Use direct async iteration (most compatible, since Chrome 86)
    for await (var entry of handle) {
      var entryName = entry[0];
      var entryHandle = entry[1];
      if (entryHandle.kind === 'file' && entryName.endsWith('.md')) {
        var file = await entryHandle.getFile();
        files.push({
          name: entryName,
          size: file.size,
          modified: new Date(file.lastModified).toLocaleString('zh-CN'),
        });
        localFileHandles[entryName] = entryHandle;
      }
    }

    files.sort(function(a, b) { return (b.modified || '').localeCompare(a.modified || ''); });
    renderFileList(files);
    document.getElementById('fileListTitle').textContent = '文件列表 (本地文件夹)';
  } catch (err) {
    if (err.name === 'AbortError') return;
    alert('打开文件夹失败：[' + err.name + '] ' + err.message);
  }
}

async function deleteSelectedFile() {
  if (!currentFile) {
    alert('请先在文件列表中选择要删除的文件');
    return;
  }
  if (!confirm('确定要删除文件 "' + currentFile + '" 吗？此操作不可恢复。')) {
    return;
  }
  try {
    if (localSource && localFolderHandle) {
      // Delete from local folder via File System Access API
      await localFolderHandle.removeEntry(currentFile);
      delete localFileHandles[currentFile];
    } else {
      // Delete from server
      var resp = await fetch('/api/v1/files/' + encodeURIComponent(currentFile), { method: 'DELETE' });
      if (!resp.ok) {
        var errData = await resp.json();
        var errMsg = (errData.error && errData.error.message) || errData.detail || '删除失败';
        throw new Error(errMsg);
      }
    }
    // Clear preview if deleted file was being viewed
    currentFile = null;
    currentRawMarkdown = '';
    sourceViewActive = false;
    document.getElementById('previewTitle').textContent = '预览';
    document.getElementById('previewPlaceholder').style.display = '';
    document.getElementById('markdownContent').classList.add('hidden');
    document.getElementById('markdownSource').classList.add('hidden');
    var toggleBtn = document.getElementById('toggleSourceBtn');
    toggleBtn.style.display = 'none';
    toggleBtn.classList.remove('active');
    toggleBtn.textContent = '原档';
    // Refresh the file list
    loadFileList();
  } catch (err) {
    alert('删除文件失败：' + err.message);
  }
}

function updateRecentFiles(files) {
  var container = document.getElementById('recentFiles');
  if (!files || files.length === 0) {
    container.innerHTML = '<div class="recent-item" style="color:var(--text-muted)">\u6682\u65e0\u6587\u4ef6</div>';
    return;
  }

  // Sort by modified time descending, take top 10
  var sorted = files.slice().sort(function(a, b) {
    return (b.modified || '').localeCompare(a.modified || '');
  }).slice(0, 10);

  container.innerHTML = '';
  sorted.forEach(function(f) {
    var item = document.createElement('div');
    item.className = 'recent-item';
    item.onclick = function() { selectFile(f.name); };
    item.textContent = f.name;
    container.appendChild(item);
  });
}

// ---- Select file for preview ----
async function selectFile(filename) {
  currentFile = filename;

  // Update highlight in file list
  document.querySelectorAll('.file-item').forEach(function(el) {
    var nameEl = el.querySelector('.file-name');
    if (nameEl && nameEl.textContent === filename) {
      el.classList.add('active');
    } else {
      el.classList.remove('active');
    }
  });

  // Update header
  document.getElementById('previewTitle').textContent = filename;

  // Reset source view
  sourceViewActive = false;
  var toggleBtn = document.getElementById('toggleSourceBtn');
  toggleBtn.style.display = 'none';
  toggleBtn.classList.remove('active');
  toggleBtn.textContent = '原档';
  document.getElementById('markdownSource').classList.add('hidden');

  // Show markdown content, hide placeholder
  var placeholder = document.getElementById('previewPlaceholder');
  var mdContent = document.getElementById('markdownContent');
  placeholder.style.display = 'none';
  mdContent.classList.remove('hidden');

  try {
    var raw;
    if (localSource && localFileHandles[filename]) {
      var fh = localFileHandles[filename];
      var file = await fh.getFile();
      raw = await file.text();
    } else {
      var resp = await fetch('/api/v1/files/' + encodeURIComponent(filename) + '/content');
      if (!resp.ok) {
        var errData = await resp.json().catch(function() { return {}; });
        throw new Error(errData.error ? errData.error.message : 'HTTP ' + resp.status);
      }
      raw = await resp.text();
    }
    currentRawMarkdown = raw;
    document.getElementById('toggleSourceBtn').style.display = '';
    if (typeof marked !== 'undefined') {
      mdContent.innerHTML = marked.parse(raw);
    } else {
      mdContent.innerHTML = '<pre>' + escapeHtml(raw) + '</pre>';
    }
  } catch (err) {
    mdContent.innerHTML = '<p style="color:var(--error);padding:20px">\u52a0\u8f7d\u6587\u4ef6\u5931\u8d25: ' + escapeHtml(err.message) + '</p>';
  }
  // Enable AI send content button
  var btnSC = document.getElementById('btnSendContent');
  if (btnSC) btnSC.disabled = false;
}

// ---- Toggle between rendered and raw markdown source ----
function toggleSourceView() {
  var mdContent = document.getElementById('markdownContent');
  var mdSource = document.getElementById('markdownSource');
  var toggleBtn = document.getElementById('toggleSourceBtn');

  sourceViewActive = !sourceViewActive;

  if (sourceViewActive) {
    // Switch to raw source
    mdContent.classList.add('hidden');
    mdSource.classList.remove('hidden');
    mdSource.textContent = currentRawMarkdown;
    toggleBtn.textContent = '渲染';
    toggleBtn.classList.add('active');
  } else {
    // Switch to rendered view
    mdSource.classList.add('hidden');
    mdContent.classList.remove('hidden');
    if (typeof marked !== 'undefined') {
      mdContent.innerHTML = marked.parse(currentRawMarkdown);
    } else {
      mdContent.innerHTML = '<pre>' + escapeHtml(currentRawMarkdown) + '</pre>';
    }
    toggleBtn.textContent = '原档';
    toggleBtn.classList.remove('active');
  }
}

// ---- Convert ----
async function doConvert() {
  var url = document.getElementById('urlInput').value.trim();
  if (!url) { alert('\u8bf7\u8f93\u5165\u7f51\u9875\u94fe\u63a5'); return; }

  var btn = document.getElementById('submitBtn');
  btn.classList.add('loading');
  btn.disabled = true;

  var endpoint, body;

  if (currentMode === 'outline') {
    endpoint = '/api/v1/convert-outline';
    body = {
      url: url,
      options: {
        include_images: document.getElementById('includeImages').checked,
        include_links: document.getElementById('includeLinks').checked,
        download_images: document.getElementById('downloadImages').checked,
        output_filename: document.getElementById('filenameInput').value.trim() || null,
        max_concurrency: parseInt(document.getElementById('concurrency').value) || 3,
        use_browser: document.getElementById('useBrowser').checked,
        headless: document.getElementById('headlessMode').checked,
        cookies: document.getElementById('cookiesInput').value.trim() || null
      }
    };
  } else {
    endpoint = '/api/v1/convert';
    body = {
      url: url,
      options: {
        include_images: document.getElementById('includeImages').checked,
        include_links: document.getElementById('includeLinks').checked,
        download_images: document.getElementById('downloadImages').checked,
        output_filename: document.getElementById('filenameInput').value.trim() || null,
        use_browser: document.getElementById('useBrowser').checked,
        headless: document.getElementById('headlessMode').checked,
        cookies: document.getElementById('cookiesInput').value.trim() || null
      }
    };
  }

  try {
    var resp = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    var data = await resp.json();

    if (data.success) {
      var d = data.data;
      // 如果用户选择了保存文件夹，将文件写入该文件夹
      if (saveFolderHandle) {
        await saveToSelectedFolder(d);
      }

      // Refresh file list and auto-select the new file
      await loadFileList();
      selectFile(d.filename);
    } else {
      alert('\u8f6c\u6362\u5931\u8d25: ' + (data.error ? data.error.message : '\u672a\u77e5\u9519\u8bef'));
    }
  } catch (err) {
    alert('\u8bf7\u6c42\u5931\u8d25: ' + err.message);
  } finally {
    btn.classList.remove('loading');
    btn.disabled = false;
  }
}

// ---- Export PDF ----
async function exportPdf() {
  if (!currentFile) {
    alert('\u8bf7\u5148\u5728\u6587\u4ef6\u5217\u8868\u4e2d\u9009\u62e9\u4e00\u4e2a\u6587\u4ef6');
    return;
  }

  try {
    var resp = await fetch('/api/v1/export/pdf', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: currentFile })
    });

    if (resp.ok) {
      var blob = await resp.blob();
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = currentFile.replace('.md', '.pdf');
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } else {
      var err = await resp.json();
      alert('\u5bfc\u51fa\u5931\u8d25: ' + (err.detail || '\u672a\u77e5\u9519\u8bef'));
    }
  } catch (err) {
    alert('\u5bfc\u51fa\u5931\u8d25: ' + err.message);
  }
}

// ---- Export to folder (File System Access API) ----
async function exportToFolder() {
  if (!currentFile) {
    alert('\u8bf7\u5148\u5728\u6587\u4ef6\u5217\u8868\u4e2d\u9009\u62e9\u4e00\u4e2a\u6587\u4ef6');
    return;
  }

  try {
    // Try File System Access API
    if (typeof showDirectoryPicker === 'function') {
      var dirHandle = await showDirectoryPicker();
      var content;
      if (localSource && localFileHandles[currentFile]) {
        var fh2 = localFileHandles[currentFile];
        var file2 = await fh2.getFile();
        content = await file2.text();
      } else {
        var resp = await fetch('/api/v1/files/' + encodeURIComponent(currentFile) + '/content');
        content = await resp.text();
      }
      var fileHandle = await dirHandle.getFileHandle(currentFile, { create: true });
      var writable = await fileHandle.createWritable();
      await writable.write(content);
      await writable.close();
      alert('\u6587\u4ef6\u5df2\u5bfc\u51fa\u5230\u6240\u9009\u6587\u4ef6\u5939');
    } else {
      // Fallback: download
      var a = document.createElement('a');
      a.href = '/api/v1/files/' + encodeURIComponent(currentFile);
      a.download = currentFile;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    }
  } catch (err) {
    if (err.name !== 'AbortError') {
      // Fallback to download
      var a2 = document.createElement('a');
      a2.href = '/api/v1/files/' + encodeURIComponent(currentFile);
      a2.download = currentFile;
      document.body.appendChild(a2);
      a2.click();
      document.body.removeChild(a2);
    }
  }
}

// ---- 选择保存文件夹 ----
async function selectSaveFolder() {
  try {
    if (typeof showDirectoryPicker !== 'function') {
      alert('当前浏览器不支持 File System Access API，请使用 Chrome 或 Edge');
      return;
    }
    var dirHandle = await showDirectoryPicker();
    saveFolderHandle = dirHandle;
    var btn = document.querySelector('.btn-row button[onclick="selectSaveFolder()"]');
    if (btn) btn.textContent = '已选: ' + dirHandle.name;
  } catch (err) {
    if (err.name !== 'AbortError') {
      console.error('选择文件夹失败:', err);
    }
  }
}

// ---- 保存文件到选择的文件夹 ----
async function saveToSelectedFolder(d) {
  try {
    // 从服务器获取 Markdown 内容
    var resp = await fetch(d.download_url.replace('/api/v1/files/', '/api/v1/files/') + '/content');
    // 使用正确的 API 端点获取内容
    var contentResp = await fetch('/api/v1/files/' + encodeURIComponent(d.filename) + '/content');
    var content = await contentResp.text();

    // 写入到选择的文件夹
    var fileHandle = await saveFolderHandle.getFileHandle(d.filename, { create: true });
    var writable = await fileHandle.createWritable();
    await writable.write(content);
    await writable.close();
  } catch (err) {
    console.error('保存到文件夹失败:', err);
  }
}

// ---- Handle local file upload ----
function handleLocalFile() {
  var input = document.createElement('input');
  input.type = 'file';
  input.accept = '.html,.md,.htm';
  input.onchange = async function(e) {
    var file = e.target.files[0];
    if (!file) return;

    if (file.size > 10 * 1024 * 1024) {
      alert('\u6587\u4ef6\u5927\u5c0f\u8d85\u8fc7 10MB \u9650\u5236');
      return;
    }

    var text = await file.text();

    // Display in preview panel
    // Reset source view
    sourceViewActive = false;
    var toggleBtn = document.getElementById('toggleSourceBtn');
    toggleBtn.style.display = 'none';
    toggleBtn.classList.remove('active');
    toggleBtn.textContent = '原档';
    document.getElementById('markdownSource').classList.add('hidden');

    var placeholder = document.getElementById('previewPlaceholder');
    var mdContent = document.getElementById('markdownContent');
    placeholder.style.display = 'none';
    mdContent.classList.remove('hidden');

    currentRawMarkdown = text;
    toggleBtn.style.display = '';

    if (file.name.endsWith('.md') && typeof marked !== 'undefined') {
      mdContent.innerHTML = marked.parse(text);
    } else {
      mdContent.innerHTML = '<pre>' + escapeHtml(text) + '</pre>';
    }

    document.getElementById('previewTitle').textContent = file.name;
    document.getElementById('urlInput').value = file.name;

    currentFile = file.name;
  };
  input.click();
}

// ---- Browser rendering toggle ----
function onBrowserChange() {
  var checked = document.getElementById('useBrowser').checked;
  var cookieGroup = document.getElementById('cookieGroup');
  if (checked) {
    cookieGroup.classList.add('show');
  } else {
    cookieGroup.classList.remove('show');
  }
}

// ---- Baidu/Weiyun placeholder ----
document.getElementById('saveBaidu').addEventListener('change', function() {
  if (this.checked) { alert('\u529f\u80fd\u5f00\u53d1\u4e2d\uff0c\u656c\u8bf7\u671f\u5f85'); }
});
document.getElementById('saveWeiyun').addEventListener('change', function() {
  if (this.checked) { alert('\u529f\u80fd\u5f00\u53d1\u4e2d\uff0c\u656c\u8bf7\u671f\u5f85'); }
});

// ---- Cookie helpers ----
function copyCookieCode() {
  var code = 'var c = document.cookie; if (c) { copy(c); console.log("Cookie \u5df2\u590d\u5236! \u5171 " + c.split(";").length + " \u9879"); } else { console.log("\\u26a0 \u6b64\u9875\u9762\u65e0 Cookie\uff0c\u8bf7\u5148\u767b\u5f55"); }';
  navigator.clipboard.writeText(code).then(function() {
    alert('JS \u4ee3\u7801\u5df2\u590d\u5236\uff01\\n\u8bf7\u5728\u76ee\u6807\u7f51\u9875\u6309 F12 \u2192 Console \u2192 \u7c98\u8d34 \u2192 \u56de\u8f66');
  }).catch(function() {
    prompt('\u8bf7\u624b\u52a8\u590d\u5236\u4ee5\u4e0b\u4ee3\u7801\uff1a', code);
  });
}

function pasteCookies() {
  navigator.clipboard.readText().then(function(text) {
    document.getElementById('cookiesInput').value = text;
  }).catch(function() {
    alert('\u65e0\u6cd5\u8bfb\u53d6\u526a\u8d34\u677f\uff0c\u8bf7\u624b\u52a8 Ctrl+V \u7c98\u8d34');
  });
}

// ---- Auto-detect Tencent Docs URL ----
document.getElementById('urlInput').addEventListener('input', function() {
  var url = this.value.trim();
  var useBrowser = document.getElementById('useBrowser');
  var hint = document.getElementById('browserHint');

  if (url.indexOf('docs.qq.com') !== -1 || url.indexOf('feishu.cn') !== -1) {
    useBrowser.checked = true;
    hint.style.display = 'inline';
    document.getElementById('cookieGroup').classList.add('show');
  } else {
    hint.style.display = 'none';
    document.getElementById('cookieGroup').classList.remove('show');
  }
});

// ---- Enter key triggers conversion ----
document.getElementById('urlInput').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') doConvert();
});

// ---- Utility ----
function escapeHtml(text) {
  if (!text) return '';
  var d = document.createElement('div');
  d.textContent = String(text);
  return d.innerHTML;
}

function formatSize(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}

// ---- Panel drag resize ----
(function() {
  var handles = document.querySelectorAll('.drag-handle');
  var appLayout = document.querySelector('.app-layout');
  var activeHandle = null;
  var startX = 0;
  var startW = 0;
  var panel = null;

  // ---- Restore saved panel widths on page load ----
  function restorePanelWidths() {
    var leftW = localStorage.getItem('panelLeftWidth');
    var middleW = localStorage.getItem('panelMiddleWidth');
    var aiChatW = localStorage.getItem('aiChatPaneWidth');
    if (leftW) {
      var leftPanel = document.querySelector('.panel-left');
      if (leftPanel) leftPanel.style.width = leftW;
    }
    if (middleW) {
      var middlePanel = document.querySelector('.panel-middle');
      if (middlePanel) middlePanel.style.width = middleW;
    }
    if (aiChatW) {
      var aiChatPane = document.querySelector('.ai-chat-pane');
      if (aiChatPane) {
        aiChatPane.style.flex = '0 0 auto';
        aiChatPane.style.width = aiChatW;
      }
    }
  }

  // ---- Save current panel widths to localStorage ----
  function savePanelWidths() {
    var leftPanel = document.querySelector('.panel-left');
    var middlePanel = document.querySelector('.panel-middle');
    var aiChatPane = document.querySelector('.ai-chat-pane');
    if (leftPanel) localStorage.setItem('panelLeftWidth', leftPanel.offsetWidth + 'px');
    if (middlePanel) localStorage.setItem('panelMiddleWidth', middlePanel.offsetWidth + 'px');
    if (aiChatPane) localStorage.setItem('aiChatPaneWidth', aiChatPane.offsetWidth + 'px');
  }

  // Restore immediately if DOM is ready, otherwise wait
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', restorePanelWidths);
  } else {
    restorePanelWidths();
  }

  // Save on page unload as backup
  window.addEventListener('beforeunload', savePanelWidths);

  handles.forEach(function(h) {
    h.addEventListener('mousedown', function(e) {
      e.preventDefault();
      activeHandle = h;
      activeHandle.classList.add('active');
      var target = h.dataset.target;
      if (target === 'left') panel = document.querySelector('.panel-left');
      else if (target === 'middle') panel = document.querySelector('.panel-middle');
      else if (target === 'ai-chat') panel = document.querySelector('.ai-chat-pane');

      if (panel) {
        startX = e.clientX;
        startW = panel.offsetWidth;
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
      }
    });
  });

  document.addEventListener('mousemove', function(e) {
    if (!activeHandle || !panel) return;
    var dx = e.clientX - startX;
    // ai-chat handle is on the left side, so direction is reversed
    if (activeHandle.dataset.target === 'ai-chat') dx = -dx;
    var newW = startW + dx;
    var cs = getComputedStyle(panel);
    var minW = parseInt(cs.minWidth) || 200;
    var maxWStr = cs.maxWidth;
    var maxW = (maxWStr === 'none' || maxWStr === '') ? 99999 : (parseInt(maxWStr) || 600);
    newW = Math.max(minW, Math.min(maxW, newW));
    panel.style.flex = '0 0 auto';
    panel.style.width = newW + 'px';
  });

  document.addEventListener('mouseup', function() {
    if (activeHandle) {
      savePanelWidths();
      activeHandle.classList.remove('active');
      activeHandle = null;
      panel = null;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    }
  });
})();

// ---- Paste clipboard content ----
async function pasteContent() {
  try {
    var html = '';
    // Try reading HTML from clipboard first
    if (navigator.clipboard && navigator.clipboard.read) {
      try {
        var items = await navigator.clipboard.read();
        for (var i = 0; i < items.length; i++) {
          var item = items[i];
          if (item.types.indexOf('text/html') !== -1) {
            var blob = await item.getType('text/html');
            html = await blob.text();
            break;
          }
          if (item.types.indexOf('text/plain') !== -1) {
            var blob2 = await item.getType('text/plain');
            html = await blob2.text();
            break;
          }
        }
      } catch (e) {
        // Clipboard API failed, try text paste
        html = await navigator.clipboard.readText();
      }
    } else {
      html = await navigator.clipboard.readText();
    }

    if (!html || !html.trim()) {
      alert('剪贴板为空，请先在浏览器中 Ctrl+A → Ctrl+C 复制内容');
      return;
    }

    // Ask user for a filename
    var customTitle = prompt('请输入文件名（留空则自动生成）：', '');
    if (customTitle === null) return; // User cancelled

    // Show loading on submit button
    var btn = document.getElementById('submitBtn');
    btn.classList.add('loading');
    btn.disabled = true;

    try {
      // Get source URL from the input box (user may have pasted a URL first)
      var sourceUrl = document.getElementById('urlInput').value;
      if (sourceUrl === 'clipboard://paste') sourceUrl = '';
      var resp = await fetch('/api/v1/convert-html', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ html: html, source_url: sourceUrl || null, title: customTitle || null })
      });
      var data = await resp.json();
      if (data.success) {
        loadFileList();
        selectFile(data.data.filename);
        document.getElementById('urlInput').value = 'clipboard://paste';
      } else {
        alert('转换失败: ' + (data.error ? data.error.message : '未知错误'));
      }
    } finally {
      btn.classList.remove('loading');
      btn.disabled = false;
    }

  } catch (err) {
    alert('读取剪贴板失败: ' + err.message + '\\n\\n请确保已 Ctrl+A → Ctrl+C 复制了内容');
  }
}

// ---- Initialize ----
loadFileList();

// ==================== AI Chat ====================

var aiProviders = [];
var aiModels = [];
var aiHealth = {};
var aiSelectedProvider = '';
var aiSelectedModel = '';
var aiChatHistory = [];
var aiCurrentSessionId = null;
var aiSessions = [];
var aiSaveTimer = null;

// ---- Load AI providers and models on startup ----
async function loadAIProviders() {
  try {
    var resp = await fetch('/api/v1/ai/providers');
    if (!resp.ok) return;
    var data = await resp.json();
    aiProviders = data.providers || [];
    aiModels = data.models || [];
    aiHealth = data.health || {};
    // 恢复上次选择的模型
    var savedModel = localStorage.getItem('aiSelectedModel');
    if (savedModel) {
      var parts = savedModel.split(':');
      aiSelectedProvider = parts[0];
      aiSelectedModel = parts.slice(1).join(':');
    }
    updateModelSelector();
    updateAIStatus();
    loadSessions();  // 加载聊天会话
  } catch (err) {
    console.error('加载 AI Provider 失败:', err);
  }
}

function updateModelSelector() {
  var sel = document.getElementById('aiModelSelect');
  sel.innerHTML = '<option value="">-- 选择模型 --</option>';

  // 按 provider 分组
  var providerMap = {};
  aiModels.forEach(function(m) {
    if (!providerMap[m.provider]) providerMap[m.provider] = [];
    providerMap[m.provider].push(m);
  });

  var labels = {
    'ollama': '本地 (Ollama)',
    'openai': 'OpenAI',
    'claude': 'Claude',
    'qwen': '通义千问 (个人版)',
    'qwen-team': '通义千问 (团队版)',
    'custom': '自定义'
  };

  Object.keys(labels).forEach(function(prov) {
    if (providerMap[prov] && providerMap[prov].length > 0) {
      var group = document.createElement('optgroup');
      group.label = labels[prov] || prov;
      providerMap[prov].forEach(function(m) {
        var opt = document.createElement('option');
        opt.value = prov + ':' + m.id;
        opt.textContent = m.display_name || m.id;
        if (aiSelectedProvider === prov && aiSelectedModel === m.id) {
          opt.selected = true;
        }
        group.appendChild(opt);
      });
      sel.appendChild(group);
    }
  });

  // 恢复之前的选择
  if (aiSelectedProvider && aiSelectedModel) {
    sel.value = aiSelectedProvider + ':' + aiSelectedModel;
  }
}

function updateAIStatus() {
  var dot = document.getElementById('aiStatusDot');
  dot.className = 'ai-status-dot';

  var sel = document.getElementById('aiModelSelect');
  if (!sel.value) {
    dot.classList.add('offline');
    dot.title = '未选择模型';
    return;
  }

  var parts = sel.value.split(':');
  var prov = parts[0];
  var status = aiHealth[prov];

  if (status === undefined) {
    dot.classList.add('checking');
    dot.title = '检测中...';
  } else if (status) {
    dot.classList.add('online');
    dot.title = prov + ' 已连接';
  } else {
    dot.classList.add('offline');
    dot.title = prov + ' 未连接';
  }
}

function onAIModelChange() {
  var sel = document.getElementById('aiModelSelect');
  if (!sel.value) {
    aiSelectedProvider = '';
    aiSelectedModel = '';
    localStorage.removeItem('aiSelectedModel');
    return;
  }
  var parts = sel.value.split(':');
  aiSelectedProvider = parts[0];
  aiSelectedModel = parts.slice(1).join(':');
  localStorage.setItem('aiSelectedModel', sel.value);
  updateAIStatus();
}

// ==================== Session Management ====================

async function loadSessions() {
  try {
    var resp = await fetch('/api/v1/ai/sessions');
    if (!resp.ok) return;
    var data = await resp.json();
    aiSessions = data.sessions || [];
    renderSessionSelector();
    // 恢复上次选中的会话
    var lastId = localStorage.getItem('aiLastSessionId');
    if (lastId && aiSessions.some(function(s) { return s.id === lastId; })) {
      await switchSession(lastId, true);
    }
  } catch (err) {
    console.error('加载会话失败:', err);
  }
}

function renderSessionSelector() {
  var sel = document.getElementById('aiSessionSelect');
  sel.innerHTML = '<option value="">-- 选择对话 --</option>';
  aiSessions.forEach(function(s) {
    var opt = document.createElement('option');
    opt.value = s.id;
    opt.textContent = (s.title || '新对话') + ' (' + s.message_count + '条)';
    if (s.id === aiCurrentSessionId) opt.selected = true;
    sel.appendChild(opt);
  });
}

async function createNewSession(silent) {
  var title = silent ? (currentFile || '新对话') : prompt('请输入对话标题:', (currentFile || '新对话') + ' 讨论');
  if (!title) return;
  try {
    var resp = await fetch('/api/v1/ai/sessions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        title: title,
        model_provider: aiSelectedProvider,
        model_id: aiSelectedModel,
        document_ref: currentFile || ''
      })
    });
    if (!resp.ok) { alert('创建会话失败'); return; }
    var data = await resp.json();
    var session = data.session;
    // 添加到列表
    aiSessions.unshift(session);
    renderSessionSelector();
    await switchSession(session.id, true);
  } catch (err) {
    alert('创建会话失败: ' + err.message);
  }
}

async function switchSession(sessionId, silent) {
  try {
    var resp = await fetch('/api/v1/ai/sessions/' + sessionId);
    if (!resp.ok) { if (!silent) alert('加载会话失败'); return; }
    var data = await resp.json();
    var session = data.session;
    aiCurrentSessionId = session.id;
    localStorage.setItem('aiLastSessionId', session.id);

    // 恢复消息
    aiChatHistory = (session.messages || []).map(function(m) {
      return {role: m.role, content: m.content};
    });

    // 重新渲染消息列表
    var container = document.getElementById('aiChatMessages');
    container.innerHTML = '';
    var emptyEl = document.createElement('div');
    emptyEl.className = 'ai-chat-empty';
    emptyEl.id = 'aiChatEmpty';
    emptyEl.innerHTML = '<div><div style="font-size:36px;margin-bottom:8px">&#x1F4AC;</div>新建或选择对话<br><span style="font-size:12px;color:var(--text-muted)">选择模型后点击「发送内容」开始</span></div>';
    container.appendChild(emptyEl);

    aiChatHistory.forEach(function(m) {
      // 渲染工具调用结果
      if (m.tool_results && m.tool_results.length > 0) {
        for (var i = 0; i < m.tool_results.length; i++) {
          appendToolCallMessage(m.tool_results[i]);
        }
      }
      if (m.role === 'system' && m.content.indexOf('[\u538b\u7f29]') === 0) {
        appendChatMessage('compressed-summary', m.content.replace('[\u538b\u7f29] ', ''));
      } else {
        appendChatMessage(m.role === 'system' ? 'system' : m.role, m.content);
      }
    });

    renderSessionSelector();
    checkCompressNeeded();
  } catch (err) {
    if (!silent) alert('加载会话失败: ' + err.message);
  }
}

function onSessionChange() {
  var sel = document.getElementById('aiSessionSelect');
  if (sel.value) {
    switchSession(sel.value);
  }
}

async function deleteCurrentSession() {
  if (!aiCurrentSessionId) { alert('没有选中的对话'); return; }
  if (!confirm('确定要删除当前对话吗？此操作不可撤销。')) return;
  try {
    var resp = await fetch('/api/v1/ai/sessions/' + aiCurrentSessionId, { method: 'DELETE' });
    if (!resp.ok) { alert('删除失败'); return; }
    aiSessions = aiSessions.filter(function(s) { return s.id !== aiCurrentSessionId; });
    aiCurrentSessionId = null;
    aiChatHistory = [];
    localStorage.removeItem('aiLastSessionId');

    // 清空消息显示
    var container = document.getElementById('aiChatMessages');
    container.innerHTML = '';
    var emptyEl = document.createElement('div');
    emptyEl.className = 'ai-chat-empty';
    emptyEl.id = 'aiChatEmpty';
    emptyEl.innerHTML = '<div><div style="font-size:36px;margin-bottom:8px">&#x1F4AC;</div>新建或选择对话<br><span style="font-size:12px;color:var(--text-muted)">选择模型后点击「发送内容」开始</span></div>';
    container.appendChild(emptyEl);

    renderSessionSelector();
    document.getElementById('btnCompress').style.display = 'none';
  } catch (err) {
    alert('删除失败: ' + err.message);
  }
}

// ---- Auto-save ----

function saveSessionDebounced() {
  if (!aiCurrentSessionId) return;
  clearTimeout(aiSaveTimer);
  aiSaveTimer = setTimeout(saveSession, 500);
}

async function saveSession() {
  if (!aiCurrentSessionId) return;
  try {
    await fetch('/api/v1/ai/sessions/' + aiCurrentSessionId, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        messages: aiChatHistory,
        model_provider: aiSelectedProvider,
        model_id: aiSelectedModel,
        document_ref: currentFile || ''
      })
    });
    // 更新本地会话列表中的消息数
    var sess = aiSessions.find(function(s) { return s.id === aiCurrentSessionId; });
    if (sess) {
      sess.message_count = aiChatHistory.length;
      sess.model_provider = aiSelectedProvider;
      sess.model_id = aiSelectedModel;
      renderSessionSelector();
    }
  } catch (err) {
    console.error('保存会话失败:', err);
  }
}

// 页面关闭前保存
window.addEventListener('beforeunload', function() {
  if (aiCurrentSessionId && aiChatHistory.length > 0) {
    var data = JSON.stringify({
      messages: aiChatHistory,
      model_provider: aiSelectedProvider,
      model_id: aiSelectedModel,
      document_ref: currentFile || ''
    });
    fetch('/api/v1/ai/sessions/' + aiCurrentSessionId, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: data,
      keepalive: true
    });
  }
});

// ==================== Context Compression ====================

function checkCompressNeeded() {
  var btn = document.getElementById('btnCompress');
  if (!aiChatHistory.length) { btn.style.display = 'none'; return; }
  // 估算总 token（简单按字符数/2）
  var totalChars = 0;
  aiChatHistory.forEach(function(m) { totalChars += (m.content || '').length; });
  var estimatedTokens = Math.ceil(totalChars / 2);
  if (estimatedTokens > 6000) {
    btn.style.display = '';
    btn.classList.add('warn');
  } else if (estimatedTokens > 3000) {
    btn.style.display = '';
    btn.classList.remove('warn');
  } else {
    btn.style.display = 'none';
  }
}

async function compressContext() {
  if (!aiSelectedProvider || !aiSelectedModel) {
    alert('请先选择 AI 模型');
    return;
  }
  if (aiChatHistory.length <= 4) {
    alert('消息太少，无需压缩');
    return;
  }

  var btn = document.getElementById('btnCompress');
  btn.disabled = true;
  btn.textContent = '压缩中...';

  try {
    var resp = await fetch('/api/v1/ai/compress', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        provider: aiSelectedProvider,
        model: aiSelectedModel,
        messages: aiChatHistory.map(function(m) { return {role: m.role, content: m.content}; })
      })
    });

    if (!resp.ok) {
      var errData = await resp.json().catch(function() { return {}; });
      throw new Error((errData.error && errData.error.message) || errData.detail || 'HTTP ' + resp.status);
    }

    var data = await resp.json();
    if (!data.summary) {
      alert(data.message || '无需压缩');
      btn.disabled = false;
      btn.textContent = '压缩';
      return;
    }

    // 保留最后 4 条消息，前面的替换为摘要
    var keepCount = 4;
    var keptMessages = aiChatHistory.slice(-keepCount);
    var compressedCount = aiChatHistory.length - keepCount;

    aiChatHistory = [
      {role: 'system', content: '[压缩] ' + data.summary}
    ].concat(keptMessages);

    // 重新渲染
    var container = document.getElementById('aiChatMessages');
    container.innerHTML = '';
    aiChatHistory.forEach(function(m) {
      if (m.role === 'system' && m.content.indexOf('[压缩]') === 0) {
        appendChatMessage('compressed-summary', m.content.replace('[压缩] ', ''));
      } else {
        appendChatMessage(m.role === 'system' ? 'system' : m.role, m.content);
      }
    });

    // 保存压缩后的状态
    saveSession();
    btn.textContent = '已压缩';
    btn.classList.remove('warn');
    setTimeout(function() {
      btn.textContent = '压缩';
      btn.disabled = false;
    }, 2000);

  } catch (err) {
    alert('压缩失败: ' + err.message);
    btn.disabled = false;
    btn.textContent = '压缩';
  }
}

// ---- Send current document content to AI as context ----
async function sendContentToAI() {
  if (!currentRawMarkdown) {
    alert('请先打开一个文件');
    return;
  }
  if (!aiSelectedProvider || !aiSelectedModel) {
    alert('请先选择 AI 模型');
    return;
  }
  // 如果没有当前会话，自动创建一个
  if (!aiCurrentSessionId) {
    await createNewSession(true);
    if (!aiCurrentSessionId) return; // 用户取消了
  }

  var btn = document.getElementById('btnSendContent');
  btn.disabled = true;
  btn.textContent = '发送中...';

  var content = currentRawMarkdown;
  var filename = currentFile || '文档';

  // 在当前会话中追加文档内容
  appendChatMessage('system', '已将《' + filename + '》的内容作为上下文发送给 AI。你可以开始提问了。');
  aiChatHistory.push({role: 'user', content: '这是文档《' + filename + '》的内容：\\n\\n' + content});
  saveSessionDebounced();

  btn.textContent = '已发送';
  setTimeout(function() {
    btn.textContent = '发送内容';
    btn.disabled = false;
  }, 2000);
}

// ---- Tool Call Display ----

function getToolDisplayName(toolName) {
  var map = {
    'web_search': '\u5168\u7f51\u641c\u7d22',
    'get_weather': '\u5929\u6c14\u67e5\u8be2',
    'get_datetime_info': '\u65e5\u671f\u65f6\u95f4',
    'translate': '\u7ffb\u8bd1'
  };
  return map[toolName] || toolName;
}

function getToolIcon(toolName) {
  var map = {
    'web_search': '&#x1F50D;',
    'get_weather': '&#x2600;&#xFE0F;',
    'get_datetime_info': '&#x1F4C5;',
    'translate': '&#x1F310;'
  };
  return map[toolName] || '&#x1F527;';
}

function appendToolCallMessage(toolResult) {
  var container = document.getElementById('aiChatMessages');
  var emptyEl = document.getElementById('aiChatEmpty');
  if (emptyEl) emptyEl.style.display = 'none';

  var card = document.createElement('div');
  card.className = 'tool-call-card collapsed' + (toolResult.success ? ' success' : ' error');

  var displayName = getToolDisplayName(toolResult.name);
  var icon = getToolIcon(toolResult.name);
  var statusText = toolResult.success ? '\u2714 \u6210\u529f' : '\u2716 \u5931\u8d25';
  var bodyText = toolResult.success ? toolResult.result : (toolResult.error || '\u672a\u77e5\u9519\u8bef');

  card.innerHTML =
    '<div class="tool-call-header">' +
      '<span class="tool-icon">' + icon + '</span>' +
      '<span class="tool-name">' + escapeHtml(displayName) + '</span>' +
      '<span class="tool-status">' + statusText + '</span>' +
      '<span class="tool-toggle">\u25BC</span>' +
    '</div>' +
    '<div class="tool-call-body">' + escapeHtml(bodyText) + '</div>';

  card.querySelector('.tool-call-header').addEventListener('click', function() {
    card.classList.toggle('collapsed');
  });

  container.appendChild(card);
  container.scrollTop = container.scrollHeight;
  return card;
}

// ---- Send a user message to AI ----
async function sendAIMessage() {
  var input = document.getElementById('aiChatInput');
  var text = input.value.trim();
  if (!text) return;

  if (!aiSelectedProvider || !aiSelectedModel) {
    alert('请先选择 AI 模型');
    return;
  }

  // 如果没有当前会话，自动创建一个
  if (!aiCurrentSessionId) {
    await createNewSession(true);
    if (!aiCurrentSessionId) return;
  }

  var btn = document.getElementById('btnSendMsg');
  btn.disabled = true;
  input.disabled = true;
  input.value = '';

  // Show user message
  appendChatMessage('user', text);
  aiChatHistory.push({role: 'user', content: text});

  // Show loading placeholder
  var loadingEl = appendChatMessage('assistant', '\u6b63\u5728\u601d\u8003\u4e2d...');
  loadingEl.style.opacity = '0.5';

  try {
    var resp = await fetch('/api/v1/ai/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        provider: aiSelectedProvider,
        model: aiSelectedModel,
        messages: aiChatHistory,
        system_prompt: '',
        use_tools: true
      })
    });

    // Remove loading placeholder
    loadingEl.remove();

    if (!resp.ok) {
      var errData = await resp.json().catch(function() { return {}; });
      var errMsg = (errData.error && errData.error.message) || errData.detail || ('HTTP ' + resp.status);
      throw new Error(errMsg);
    }

    var data = await resp.json();

    // 显示工具调用结果（如果有）
    var toolResults = data.tool_results;
    if (toolResults && toolResults.length > 0) {
      for (var i = 0; i < toolResults.length; i++) {
        appendToolCallMessage(toolResults[i]);
      }
    }

    appendChatMessage('assistant', data.content);
    aiChatHistory.push({
      role: 'assistant',
      content: data.content,
      tool_results: data.tool_results || null
    });
    saveSessionDebounced();
    checkCompressNeeded();

  } catch (err) {
    loadingEl.remove();
    appendChatMessage('assistant', '\u274c \u8bf7\u6c42\u5931\u8d25: ' + escapeHtml(String(err.message)));
  } finally {
    btn.disabled = false;
    input.disabled = false;
    input.focus();
  }
}

function onChatInputKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendAIMessage();
  }
}

function appendChatMessage(role, content) {
  var container = document.getElementById('aiChatMessages');
  var emptyEl = document.getElementById('aiChatEmpty');
  if (emptyEl) emptyEl.style.display = 'none';

  var div = document.createElement('div');
  div.className = 'ai-chat-msg ' + role;

  if (role === 'system') {
    div.style.alignSelf = 'center';
    div.style.background = '#fffbe6';
    div.style.border = '1px solid #ffe58f';
    div.style.color = '#8c6d1f';
    div.style.maxWidth = '100%';
    div.style.textAlign = 'center';
    div.textContent = content;
  } else if (role === 'compressed-summary') {
    // 可折叠的压缩摘要
    var previewText = content.substring(0, 100) + (content.length > 100 ? '...' : '');
    div.className = 'ai-chat-msg compressed-summary';
    div.innerHTML = '<div class="summary-preview">已压缩上下文 (点击展开): ' + escapeHtml(previewText) + '</div>' +
                    '<div class="summary-full">' + escapeHtml(content) + '</div>';
    div.addEventListener('click', function() {
      div.classList.toggle('expanded');
    });
  } else if (role === 'assistant') {
    // Simple markdown rendering for assistant messages
    div.innerHTML = simpleMarkdownRender(content);
  } else {
    div.textContent = content;
  }

  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

function simpleMarkdownRender(text) {
  // Escape HTML first
  var esc = escapeHtml(text);
  // Bold **text**
  esc = esc.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
  // Inline code `code`
  esc = esc.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Replace newlines with <br> for single newlines
  esc = esc.replace(/\\n/g, '<br>');
  return esc;
}

// ---- AI Settings ----
async function openAISettings() {
  document.getElementById('aiSettingsModal').classList.add('show');

  try {
    var resp = await fetch('/api/v1/ai/config');
    var data = await resp.json();
    renderSettingsForm(data.config || {});
  } catch (err) {
    console.error('加载配置失败:', err);
  }
}

function closeAISettings() {
  document.getElementById('aiSettingsModal').classList.remove('show');
}

function renderSettingsForm(config) {
  var body = document.getElementById('aiSettingsBody');
  var sections = [
    {
      key: 'ollama',
      title: '本地 Ollama',
      enabled: config.ollama_enabled !== 'false',
      fields: [
        {key: 'ollama_base_url', label: 'Base URL', placeholder: 'http://localhost:11434', value: config.ollama_base_url || 'http://localhost:11434'}
      ]
    },
    {
      key: 'openai',
      title: 'OpenAI',
      enabled: config.openai_enabled !== 'false',
      fields: [
        {key: 'openai_api_key', label: 'API Key', placeholder: 'sk-...', value: config.openai_api_key || '', type: 'password'},
        {key: 'openai_base_url', label: 'Base URL', placeholder: 'https://api.openai.com/v1', value: config.openai_base_url || 'https://api.openai.com/v1'},
        {key: 'openai_models', label: '模型列表 (逗号分隔)', placeholder: 'gpt-4o-mini,gpt-4o', value: config.openai_models || 'gpt-4o-mini,gpt-4o'}
      ]
    },
    {
      key: 'claude',
      title: 'Anthropic Claude',
      enabled: config.claude_enabled !== 'false',
      fields: [
        {key: 'claude_api_key', label: 'API Key', placeholder: 'sk-ant-...', value: config.claude_api_key || '', type: 'password'},
        {key: 'claude_base_url', label: 'Base URL (可选)', placeholder: '', value: config.claude_base_url || ''},
        {key: 'claude_models', label: '模型列表 (逗号分隔)', placeholder: 'claude-3-5-haiku-20241022', value: config.claude_models || 'claude-3-5-haiku-20241022,claude-3-5-sonnet-20241022'}
      ]
    },
    {
      key: 'qwen',
      title: '通义千问 (个人版)',
      enabled: config.qwen_enabled !== 'false',
      fields: [
        {key: 'qwen_base_url', label: 'Base URL', placeholder: 'https://dashscope.aliyuncs.com/compatible-mode/v1', value: config.qwen_base_url || 'https://dashscope.aliyuncs.com/compatible-mode/v1'},
        {key: 'qwen_api_key', label: 'API Key', placeholder: 'sk-...', value: config.qwen_api_key || '', type: 'password'},
        {key: 'qwen_models', label: '模型列表 (逗号分隔)', placeholder: 'qwen-turbo,qwen-plus,qwen-max', value: config.qwen_models || 'qwen-turbo,qwen-plus,qwen-max'}
      ]
    },
    {
      key: 'qwen-team',
      title: '通义千问 (团队版)',
      enabled: config.qwen_team_enabled !== 'false',
      fields: [
        {key: 'qwen_team_base_url', label: 'Base URL', placeholder: 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1', value: config.qwen_team_base_url || 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'},
        {key: 'qwen_team_api_key', label: 'API Key', placeholder: 'sk-...', value: config.qwen_team_api_key || '', type: 'password'},
        {key: 'qwen_team_models', label: '模型列表 (逗号分隔)', placeholder: 'qwen3.6-flash,qwen3.6-plus,qwen3.7-max', value: config.qwen_team_models || 'qwen3.6-flash,qwen3.6-plus,qwen3.7-max'}
      ]
    },
    {
      key: 'custom',
      title: '自定义 OpenAI 兼容 API',
      enabled: config.custom_enabled !== 'false',
      fields: [
        {key: 'custom_base_url', label: 'Base URL', placeholder: 'https://api.deepseek.com/v1', value: config.custom_base_url || ''},
        {key: 'custom_api_key', label: 'API Key', placeholder: 'sk-...', value: config.custom_api_key || '', type: 'password'},
        {key: 'custom_models', label: '模型列表 (逗号分隔)', placeholder: 'deepseek-chat', value: config.custom_models || ''}
      ]
    }
  ];

  body.innerHTML = '';
  sections.forEach(function(sec) {
    var secDiv = document.createElement('div');
    secDiv.className = 'config-section';

    // Header row with checkbox
    var headerRow = document.createElement('div');
    headerRow.className = 'config-section-header';
    var cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.id = 'aiCfg_' + sec.key + '_enabled';
    cb.checked = sec.enabled;
    cb.style.cssText = 'margin-right:8px;width:16px;height:16px;cursor:pointer';
    var label = document.createElement('label');
    label.htmlFor = cb.id;
    label.textContent = sec.title;
    label.style.cssText = 'font-size:13px;font-weight:600;cursor:pointer';
    headerRow.appendChild(cb);
    headerRow.appendChild(label);
    secDiv.appendChild(headerRow);

    // Fields container (dim when disabled)
    var fieldsDiv = document.createElement('div');
    fieldsDiv.className = 'config-section-fields';
    fieldsDiv.style.cssText = sec.enabled ? '' : 'opacity:0.4;pointer-events:none';
    secDiv.appendChild(fieldsDiv);

    // Toggle fields visibility on checkbox change
    cb.addEventListener('change', function() {
      fieldsDiv.style.cssText = this.checked ? '' : 'opacity:0.4;pointer-events:none';
    });

    sec.fields.forEach(function(field) {
      var row = document.createElement('div');
      row.className = 'config-row';
      var fl = document.createElement('label');
      fl.textContent = field.label;
      var input = document.createElement('input');
      input.type = field.type || 'text';
      input.id = 'aiCfg_' + field.key;
      input.value = field.value || '';
      input.placeholder = field.placeholder || '';
      row.appendChild(fl);
      row.appendChild(input);
      fieldsDiv.appendChild(row);
    });

    body.appendChild(secDiv);
  });
}

async function saveAISettings() {
  var config = {};
  var inputs = document.querySelectorAll('#aiSettingsBody input');
  inputs.forEach(function(input) {
    var key = input.id.replace('aiCfg_', '');
    if (input.type === 'checkbox') {
      config[key] = input.checked ? 'true' : 'false';
    } else {
      config[key] = input.value.trim();
    }
  });

  try {
    var resp = await fetch('/api/v1/ai/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({config: config})
    });
    if (!resp.ok) {
      var errData = await resp.json().catch(function() { return {}; });
      var errMsg = (errData.error && errData.error.message) || errData.detail || '保存失败';
      throw new Error(errMsg);
    }
    closeAISettings();
    // 重新加载 Provider
    await loadAIProviders();
    alert('配置已保存');
  } catch (err) {
    alert('保存配置失败: ' + err.message);
  }
}

// ---- Initialize AI on page load ----
loadAIProviders();
</script>
</body>
</html>"""


# ---- API 路由 ----

@app.get("/api/v1/health", response_model=HealthResponse)
async def health():
    """健康检查"""
    return HealthResponse()

# 调试端点：检查当前加载的模块版本
@app.get("/api/v1/debug")
async def debug_info():
    import app.main as m
    import inspect
    src = inspect.getsource(m.index)
    src_len = len(src)
    html_start = src.find('return """')
    html = src[html_start+9:]
    html = html[:html.rfind('"""')]
    return {
        "source_len": src_len,
        "html_len": len(html),
        "has_onBrowserChange": "onBrowserChange" in html,
        "has_browserExplain": "browserExplain" in html,
        "file": m.__file__,
    }


@app.post("/api/v1/convert", response_model=ConvertResponse)
async def convert_post(
    body: ConvertRequest,
    fetcher: Fetcher = Depends(get_fetcher),
    browser_fetcher: BrowserFetcher = Depends(get_browser_fetcher),
    extractor: Extractor = Depends(get_extractor),
    converter: Converter = Depends(get_converter),
    storage: Storage = Depends(get_storage),
    image_downloader: ImageDownloader = Depends(get_image_downloader),
):
    """提交 URL 进行转换（POST JSON Body）"""
    return await _do_convert(
        url=body.url,
        options=body.options or ConvertOptions(),
        fetcher=fetcher,
        browser_fetcher=browser_fetcher,
        extractor=extractor,
        converter=converter,
        storage=storage,
        image_downloader=image_downloader,
    )


@app.get("/api/v1/convert", response_model=ConvertResponse)
async def convert_get(
    url: str = Query(..., description="目标网页 URL"),
    include_images: bool = Query(default=True, description="是否保留图片引用"),
    include_links: bool = Query(default=True, description="是否保留超链接"),
    download_images: bool = Query(default=True, description="是否下载图片到本地"),
    output_filename: str | None = Query(default=None, description="自定义输出文件名"),
    use_browser: bool = Query(default=False, description="是否使用浏览器渲染"),
    fetcher: Fetcher = Depends(get_fetcher),
    browser_fetcher: BrowserFetcher = Depends(get_browser_fetcher),
    extractor: Extractor = Depends(get_extractor),
    converter: Converter = Depends(get_converter),
    storage: Storage = Depends(get_storage),
    image_downloader: ImageDownloader = Depends(get_image_downloader),
):
    """提交 URL 进行转换（GET Query 参数）"""
    return await _do_convert(
        url=url,
        options=ConvertOptions(
            include_images=include_images,
            include_links=include_links,
            download_images=download_images,
            output_filename=output_filename,
            use_browser=use_browser,
        ),
        fetcher=fetcher,
        browser_fetcher=browser_fetcher,
        extractor=extractor,
        converter=converter,
        storage=storage,
        image_downloader=image_downloader,
    )


# ---- 粘贴内容 API ----

@app.post("/api/v1/convert-html", response_model=ConvertResponse)
async def convert_pasted_html(
    body: PasteConvertRequest,
    extractor: Extractor = Depends(get_extractor),
    converter: Converter = Depends(get_converter),
    storage: Storage = Depends(get_storage),
    image_downloader: ImageDownloader = Depends(get_image_downloader),
):
    """将粘贴的 HTML 内容转换为 Markdown"""
    from bs4 import BeautifulSoup
    import re

    soup = BeautifulSoup(body.html, 'lxml')

    # Remove script, style, nav, header, footer elements
    for tag in soup.find_all(['script', 'style', 'nav', 'header', 'footer']):
        tag.decompose()

    # Get title
    title = body.title or ''
    if not title:
        h1 = soup.find('h1')
        if h1:
            title = h1.get_text(strip=True)
    if not title:
        if soup.title:
            title = soup.title.get_text(strip=True)
    if not title:
        title = '粘贴内容'

    # Convert to markdown - process body content
    body_el = soup.find('body') or soup
    md_lines = []

    # Add title as H1
    md_lines.append(f'# {title}')
    md_lines.append('')

    # Track processed images to avoid duplicates
    seen_images = set()

    # Process elements in order
    for el in body_el.descendants:
        if not hasattr(el, 'name') or el.name is None:
            continue

        tag = el.name.lower()
        txt = el.get_text(strip=True)

        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            level = int(tag[1])
            if txt:
                md_lines.append(f'{"#" * level} {txt}')
                md_lines.append('')

        elif tag == 'img':
            src = el.get('src', '')
            alt = el.get('alt', '') or txt or '图片'
            if src and src not in seen_images:
                seen_images.add(src)
                md_lines.append(f'![{alt}]({src})')
                md_lines.append('')

        elif tag == 'p':
            if txt and len(txt) > 2:
                # Check if it contains images
                imgs = el.find_all('img')
                for img in imgs:
                    src2 = img.get('src', '')
                    alt2 = img.get('alt', '') or '图片'
                    if src2 and src2 not in seen_images:
                        seen_images.add(src2)
                        md_lines.append(f'![{alt2}]({src2})')
                        md_lines.append('')
                # Add text
                text_only = el.get_text(strip=True)
                if text_only and len(text_only) > 2:
                    md_lines.append(text_only)
                    md_lines.append('')

        elif tag == 'li':
            if txt and len(txt) > 2:
                md_lines.append(f'- {txt}')

        elif tag == 'a':
            href = el.get('href', '')
            if txt and href and len(txt) > 1:
                md_lines.append(f'[{txt}]({href})')
                md_lines.append('')

        elif tag in ('pre', 'code'):
            if txt:
                md_lines.append('```')
                md_lines.append(txt)
                md_lines.append('```')
                md_lines.append('')

        elif tag in ('strong', 'b'):
            if txt and len(txt) > 1:
                if not md_lines or md_lines[-1] != f'**{txt}**':
                    md_lines.append(f'**{txt}**')
                    md_lines.append('')

    # If very little content was extracted, fall back to trafilatura
    if len(md_lines) < 5:
        extract_options = ExtractOptions(include_images=True, include_links=True)
        extract_result = extractor.extract(html=body.html, url='', options=extract_options)
        markdown = converter.post_process(extract_result.markdown, extract_options)
        title = body.title or extract_result.title or '粘贴内容'
    else:
        markdown = '\n'.join(md_lines)
        # Clean up excessive blank lines
        markdown = re.sub(r'\n{4,}', '\n\n\n', markdown)

    # Filter UI noise
    markdown = converter._filter_ui_noise(markdown)

    # Download and embed images as base64 to ensure they persist
    source_url = body.source_url or ''
    if not source_url:
        # Try to extract base/source URL from the HTML
        base_el = soup.find('base')
        if base_el and base_el.get('href'):
            source_url = base_el.get('href')
        if not source_url:
            canonical = soup.find('link', rel='canonical')
            if canonical and canonical.get('href'):
                source_url = canonical.get('href')
    if not source_url:
        source_url = 'clipboard://paste'

    markdown, embedded_count = await image_downloader.download_and_embed(markdown, source_url)

    file_path, saved_filename = storage.save(content=markdown, title=title)
    return ConvertResponse(
        data=ConvertData(
            filename=saved_filename,
            title=title,
            file_path=file_path,
            download_url=f"/api/v1/files/{saved_filename}",
            content_length=len(markdown),
            source_url="clipboard://paste",
            image_count=embedded_count or len(seen_images),
        )
    )

# ---- 文件管理 API ----

@app.get("/api/v1/files", response_model=FileListResponse)
async def list_files(storage: Storage = Depends(get_storage)):
    """列出所有可下载的 Markdown 文件"""
    files = storage.list_files()
    return FileListResponse(files=[FileInfo(**f) for f in files])


@app.get("/api/v1/files/{filename}")
async def download_file(
    filename: str,
    storage: Storage = Depends(get_storage),
):
    """下载生成的 Markdown 文件"""
    # 安全检查：防止路径遍历
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="无效的文件名")

    filepath = storage.get_file_path(filename)
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(
        path=filepath,
        filename=filename,
        media_type="text/markdown; charset=utf-8",
    )


@app.get("/api/v1/files/{filename}/content")
async def get_file_content(
    filename: str,
    storage: Storage = Depends(get_storage),
):
    """获取 Markdown 文件原始内容（用于预览）"""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="无效的文件名")

    filepath = storage.get_file_path(filename)
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="文件不存在")

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    from fastapi.responses import Response
    return Response(content=content, media_type="text/plain; charset=utf-8")


@app.delete("/api/v1/files/{filename}")
async def delete_file(
    filename: str,
    storage: Storage = Depends(get_storage),
):
    """删除指定的 Markdown 文件"""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="无效的文件名")

    success = storage.delete_file(filename)
    if not success:
        raise HTTPException(status_code=404, detail="文件不存在")

    return {"success": True, "message": f"文件 {filename} 已删除"}


@app.post("/api/v1/export/pdf")
async def export_pdf(
    body: ExportPdfRequest,
    storage: Storage = Depends(get_storage),
):
    """导出 Markdown 文件为 PDF"""
    if ".." in body.filename or "/" in body.filename or "\\" in body.filename:
        raise HTTPException(status_code=400, detail="无效的文件名")

    filepath = storage.get_file_path(body.filename)
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="文件不存在")

    with open(filepath, "r", encoding="utf-8") as f:
        md_content = f.read()

    html = markdown_to_print_html(md_content, title=body.filename.replace(".md", ""))
    return HTMLResponse(content=html)


# ---- AI 相关 API ----

@app.get("/api/v1/ai/providers")
async def list_ai_providers():
    """获取所有可用 AI Provider 及模型"""
    manager = get_ai_manager()
    if not manager.is_initialized():
        manager.initialize()
    providers = manager.get_providers()
    models = await manager.list_all_models()
    health = await manager.check_all_health()
    return {
        "providers": providers,
        "models": models,
        "health": health,
    }


@app.get("/api/v1/ai/config")
async def get_ai_config():
    """获取当前 AI 配置（敏感信息脱敏）"""
    manager = get_ai_manager()
    config = manager.get_config()
    # 对 API Key 脱敏
    safe_config = {}
    for k, v in config.items():
        if "api_key" in k and v:
            safe_config[k] = v[:4] + "****" + v[-4:] if len(v) > 8 else "****"
        else:
            safe_config[k] = v
    return {"config": safe_config}


@app.post("/api/v1/ai/config")
async def save_ai_config(body: dict):
    """保存 AI 配置到持久化文件，并重新初始化 Provider"""
    config = body.get("config", {})
    AIManager.save_config(config)
    # 重新初始化
    manager = get_ai_manager()
    manager.initialize()
    return {"success": True}


@app.post("/api/v1/ai/chat")
async def ai_chat(body: dict):
    """AI 聊天接口（支持 Function Calling / 工具调用）

    Body:
        provider: str  - Provider 名称
        model: str     - 模型 ID
        messages: list - [{role, content, tool_calls?, tool_call_id?, name?}]
        system_prompt: str (可选)
        use_tools: bool (可选，默认 true) - 是否启用工具调用
    """
    provider_name = body.get("provider", "")
    model = body.get("model", "")
    raw_messages = body.get("messages", [])
    system_prompt = body.get("system_prompt", "")
    use_tools = body.get("use_tools", True)

    if not provider_name or not model:
        raise HTTPException(status_code=400, detail="缺少 provider 或 model 参数")

    manager = get_ai_manager()
    if not manager.is_initialized():
        manager.initialize()

    messages = []
    for m in raw_messages:
        msg = ChatMessage(
            role=m.get("role", "user"),
            content=m.get("content", ""),
            tool_call_id=m.get("tool_call_id", ""),
            name=m.get("name", ""),
        )
        # 解析 tool_calls
        raw_tool_calls = m.get("tool_calls")
        if raw_tool_calls:
            from app.services.ai.base import RawToolCall
            msg.tool_calls = [
                RawToolCall(id=tc.get("id", ""), name=tc.get("name", ""), arguments=tc.get("arguments", {}))
                for tc in raw_tool_calls
            ]
        messages.append(msg)

    try:
        if use_tools:
            response = await manager.chat_with_tools(
                provider_name=provider_name,
                model=model,
                messages=messages,
                system_prompt=system_prompt,
            )
        else:
            response = await manager.chat(
                provider_name=provider_name,
                model=model,
                messages=messages,
                system_prompt=system_prompt,
            )

        result = {
            "success": True,
            "content": response.content,
            "model": response.model,
            "provider": response.provider,
            "usage": response.usage,
        }
        if response.tool_calls:
            result["tool_calls"] = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in response.tool_calls
            ]
        if response.tool_results:
            result["tool_results"] = response.tool_results
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 调用失败: {str(e)}")


@app.get("/api/v1/ai/health")
async def check_ai_health():
    """检查各 Provider 健康状态"""
    manager = get_ai_manager()
    if not manager.is_initialized():
        manager.initialize()
    health = await manager.check_all_health()
    return {"health": health}


# ---- AI 聊天记录 ----

@app.get("/api/v1/ai/sessions")
async def list_chat_sessions():
    """获取所有聊天会话列表"""
    mgr = get_chat_history_manager()
    sessions = mgr.list_sessions()
    return {"sessions": sessions}


@app.post("/api/v1/ai/sessions", status_code=201)
async def create_chat_session(body: CreateSessionRequest):
    """创建新的聊天会话"""
    mgr = get_chat_history_manager()
    session = mgr.create_session(
        title=body.title,
        model_provider=body.model_provider,
        model_id=body.model_id,
        document_ref=body.document_ref,
    )
    return {"session": session}


@app.get("/api/v1/ai/sessions/{session_id}")
async def get_chat_session(session_id: str):
    """获取指定会话详情（含消息列表）"""
    mgr = get_chat_history_manager()
    session = mgr.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session": session}


@app.put("/api/v1/ai/sessions/{session_id}")
async def update_chat_session(session_id: str, body: UpdateSessionRequest):
    """更新会话消息和元数据"""
    mgr = get_chat_history_manager()
    messages = [m.model_dump() for m in body.messages]
    kwargs = {}
    if body.title is not None:
        kwargs["title"] = body.title
    if body.model_provider is not None:
        kwargs["model_provider"] = body.model_provider
    if body.model_id is not None:
        kwargs["model_id"] = body.model_id
    if body.document_ref is not None:
        kwargs["document_ref"] = body.document_ref
    if body.compressed is not None:
        kwargs["compressed"] = body.compressed
    if body.original_message_count is not None:
        kwargs["original_message_count"] = body.original_message_count
    session = mgr.update_session(session_id, messages, **kwargs)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session": session}


@app.delete("/api/v1/ai/sessions/{session_id}")
async def delete_chat_session(session_id: str):
    """删除指定会话及其所有消息"""
    mgr = get_chat_history_manager()
    deleted = mgr.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"success": True}


# ---- AI 上下文压缩 ----

@app.post("/api/v1/ai/compress")
async def compress_context(body: CompressRequest):
    """压缩聊天上下文 - 调用 AI 对早期消息做摘要"""
    provider = body.provider
    model = body.model
    raw_messages = [m.model_dump() for m in body.messages]

    if not raw_messages:
        raise HTTPException(status_code=400, detail="消息列表为空")

    # 保留最后 4 条不压缩
    keep_count = 4
    if len(raw_messages) <= keep_count:
        return {"summary": "", "original_count": len(raw_messages), "message": "消息太少，无需压缩"}

    to_compress = raw_messages[:-keep_count]
    original_count = len(raw_messages)

    # 构造对话文本
    conversation_text = ""
    for m in to_compress:
        role_label = "用户" if m["role"] == "user" else ("AI" if m["role"] == "assistant" else "系统")
        conversation_text += f"[{role_label}]: {m['content']}\n\n"

    system_prompt = (
        "你是一个对话摘要助手。请总结以下对话内容，保留所有关键事实、重要观点、用户问题和AI回答的要点。"
        "摘要应该简洁但包含继续对话所需的必要上下文信息。请使用对话原文的语言进行摘要。"
    )

    manager = get_ai_manager()
    if not manager.is_initialized():
        manager.initialize()

    try:
        response = await manager.chat(
            provider_name=provider,
            model=model,
            messages=[
                ChatMessage(role="user", content=f"请总结以下对话：\n\n{conversation_text}")
            ],
            system_prompt=system_prompt,
        )
        return {
            "summary": response.content,
            "original_count": original_count,
            "compressed_count": len(to_compress),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"压缩失败: {str(e)}")


# ---- 大纲爬取 API ----

@app.post("/api/v1/convert-outline", response_model=OutlineConvertResponse)
async def convert_outline(
    body: OutlineConvertRequest,
    fetcher: Fetcher = Depends(get_fetcher),
    browser_fetcher: BrowserFetcher = Depends(get_browser_fetcher),
    extractor: Extractor = Depends(get_extractor),
    converter: Converter = Depends(get_converter),
    storage: Storage = Depends(get_storage),
    image_downloader: ImageDownloader = Depends(get_image_downloader),
    outline_crawler: OutlineCrawler = Depends(get_outline_crawler),
):
    """大纲爬取：自动发现导航结构并抓取所有章节页面"""
    return await _do_outline_convert(
        url=body.url,
        options=body.options or OutlineConvertOptions(),
        fetcher=fetcher,
        browser_fetcher=browser_fetcher,
        extractor=extractor,
        converter=converter,
        storage=storage,
        image_downloader=image_downloader,
        outline_crawler=outline_crawler,
    )


# ---- 核心逻辑 ----

async def _extract_tencent_doc(
    url: str,
    browser_fetcher: BrowserFetcher,
    options: ConvertOptions,
) -> tuple[str, str]:
    """通过腾讯文档 API 提取正文（无需登录即可获取公开文档内容）
    
    Returns: (text, title)
    """
    import asyncio
    from app.services.tencent_doc_extractor import TencentDocExtractor

    extractor = TencentDocExtractor()
    page = await browser_fetcher.create_page(headless=options.headless)

    try:
        await extractor.setup_intercept(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        # 给 API 响应一点时间
        for _ in range(10):
            if extractor.has_content():
                break
            await asyncio.sleep(0.5)

        if extractor.has_content():
            return extractor.get_text(), extractor.get_title()
        return "", ""
    finally:
        await page.close()


async def _do_convert(
    url: str,
    options: ConvertOptions,
    fetcher: Fetcher,
    browser_fetcher: BrowserFetcher,
    extractor: Extractor,
    converter: Converter,
    storage: Storage,
    image_downloader: ImageDownloader,
) -> ConvertResponse:
    # 1. URL 校验
    if not url or not url.strip():
        raise MissingURLError()

    if not validate_url(url):
        from app.exceptions import InvalidURLError
        raise InvalidURLError(detail=f"URL 格式不合法: {url}")

    # 2. 腾讯文档特殊处理：通过 API 直接提取正文（绕过 Canvas 渲染限制）
    if TencentDocExtractor.is_tencent_doc_url(url) and options.use_browser:
        tencent_text, tencent_title = await _extract_tencent_doc(url, browser_fetcher, options)
        if tencent_text:
            extract_options = ExtractOptions(
                include_images=options.include_images,
                include_links=options.include_links,
            )
            markdown = converter.post_process(tencent_text, extract_options)
            doc_title = tencent_title or "腾讯文档"
            # 移除正文中与文档标题重复的首行（避免 # 标题 和正文首行重复）
            if doc_title:
                lines = markdown.split("\n")
                for i, ln in enumerate(lines):
                    s = ln.strip()
                    if not s:
                        continue
                    # 去掉 ## 前缀后与标题比较（_apply_headings 会给首行加 ##）
                    text_no_prefix = re.sub(r"^#{1,6}\s+", "", s)
                    if text_no_prefix in doc_title or doc_title in text_no_prefix:
                        lines[i] = ""
                    break
                markdown = "\n".join(lines)
            # 将文档标题作为 H1 添加到正文前面
            if doc_title and not markdown.lstrip().startswith(f"# {doc_title}"):
                markdown = f"# {doc_title}\n\n{markdown}"
            filename = options.output_filename
            if filename and not filename.endswith(".md"):
                filename += ".md"
            file_path, saved_filename = storage.save(
                content=markdown, filename=filename, title=doc_title,
            )
            return ConvertResponse(
                data=ConvertData(
                    filename=saved_filename,
                    title=doc_title,
                    file_path=file_path,
                    download_url=f"/api/v1/files/{saved_filename}",
                    content_length=len(markdown),
                    source_url=url,
                    image_count=0,
                )
            )

    # 3. 常规抓取网页
    if options.use_browser:
        fetch_result = await browser_fetcher.fetch(url, cookies=options.cookies, headless=options.headless)
    else:
        fetch_result = await fetcher.fetch(url)

    # 3. 提取正文
    extract_options = ExtractOptions(
        include_images=options.include_images,
        include_links=options.include_links,
    )
    extract_result = extractor.extract(
        html=fetch_result.html,
        url=fetch_result.final_url,
        options=extract_options,
    )

    # 3.5 从原始 HTML 提取图片注入 Markdown（trafilatura 不输出图片引用）
    if options.include_images:
        extract_result.markdown = _inject_images_from_html(
            extract_result.markdown, fetch_result.html, fetch_result.final_url,
        )

    # 4. Markdown 后处理
    markdown = converter.post_process(extract_result.markdown, extract_options)

    # 5. 下载图片并以 base64 嵌入 Markdown（如果启用）
    image_count = 0
    if options.download_images and options.include_images:
        markdown, image_count = await image_downloader.download_and_embed(
            markdown=markdown,
            source_url=fetch_result.final_url,
        )

    # 6. 保存文件
    filename = options.output_filename
    if filename and not filename.endswith(".md"):
        filename += ".md"

    file_path, saved_filename = storage.save(
        content=markdown,
        filename=filename,
        title=extract_result.title,
    )

    return ConvertResponse(
        data=ConvertData(
            filename=saved_filename,
            title=extract_result.title or "Untitled",
            file_path=file_path,
            download_url=f"/api/v1/files/{saved_filename}",
            content_length=len(markdown),
            source_url=fetch_result.final_url,
            image_count=image_count,
        )
    )


def _inject_images_from_html(markdown: str, html: str, base_url: str) -> str:
    """从原始 HTML 中提取图片 URL，按文档位置注入 Markdown 对应段落。

    遍历 HTML 元素（document order），将每张图片归属到最近的标题下，
    然后在 Markdown 中该标题的内容段落后插入图片引用。
    trafilatura 不输出 ![](url) 引用，所以此函数负责图片定位注入。
    同时过滤广告/推广类图片（根据章节标题关键词和图片 URL 来源判断）。
    """
    from bs4 import BeautifulSoup
    from urllib.parse import urlparse, unquote
    from collections import Counter
    import os

    soup = BeautifulSoup(html, "lxml")
    body = soup.find("body") or soup

    heading_tags = {"h1", "h2", "h3", "h4", "h5", "h6"}
    bold_tags = {"strong", "b"}  # WeChat / rich-text articles use bold as headings
    seen_urls = set()

    # --- Phase 0: Pre-scan images to detect ad/non-content CDN patterns ---
    # Collect all image URLs, extract their CDN base paths
    all_img_urls_prescan = []
    for img in soup.find_all("img"):
        src = img.get("src", "").strip()
        data_src = img.get("data-src", "").strip()
        url = data_src if (data_src and data_src.startswith("http")) else src
        if url and url.startswith("http"):
            all_img_urls_prescan.append(url)

    # Determine majority CDN base path to detect ad images from different sources
    # e.g. WeChat content images vs public-account promo images have different CDN folders
    majority_base = ""
    if len(all_img_urls_prescan) >= 3:
        base_paths = []
        for url in all_img_urls_prescan:
            parsed = urlparse(url)
            parts = parsed.path.rsplit("/", 2)
            if len(parts) >= 2:
                base_paths.append(parts[0] + "/" + parts[-2])
            else:
                base_paths.append(parsed.path.rsplit("/", 1)[0])
        path_counter = Counter(base_paths)
        majority_base = path_counter.most_common(1)[0][0] if path_counter else ""

    # Ad-related section heading keywords (Chinese + English patterns)
    AD_HEADING_KEYWORDS = [
        "版本信息", "记录说明", "关注", "扫一扫", "扫码",
        "广告", "推广", "商务合作", "合作洽谈",
        "版权声明", "免责声明", "转载声明",
        "推荐阅读", "热门文章", "往期回顾", "精选内容",
        "阅读原文", "查看原文", "了解更多", "点击查看",
        "关于我们", "联系我们", "商务", "APP下载",
        "粉丝群", "入群", "福利", "抽奖",
        # Additional patterns based on real-world page structures
        "推荐产品", "产品推荐", "相关产品", "更多产品", "热门产品",
        "推荐工具", "相关工具", "更多工具",
        "更多服务", "相关服务", "热门服务",
        "免费试用", "立即体验", "申请试用", "立即下载",
        "关注我们", "官方微信", "公众号", "视频号",
        "备案", "ICP", "公安备案",
        "推荐", "相关推荐", "热门推荐", "为你推荐",
        "相关文章", "更多文章", "最新文章",
        "评论", "留言", "分享", "收藏", "点赞",
        "导航", "目录", "分类", "标签",
        "关于作者", "作者简介",
        "版权", "隐私", "用户协议", "服务条款",
        "返回首页", "回到顶部", "上一篇", "下一篇",
    ]

    def _is_ad_heading(text: str) -> bool:
        """Check if heading text indicates an ad/promo section."""
        if not text:
            return False
        text_lower = text.lower()
        for kw in AD_HEADING_KEYWORDS:
            if kw.lower() in text_lower:
                return True
        return False

    # Tags and class keywords that indicate footer/nav/sidebar/recommend sections
    FOOTER_NAV_TAGS = {"footer", "nav", "aside"}
    FOOTER_NAV_CLASS_KW = [
        "footer", "nav", "sidebar", "recommend", "related", "bottom",
        "side", "promo", "promotion", "popular", "hot", "trending",
        "contact", "about", "service", "info", "copyright", "social",
    ]

    def _is_in_footer_section(img_el) -> bool:
        """Check if image element is inside a footer/nav/sidebar section."""
        for parent in img_el.parents:
            if not hasattr(parent, "name") or parent.name is None:
                continue
            if parent.name.lower() in FOOTER_NAV_TAGS:
                return True
            cls = " ".join(parent.get("class", []))
            pid = parent.get("id", "")
            combined = f"{cls} {pid}".lower()
            for kw in FOOTER_NAV_CLASS_KW:
                if kw in combined:
                    return True
        return False

    def _is_ad_image_url(url: str) -> bool:
        """Check if image URL is from a likely ad/promo CDN source.
        
        Only filters when there's a clear super-majority CDN path (>70%)
        AND the minority path has very few images (<3 or <20%).
        This prevents false positives on sites with multiple legitimate CDN sources.
        """
        if not majority_base or not url:
            return False
        parsed = urlparse(url)
        parts = parsed.path.rsplit("/", 2)
        img_base = (parts[0] + "/" + parts[-2]) if len(parts) >= 2 else parsed.path.rsplit("/", 1)[0]
        if img_base == majority_base:
            return False
        
        # Only filter if majority is a clear super-majority
        total = sum(path_counter.values())
        majority_count = path_counter.get(majority_base, 0)
        minority_count = path_counter.get(img_base, 0)
        
        # Conditions for considering minority path as ad:
        # 1. Majority path has > 70% of images
        # 2. Minority path has very few images (< 3 or < 20% of total)
        if majority_count / total > 0.7 and (minority_count < 3 or minority_count / total < 0.2):
            return True
        return False

    # --- Phase 1: Walk HTML, group images by nearest heading ---
    groups = []             # [(heading_text, [img_ref, ...]), ...]
    orphan_images = []      # images found before any heading
    current_heading = ""
    current_images = []
    current_is_ad_section = False  # Track if current section is an ad section

    for el in body.descendants:
        if not hasattr(el, "name"):
            continue
        tag = el.name.lower() if el.name else ""

        # Heading tag (h1-h6): save previous group, start new one
        if tag in heading_tags:
            if current_images:
                if current_heading:
                    groups.append((current_heading, current_images, current_is_ad_section))
                else:
                    orphan_images.extend(current_images)
                current_images = []
            text = el.get_text(strip=True)
            if text and len(text) > 2:
                current_heading = text[:120]
                current_is_ad_section = _is_ad_heading(text)
            else:
                current_heading = ""
                current_is_ad_section = False

        # Bold tag: may be a heading in WeChat / rich-text articles
        if tag in bold_tags:
            bold_text = el.get_text(strip=True)
            if bold_text and 3 <= len(bold_text) <= 100:
                if current_images:
                    if current_heading:
                        groups.append((current_heading, current_images, current_is_ad_section))
                    else:
                        orphan_images.extend(current_images)
                    current_images = []
                current_heading = bold_text[:120]
                current_is_ad_section = _is_ad_heading(bold_text)

        # Image encountered
        if tag == "img":
            src = el.get("src", "").strip()
            data_src = el.get("data-src", "").strip()
            url = data_src if (data_src and data_src.startswith("http")) else src
            if not url or not url.startswith("http"):
                continue

            # Filter small UI images
            cls = " ".join(el.get("class", []))
            if any(kw in cls.lower() for kw in ("avatar", "emoji", "icon", "logo", "qr", "cover")):
                continue

            # Filter images from ad sections (promo/meta content, not article body)
            if current_is_ad_section:
                continue

            # Filter images inside footer/nav/sidebar sections
            if _is_in_footer_section(el):
                continue

            # Filter images from non-majority CDN paths (likely from different account / source)
            if _is_ad_image_url(url):
                continue

            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Alt text
            alt = el.get("alt", "").strip()
            if not alt or len(alt) < 2:
                path = urlparse(url).path
                fname = unquote(os.path.basename(path))
                alt = os.path.splitext(fname)[0] if fname else "图片"

            current_images.append(f"![{alt}]({url})")

    # Save last group
    if current_images:
        if current_heading:
            groups.append((current_heading, current_images, current_is_ad_section))
        else:
            orphan_images.extend(current_images)

    if not groups and not orphan_images:
        return markdown

    # --- Phase 2: Insert images at matched positions in markdown ---
    lines = markdown.split("\n")
    insertions = []  # (line_index, [img_refs_to_insert_before_this_line])

    # Find all heading positions in markdown (lines like **...** or # ...)
    heading_positions = []  # [(heading_text_in_md, line_index)]
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("**") and stripped.endswith("**") and len(stripped) < 100:
            heading_positions.append((stripped.strip("* "), i))
        elif stripped.startswith("# ") and len(stripped) < 100:
            heading_positions.append((stripped.strip("# "), i))

    # Track which markdown heading indices have been used
    used_md_indices = set()

    for heading_text, img_refs, is_ad in groups:
        if not img_refs:
            continue
        if is_ad:
            continue  # Skip ad section images

        # Try to match heading_text against markdown headings
        best_match = -1
        search_long = heading_text[:60] if len(heading_text) > 60 else heading_text
        search_short = heading_text[:15]

        for md_heading, idx in heading_positions:
            if idx in used_md_indices:
                continue
            if len(search_long) >= 3 and (search_long in md_heading or md_heading in search_long):
                best_match = idx
                break

        if best_match < 0 and len(search_short) >= 3:
            for md_heading, idx in heading_positions:
                if idx in used_md_indices:
                    continue
                if search_short in md_heading:
                    best_match = idx
                    break

        if best_match < 0:
            orphan_images.extend(img_refs)
            continue

        used_md_indices.add(best_match)

        # Find next heading position (end of this section)
        next_heading_line = len(lines)
        for _, idx in heading_positions:
            if idx > best_match:
                next_heading_line = idx
                break

        # Insert right before the next heading (at end of current section)
        insert_at = next_heading_line
        while insert_at > best_match + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1

        insertions.append((insert_at, img_refs))

    # --- Phase 4: Apply insertions in REVERSE order ---
    insertions.sort(key=lambda x: x[0], reverse=True)
    for idx, img_refs in insertions:
        block_str = "\n\n" + "\n\n".join(img_refs) + "\n"
        if idx < len(lines):
            lines.insert(idx, block_str)
        else:
            lines.append("")
            lines.append(block_str.rstrip("\n"))

    return "\n".join(lines)


async def _do_outline_convert(
    url: str,
    options: OutlineConvertOptions,
    fetcher: Fetcher,
    browser_fetcher: BrowserFetcher,
    extractor: Extractor,
    converter: Converter,
    storage: Storage,
    image_downloader: ImageDownloader,
    outline_crawler: OutlineCrawler,
) -> OutlineConvertResponse:
    # 1. URL 校验
    if not url or not url.strip():
        raise MissingURLError()

    if not validate_url(url):
        from app.exceptions import InvalidURLError
        raise InvalidURLError(detail=f"URL 格式不合法: {url}")

    # 2. 设置爬取并发
    outline_crawler.max_concurrency = options.max_concurrency

    # 3. 选择抓取方式
    chosen_fetcher = browser_fetcher if options.use_browser else fetcher

    # 如果使用浏览器，预设模式和 cookies
    if options.use_browser:
        await browser_fetcher.configure_mode(options.headless)
        if options.cookies:
            await browser_fetcher.set_cookies_for_url(url, options.cookies)

    # 4. 大纲爬取
    extract_options = ExtractOptions(
        include_images=options.include_images,
        include_links=options.include_links,
    )
    outline_result = await outline_crawler.crawl(
        url=url,
        fetcher=chosen_fetcher,
        extractor=extractor,
        options=extract_options,
    )

    # 4. Markdown 后处理
    markdown = converter.post_process(outline_result.markdown, extract_options)

    # 5. 下载图片并以 base64 嵌入 Markdown（如果启用）
    image_count = 0
    if options.download_images and options.include_images:
        markdown, image_count = await image_downloader.download_and_embed(
            markdown=markdown,
            source_url=url,
        )

    # 6. 保存文件
    filename = options.output_filename
    if filename and not filename.endswith(".md"):
        filename += ".md"

    file_path, saved_filename = storage.save(
        content=markdown,
        filename=filename,
        title=outline_result.title,
    )

    return OutlineConvertResponse(
        data=OutlineConvertData(
            filename=saved_filename,
            title=outline_result.title or "Untitled",
            file_path=file_path,
            download_url=f"/api/v1/files/{saved_filename}",
            content_length=len(markdown),
            source_url=url,
            page_count=outline_result.page_count,
            failed_count=outline_result.failed_count,
            failed_urls=outline_result.failed_urls,
        )
    )
    markdown = converter.post_process(outline_result.markdown, extract_options)

    # 5. 下载图片并以 base64 嵌入 Markdown（如果启用）
    image_count = 0
    if options.download_images and options.include_images:
        markdown, image_count = await image_downloader.download_and_embed(
            markdown=markdown,
            source_url=url,
        )

    # 6. 保存文件
    filename = options.output_filename
    if filename and not filename.endswith(".md"):
        filename += ".md"

    file_path, saved_filename = storage.save(
        content=markdown,
        filename=filename,
        title=outline_result.title,
    )

    return OutlineConvertResponse(
        data=OutlineConvertData(
            filename=saved_filename,
            title=outline_result.title or "Untitled",
            file_path=file_path,
            download_url=f"/api/v1/files/{saved_filename}",
            content_length=len(markdown),
            source_url=url,
            page_count=outline_result.page_count,
            failed_count=outline_result.failed_count,
            failed_urls=outline_result.failed_urls,
        )
    )
