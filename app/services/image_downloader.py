from __future__ import annotations
import base64
import re
import asyncio
from urllib.parse import urljoin, urlparse

import httpx

from app.config import settings


class ImageDownloader:
    """下载网页中的图片，转换为 base64 嵌入 Markdown"""

    async def download_and_embed(self, markdown: str, source_url: str) -> tuple[str, int]:
        """
        下载图片并以 base64 嵌入 Markdown

        Args:
            markdown: Markdown 文本
            source_url: 原始网页 URL（用于解析相对路径）

        Returns:
            (替换后的 Markdown, 成功下载的图片数量)
        """
        image_urls = self._extract_image_urls(markdown)
        if not image_urls:
            return markdown, 0

        # 并发下载图片
        tasks = []
        for img_url in image_urls:
            absolute_url = urljoin(source_url, img_url)
            if absolute_url.startswith("data:"):
                tasks.append(asyncio.sleep(0, result=None))
            else:
                tasks.append(self._download_as_base64(absolute_url))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 建立 URL 映射
        url_mapping: dict[str, str] = {}
        success_count = 0
        for img_url, result in zip(image_urls, results):
            if isinstance(result, str) and result:
                url_mapping[img_url] = result
                success_count += 1

        markdown = self._replace_urls(markdown, url_mapping)
        markdown = self._deduplicate_images(markdown)
        return markdown, success_count

    @staticmethod
    def _deduplicate_images(markdown: str) -> str:
        """删除完全重复的图片（相同 alt + 相同 data URI 出现在多个位置）"""
        seen_keys = set()
        result_lines = []
        skip_next_empty = False

        for line in markdown.split('\n'):
            m = re.match(r'^!\[([^\]]*)\]\((data:[^)]+)\)$', line.strip())
            if m:
                key = (m.group(1), m.group(2))
                if key in seen_keys:
                    # 跳过这个重复图片行和紧跟的空行
                    skip_next_empty = True
                    continue
                seen_keys.add(key)
            elif skip_next_empty and line.strip() == '':
                skip_next_empty = False
                continue
            else:
                skip_next_empty = False
            result_lines.append(line)

        # 清理末尾多余空行
        while result_lines and not result_lines[-1].strip():
            result_lines.pop()

        return '\n'.join(result_lines)

    def _extract_image_urls(self, markdown: str) -> list[str]:
        """提取 Markdown 中的图片 URL"""
        urls = []
        for match in re.finditer(r'!\[.*?\]\(([^)]+)\)', markdown):
            urls.append(match.group(1))
        for match in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', markdown, re.IGNORECASE):
            urls.append(match.group(1))
        return urls

    async def _download_as_base64(self, url: str) -> str | None:
        """下载图片并返回 base64 data URI"""
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(url, headers={
                    "User-Agent": settings.user_agent,
                    "Referer": url,
                })
                resp.raise_for_status()

                content_type = resp.headers.get("content-type", "image/png").split(";")[0].strip()
                b64 = base64.b64encode(resp.content).decode("ascii")
                return f"data:{content_type};base64,{b64}"
        except Exception:
            return None

    @staticmethod
    def _replace_urls(markdown: str, mapping: dict[str, str]) -> str:
        """替换 Markdown 中的图片 URL"""
        result = markdown
        for old_url, new_uri in mapping.items():
            result = result.replace(f"]({old_url})", f"]({new_uri})")
        return result
