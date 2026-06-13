from __future__ import annotations
from dataclasses import dataclass, field

import trafilatura
from bs4 import BeautifulSoup

from app.config import settings
from app.exceptions import ExtractionFailedError


@dataclass
class ExtractOptions:
    """提取选项"""
    include_images: bool = True
    include_links: bool = True


@dataclass
class ExtractResult:
    """提取结果"""
    markdown: str
    title: str
    metadata: dict = field(default_factory=dict)


class Extractor:
    """正文提取服务 - trafilatura 为主，BeautifulSoup 降级"""

    def extract(self, html: str, url: str, options: ExtractOptions | None = None) -> ExtractResult:
        if options is None:
            options = ExtractOptions()

        # 首选：使用 trafilatura 提取正文和元数据
        result = self._extract_with_trafilatura(html, url, options)
        if result and len(result.markdown.strip()) >= settings.min_content_length:
            return result

        # 降级：使用 BeautifulSoup 提取
        fallback = self._extract_with_bs4(html, url)
        if fallback and len(fallback.markdown.strip()) >= settings.min_content_length:
            return fallback

        raise ExtractionFailedError(
            detail="无法识别网页正文内容，页面可能不包含可提取的文章内容"
        )

    def _extract_with_trafilatura(
        self, html: str, url: str, options: ExtractOptions
    ) -> ExtractResult | None:
        """使用 trafilatura 提取"""
        try:
            # Pre-process: normalize lazy-loaded images for trafilatura
            if options.include_images:
                html = self._normalize_images(html)

            # 提取元数据
            metadata = trafilatura.extract_metadata(
                html,
                default_url=url,
            )
            title = ""
            if metadata:
                title = metadata.title or ""

            # 提取正文为 Markdown
            markdown = trafilatura.extract(
                html,
                output_format="markdown",
                include_images=options.include_images,
                include_links=options.include_links,
                include_tables=True,
                url=url,
                with_metadata=True,
            )

            if not markdown or not markdown.strip():
                return None

            return ExtractResult(
                markdown=markdown.strip(),
                title=title or self._extract_title_from_html(html),
                metadata={
                    "title": title,
                    "author": metadata.author if metadata else "",
                    "date": metadata.date if metadata else "",
                    "hostname": metadata.hostname if metadata else "",
                } if metadata else {},
            )
        except Exception:
            return None

    def _extract_with_bs4(self, html: str, url: str) -> ExtractResult | None:
        """使用 BeautifulSoup 降级提取"""
        try:
            soup = BeautifulSoup(html, "lxml")

            # 移除噪音标签
            for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()

            # 移除常见广告/侧边栏
            for selector in [
                '[class*="sidebar"]', '[class*="advert"]', '[class*="comment"]',
                '[id*="sidebar"]', '[id*="advert"]', '[id*="comment"]',
                '[class*="nav"]', '[class*="menu"]', '[class*="footer"]',
                ".ad", ".ads", "#ad", "#ads",
            ]:
                for tag in soup.select(selector):
                    tag.decompose()

            # 优先查找正文容器
            content = (
                soup.find("article")
                or soup.find("main")
                or soup.find(attrs={"role": "main"})
                or soup.find(class_="content")
                or soup.find(id="content")
            )

            if not content:
                # 使用 body
                content = soup.find("body")

            if not content:
                return None

            # 提取标题
            title = self._extract_title_from_html(html)

            # 获取文本，保留基本格式
            text_parts = []
            for elem in content.descendants:
                if isinstance(elem, str):
                    txt = elem.strip()
                    if txt and txt not in text_parts:
                        text_parts.append(txt)

            # 简单 Markdown 生成
            markdown_parts = []
            if title:
                markdown_parts.append(f"# {title}\n")

            # 处理标题
            for tag in content.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
                level = int(tag.name[1])
                txt = tag.get_text(strip=True)
                if txt:
                    markdown_parts.append(f"{'#' * level} {txt}\n")

            # 处理段落
            for tag in content.find_all(["p", "li"]):
                txt = tag.get_text(strip=True)
                if txt and len(txt) > 10:
                    if tag.name == "li":
                        markdown_parts.append(f"- {txt}\n")
                    else:
                        markdown_parts.append(f"{txt}\n\n")

            # 处理链接
            for tag in content.find_all("a", href=True):
                txt = tag.get_text(strip=True)
                href = tag["href"]
                if txt and href and not href.startswith("#"):
                    if href.startswith("/"):
                        from urllib.parse import urljoin
                        href = urljoin(url, href)
                    markdown_parts.append(f"[{txt}]({href})\n\n")

            markdown = "\n".join(markdown_parts).strip()
            if not markdown:
                markdown = "\n\n".join(text_parts)

            return ExtractResult(
                markdown=markdown.strip(),
                title=title,
            )
        except Exception:
            return None

    def _extract_title_from_html(self, html: str) -> str:
        """从 HTML 中提取标题"""
        soup = BeautifulSoup(html, "lxml")
        # 按优先级尝试
        if soup.title:
            return soup.title.get_text(strip=True)
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(strip=True)
        return ""

    @staticmethod
    def _normalize_images(html: str) -> str:
        """预处理 HTML，将 data-src / data-original 等懒加载属性转为 src，使 trafilatura 能识别图片"""
        soup = BeautifulSoup(html, "lxml")
        modified = False
        for img in soup.find_all('img'):
            src = img.get('src', '').strip()
            data_src = img.get('data-src', '').strip()
            # Case 1: data-src has real URL, src is empty/SVG placeholder/JS garbage
            if data_src and data_src.startswith('http') and (
                not src
                or src.startswith('data:')          # SVG placeholder (WeChat lazy-load)
                or src.startswith("')")             # JS template artifact
                or 'concat' in src                  # JS template artifact
            ):
                img['src'] = data_src
                modified = True
                continue
            # Case 2: src is missing/bad, try other lazy-load attributes
            if not src or src.startswith("')") or 'concat' in src or src.startswith('data:'):
                for attr in ('data-original', 'data-url', 'data-lazy-src'):
                    alt_src = img.get(attr, '').strip()
                    if alt_src and alt_src.startswith('http'):
                        img['src'] = alt_src
                        modified = True
                        break
        return str(soup) if modified else html
