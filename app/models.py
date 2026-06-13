from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field, HttpUrl


class ConvertOptions(BaseModel):
    """转换选项"""
    include_images: bool = Field(default=True, description="是否保留图片引用")
    include_links: bool = Field(default=True, description="是否保留超链接")
    download_images: bool = Field(default=True, description="是否下载图片到本地")
    output_filename: Optional[str] = Field(default=None, description="自定义输出文件名（不含扩展名）")
    use_browser: bool = Field(default=False, description="是否使用浏览器渲染（用于 SPA 页面）")
    headless: bool = Field(default=True, description="浏览器是否无头模式（关闭可看到浏览器窗口）")
    cookies: Optional[str] = Field(default=None, description="Cookie 字符串，用于绕过登录验证 (name1=value1; name2=value2)")


class ConvertRequest(BaseModel):
    """POST 请求体"""
    url: str = Field(..., description="目标网页 URL")
    options: ConvertOptions = Field(default_factory=ConvertOptions)


class ConvertData(BaseModel):
    """成功响应数据"""
    filename: str
    title: str
    file_path: str
    download_url: str
    content_length: int
    source_url: str
    image_count: int = Field(default=0, description="下载的图片数量")


class ConvertResponse(BaseModel):
    """成功响应"""
    success: bool = True
    data: ConvertData


class ErrorDetail(BaseModel):
    """错误详情"""
    code: str
    message: str
    detail: Optional[str] = None


class ErrorResponse(BaseModel):
    """错误响应"""
    success: bool = False
    error: ErrorDetail


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str = "ok"
    version: str = "1.0.0"


# ---- 文件管理 ----

class FileInfo(BaseModel):
    """文件信息"""
    name: str
    size: int
    modified: str


class FileListResponse(BaseModel):
    """文件列表响应"""
    files: list[FileInfo]


class ExportPdfRequest(BaseModel):
    """PDF 导出请求"""
    filename: str




class PasteConvertRequest(BaseModel):
    """粘贴 HTML 内容转换请求"""
    html: str = Field(..., description="剪贴板中的 HTML 内容")
    title: Optional[str] = Field(default=None, description="文档标题（可选，从 HTML 中提取）")
    source_url: Optional[str] = Field(default=None, description="来源页面 URL（用于下载图片时解析相对路径）")


# ---- 大纲爬取 ----

class OutlineConvertOptions(BaseModel):
    """大纲爬取选项"""
    include_images: bool = Field(default=True, description="是否保留图片引用")
    include_links: bool = Field(default=True, description="是否保留超链接")
    download_images: bool = Field(default=True, description="是否下载图片到本地并嵌入 base64")
    output_filename: Optional[str] = Field(default=None, description="自定义输出文件名（不含扩展名）")
    max_concurrency: int = Field(default=3, description="并发抓取数", ge=1, le=8)
    use_browser: bool = Field(default=False, description="是否使用浏览器渲染（用于 SPA 页面）")
    headless: bool = Field(default=True, description="浏览器是否无头模式（关闭可看到浏览器窗口）")
    cookies: Optional[str] = Field(default=None, description="Cookie 字符串，用于绕过登录验证 (name1=value1; name2=value2)")


class OutlineConvertRequest(BaseModel):
    """大纲爬取 POST 请求体"""
    url: str = Field(..., description="目标网页 URL（含大纲导航的页面）")
    options: OutlineConvertOptions = Field(default_factory=OutlineConvertOptions)


class OutlineConvertData(BaseModel):
    """大纲爬取成功响应数据"""
    filename: str
    title: str
    file_path: str
    download_url: str
    content_length: int
    source_url: str
    page_count: int = Field(default=1, description="成功抓取的页面数")
    failed_count: int = Field(default=0, description="抓取失败的页面数")
    failed_urls: list[str] = Field(default_factory=list, description="失败的 URL 列表")


class OutlineConvertResponse(BaseModel):
    """大纲爬取成功响应"""
    success: bool = True
    data: OutlineConvertData


# ---- AI 聊天记录 ----

class ChatMessageModel(BaseModel):
    """聊天消息"""
    role: str = Field(..., description="user / assistant / system")
    content: str = Field(..., description="消息内容")


class ChatSessionItem(BaseModel):
    """会话列表项"""
    id: str
    title: str
    model_provider: str = ""
    model_id: str = ""
    document_ref: str = ""
    message_count: int = 0
    compressed: bool = False
    created_at: str
    updated_at: str


class ChatSessionDetail(BaseModel):
    """会话详情（含消息列表）"""
    id: str
    title: str
    model_provider: str = ""
    model_id: str = ""
    document_ref: str = ""
    message_count: int = 0
    compressed: bool = False
    created_at: str
    updated_at: str
    messages: list[ChatMessageModel] = Field(default_factory=list)


class CreateSessionRequest(BaseModel):
    """创建会话请求"""
    title: str = "新对话"
    model_provider: str = ""
    model_id: str = ""
    document_ref: str = ""


class UpdateSessionRequest(BaseModel):
    """更新会话请求"""
    messages: list[ChatMessageModel] = Field(default_factory=list)
    title: Optional[str] = None
    model_provider: Optional[str] = None
    model_id: Optional[str] = None
    document_ref: Optional[str] = None
    compressed: Optional[bool] = None
    original_message_count: Optional[int] = None


class CompressRequest(BaseModel):
    """上下文压缩请求"""
    provider: str
    model: str
    messages: list[ChatMessageModel]
