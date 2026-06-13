# Web to Markdown - 抓屏工具

将网页链接转换为 Markdown 文件，支持 AI 对话、桌面原生窗口。

## 功能

- **网页转 Markdown**：输入 URL 一键转换，支持保留图片、链接
- **大纲批量转换**：自动发现文档目录结构，并发抓取合并
- **粘贴 HTML**：直接粘贴剪贴板内容转换
- **浏览器渲染**：Playwright 驱动，支持 SPA / 腾讯文档等 JS 页面
- **图片处理**：下载后 Base64 内嵌，生成自包含 .md 文件
- **AI 对话**：支持 Ollama / OpenAI / Claude / 通义千问（个人版+团队版）
- **AI 工具**：翻译、天气、网页搜索、日期时间
- **PDF 导出**：Markdown 转 PDF
- **桌面窗口**：pywebview 原生窗口，无需打开浏览器

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 安装 Playwright 浏览器

```bash
playwright install chromium
```

### 启动

```bash
# Web 模式（浏览器访问）
python run.py

# 桌面模式（原生窗口，需 WebView2）
python desktop_app.py
```

访问地址：http://127.0.0.1:8080

## 配置

### 环境变量

复制 `.env.example` 为 `.env`，填入 API Key：

```env
AI_OPENAI_API_KEY=sk-xxx
AI_CLAUDE_API_KEY=sk-ant-xxx
AI_QWEN_API_KEY=sk-xxx
AI_QWEN_TEAM_API_KEY=sk-xxx
```

### AI 模型

启动后在界面中点击「AI 设置」启用所需 Provider 并配置模型。

## 技术栈

- **后端**：Python / FastAPI / uvicorn
- **抓取**：httpx / trafilatura / Playwright
- **AI**：OpenAI SDK / Anthropic SDK
- **桌面**：pywebview (Edge WebView2)

## 许可证

MIT
