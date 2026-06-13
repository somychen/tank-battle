from __future__ import annotations
import asyncio
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.config import settings
from app.services.fetcher import Fetcher
from app.services.extractor import Extractor, ExtractOptions, ExtractResult


@dataclass
class OutlineLink:
    """导航大纲中的一个链接"""
    title: str
    url: str
    depth: int = 0  # 标题层级（1-based）


@dataclass
class OutlineResult:
    """大纲爬取结果"""
    title: str
    markdown: str
    page_count: int
    failed_count: int
    failed_urls: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class OutlineCrawler:
    """大纲爬取服务 - 识别页面导航结构并逐章抓取合并"""

    # 常见的导航/侧边栏容器选择器
    NAV_SELECTORS = [
        "nav",
        "aside",
        '[class*="sidebar"]',
        '[class*="Sidebar"]',
        '[class*="SIDEBAR"]',
        '[id*="sidebar"]',
        '[id*="Sidebar"]',
        '[class*="nav"]',
        '[class*="menu"]',
        '[class*="toc"]',
        '[class*="TOC"]',
        '[class*="outline"]',
        '[class*="catalog"]',
        '[class*="directory"]',
        '[role="navigation"]',
        ".sidebar",
        "#sidebar",
        ".toc",
        "#toc",
    ]

    # 需要排除的链接文本模式
    EXCLUDE_TEXT_PATTERNS = [
        r"^(首页|登录|注册|搜索|设置|退出|关于|帮助|FAQ|联系|反馈)$",
        r"^(Home|Login|Register|Search|Settings|Logout|About|Help)$",
        r"^(上一页|下一页|上一篇|下一篇|返回|Previous|Next)$",
    ]

    # 需要排除的 URL 模式
    EXCLUDE_URL_PATTERNS = [
        r"#",           # 锚点
        r"javascript:",  # JS 链接
        r"mailto:",     # 邮箱
        r"tel:",        # 电话
        r"\.(pdf|zip|doc|docx|xls|xlsx|ppt|pptx|rar|tar|gz)$",  # 非HTML文件
    ]

    def __init__(self, max_concurrency: int = 3):
        self.max_concurrency = max_concurrency
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def crawl(
        self,
        url: str,
        fetcher: Fetcher,
        extractor: Extractor,
        options: ExtractOptions | None = None,
    ) -> OutlineResult:
        """主入口：抓取大纲所有页面并合并"""
        if options is None:
            options = ExtractOptions()

        # 1. 获取主页，发现大纲链接
        fetch_result = await fetcher.fetch(url)
        outline_links = self._discover_outline(fetch_result.html, url)

        if not outline_links:
            # 如果没发现大纲，退化为单页抓取
            extract_result = extractor.extract(fetch_result.html, url, options)
            return OutlineResult(
                title=extract_result.title,
                markdown=extract_result.markdown,
                page_count=1,
                failed_count=0,
                metadata=extract_result.metadata,
            )

        # 2. 提取主页本身的标题/内容
        main_extract = extractor.extract(fetch_result.html, url, options)

        # 3. 并发抓取每个子页面
        results: list[tuple[int, ExtractResult | None, str]] = []
        sem = asyncio.Semaphore(self.max_concurrency)

        async def fetch_one(link: OutlineLink) -> tuple[int, ExtractResult | None, str]:
            async with sem:
                try:
                    page_result = await fetcher.fetch(link.url)
                    extract_result = extractor.extract(page_result.html, link.url, options)
                    return (link.depth, extract_result, link.url)
                except Exception:
                    return (link.depth, None, link.url)

        tasks = [fetch_one(link) for link in outline_links]
        results = await asyncio.gather(*tasks)

        # 4. 合并所有内容
        return self._merge_results(main_extract, outline_links, results)

    def _discover_outline(self, html: str, base_url: str) -> list[OutlineLink]:
        """从 HTML 中发现导航/大纲链接"""
        soup = BeautifulSoup(html, "lxml")

        # 查找导航容器
        nav_container = self._find_nav_container(soup)
        if not nav_container:
            return []

        # 提取容器内的所有链接
        links = self._extract_links_from_container(nav_container, base_url)

        # 去重并过滤
        seen_urls: set[str] = set()
        filtered: list[OutlineLink] = []
        for link in links:
            normalized = self._normalize_url(link.url)
            if normalized in seen_urls:
                continue
            if self._should_exclude(link):
                continue
            seen_urls.add(normalized)
            filtered.append(link)

        return filtered

    def _find_nav_container(self, soup: BeautifulSoup) -> BeautifulSoup | None:
        """查找最可能的导航/侧边栏容器"""
        candidates: list[tuple[BeautifulSoup, int]] = []

        for selector in self.NAV_SELECTORS:
            try:
                elements = soup.select(selector)
                for el in elements:
                    link_count = len(el.find_all("a", href=True))
                    if link_count >= 3:  # 至少3个链接才算导航
                        candidates.append((el, link_count))
            except Exception:
                continue

        if not candidates:
            return None

        # 按链接数量排序，取最多链接的那个
        candidates.sort(key=lambda x: x[1], reverse=True)

        # 如果有多个候选且差距不大，选择链接最集中的
        # 优先选择 <nav> 或包含 sidebar/toc 的元素
        best = candidates[0][0]
        best_count = candidates[0][1]

        for el, count in candidates:
            tag = el.name or ""
            classes = " ".join(el.get("class", []))
            ids = el.get("id", "")
            combined = f"{tag} {classes} {ids}".lower()

            # 明确匹配 sidebar/toc/nav 的优先
            if any(kw in combined for kw in ["sidebar", "toc", "outline", "catalog"]):
                if count >= best_count * 0.5:  # 至少有最好的一半链接数
                    return el

        return best

    def _extract_links_from_container(
        self, container: BeautifulSoup, base_url: str
    ) -> list[OutlineLink]:
        """从导航容器中提取所有大纲链接"""
        links: list[OutlineLink] = []

        for a_tag in container.find_all("a", href=True):
            href = a_tag.get("href", "").strip()
            if not href:
                continue

            # 解析为绝对 URL
            full_url = urljoin(base_url, href)
            if not full_url.startswith(("http://", "https://")):
                continue

            # 获取链接文本
            text = a_tag.get_text(strip=True)
            if not text:
                # 尝试从 aria-label 或 title 获取
                text = a_tag.get("aria-label", "") or a_tag.get("title", "")
            if not text:
                continue

            # 推断层级深度
            depth = self._infer_depth(a_tag)

            links.append(OutlineLink(title=text, url=full_url, depth=depth))

        return links

    @staticmethod
    def _infer_depth(a_tag) -> int:
        """推断链接在导航中的层级深度"""
        # 查找最近的列表嵌套层数
        depth = 1
        parent = a_tag.parent
        while parent:
            if parent.name in ("li",):
                # 计算外层 ol/ul 嵌套层数
                ul_depth = 0
                p = parent
                while p:
                    if p.name in ("ul", "ol"):
                        ul_depth += 1
                    p = p.parent
                depth = max(depth, ul_depth)
            parent = parent.parent
        return min(depth, 6)

    def _should_exclude(self, link: OutlineLink) -> bool:
        """判断链接是否应该排除"""
        # 检查 URL 模式
        for pattern in self.EXCLUDE_URL_PATTERNS:
            if re.search(pattern, link.url, re.IGNORECASE):
                return True

        # 检查链接文本模式
        for pattern in self.EXCLUDE_TEXT_PATTERNS:
            if re.match(pattern, link.title):
                return True

        # 排除过短的文本（可能是图标等）
        if len(link.title) < 2:
            return True

        # 排除过长的文本（可能是段落不是标题）
        if len(link.title) > 120:
            return True

        return False

    @staticmethod
    def _normalize_url(url: str) -> str:
        """标准化 URL 用于去重（去除尾部斜杠、fragment、小写化）"""
        parsed = urlparse(url)
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
        if parsed.query:
            normalized += f"?{parsed.query}"
        return normalized.lower()

    def _merge_results(
        self,
        main_extract: ExtractResult,
        outline_links: list[OutlineLink],
        fetch_results: list[tuple[int, ExtractResult | None, str]],
    ) -> OutlineResult:
        """合并所有抓取结果为一个 Markdown"""
        lines: list[str] = []
        failed_urls: list[str] = []
        success_count = 0

        # 主标题
        title = main_extract.title or "Outline Document"
        lines.append(f"# {title}\n")

        # 添加元信息
        if main_extract.metadata:
            meta = main_extract.metadata
            if meta.get("author"):
                lines.append(f"> 作者：{meta['author']}")
            if meta.get("date"):
                lines.append(f"> 日期：{meta['date']}")
            if meta.get("hostname"):
                lines.append(f"> 来源：{meta['hostname']}")
        lines.append("")

        # 添加目录
        lines.append("## 目录\n")
        for i, link in enumerate(outline_links, 1):
            indent = "  " * max(0, link.depth - 1)
            lines.append(f"{indent}{i}. [{link.title}](#{self._anchor_id(link.title)})")
        lines.append("")

        # 合并结果：按原始大纲顺序输出
        for i, (link, (depth, extract_result, url)) in enumerate(
            zip(outline_links, fetch_results)
        ):
            heading_level = min(depth + 1, 6)
            heading_prefix = "#" * heading_level

            if extract_result and extract_result.markdown.strip():
                success_count += 1
                lines.append(f"{heading_prefix} {link.title}\n")

                # 清理子页面的顶级标题（避免与大纲标题重复）
                content = extract_result.markdown
                # 去掉子页面中与大纲链接同名的顶级标题行
                for h_level in range(1, 4):
                    prefix = "#" * h_level + " "
                    if content.startswith(prefix):
                        first_line_end = content.find("\n")
                        if first_line_end == -1:
                            first_line = content
                        else:
                            first_line = content[:first_line_end]
                        # 如果标题与大纲链接文本相似，移除
                        if self._titles_similar(first_line[len(prefix):].strip(), link.title):
                            if first_line_end != -1:
                                content = content[first_line_end:].strip()
                            else:
                                content = ""
                        break

                lines.append(content)
                lines.append("")
            else:
                # 失败的页面，保留标题和原始链接
                lines.append(f"{heading_prefix} {link.title}\n")
                lines.append(f"> *此章节抓取失败，[访问原页面]({link.url})*\n")
                failed_urls.append(link.url)

        return OutlineResult(
            title=title,
            markdown="\n".join(lines),
            page_count=success_count,
            failed_count=len(failed_urls),
            failed_urls=failed_urls,
            metadata=main_extract.metadata,
        )

    @staticmethod
    def _anchor_id(title: str) -> str:
        """生成 Markdown 锚点 ID"""
        # 简化处理，与大多数 Markdown 渲染器行为一致
        anchor = re.sub(r"[^\w\s-]", "", title.lower())
        anchor = re.sub(r"\s+", "-", anchor)
        return anchor

    @staticmethod
    def _titles_similar(t1: str, t2: str) -> bool:
        """判断两个标题是否相似"""
        t1 = re.sub(r"\s+", "", t1.lower())
        t2 = re.sub(r"\s+", "", t2.lower())
        if t1 == t2:
            return True
        if len(t1) > 5 and len(t2) > 5:
            return t1 in t2 or t2 in t1
        return False
