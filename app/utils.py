import re
import unicodedata
from datetime import datetime
from urllib.parse import urlparse

import chardet


def validate_url(url: str) -> bool:
    """校验 URL 格式是否合法"""
    try:
        result = urlparse(url)
        return all([result.scheme in ("http", "https"), result.netloc])
    except Exception:
        return False


def sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    # 移除或替换 Windows/Linux 文件名中不允许的字符
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    # 合并多余空白
    name = re.sub(r"\s+", " ", name).strip()
    # 限制长度
    if len(name) > 80:
        name = name[:80]
    return name


def generate_filename(title: str | None, extension: str = ".md") -> str:
    """根据标题和时间戳生成文件名"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if title:
        clean_title = sanitize_filename(title)
        if clean_title:
            return f"{clean_title}_{timestamp}{extension}"
    return f"article_{timestamp}{extension}"


def detect_encoding(content: bytes, content_type: str | None = None) -> str:
    """
    多策略编码检测
    1. 从 Content-Type 响应头检测
    2. 从 HTML meta 标签检测
    3. 使用 chardet 自动检测
    """
    # 从 Content-Type 头检测
    if content_type:
        match = re.search(r"charset=([^\s;]+)", content_type, re.IGNORECASE)
        if match:
            return match.group(1).strip().strip('"').strip("'")

    # 从 HTML meta 标签检测（只检查前 2048 字节以提升性能）
    head = content[:2048].decode("ascii", errors="ignore")
    # <meta charset="utf-8">
    match = re.search(r'<meta[^>]+charset=["\']?([^"\'\s;>]+)', head, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # chardet 自动检测
    result = chardet.detect(content)
    if result and result.get("encoding"):
        enc = result["encoding"]
        if enc.lower() == "gb2312":
            return "gbk"
        return enc

    # 最终兜底
    return "utf-8"


def decode_html(content: bytes, content_type: str | None = None) -> str:
    """将 HTML 字节内容解码为字符串"""
    encoding = detect_encoding(content, content_type)
    # 尝试多种编码
    encodings_to_try = [encoding, "utf-8", "gbk", "latin-1"]
    for enc in encodings_to_try:
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    # 最终兜底
    return content.decode("utf-8", errors="replace")
