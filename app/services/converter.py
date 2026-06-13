from __future__ import annotations
import re

from markdownify import markdownify as md

from app.services.extractor import ExtractOptions


# UI 噪音关键词——出现这些词的行会被清理
UI_NOISE_KEYWORDS = {
    "登录腾讯文档", "立即登录", "登录", "注册", "注销",
    "个人", "只能查看", "仅可查看", "在线文档",
    "菜单", "插入", "正文", "标题", "副标题",
    "默认字体", "微软雅黑", "宋体", "黑体", "楷体",
    "小一", "小二", "三号", "四号", "五号", "更多",
    "加粗", "斜体", "下划线", "删除线",
    "快捷工具", "PDF转换", "生成图片", "排版美化", "打印", "下载",
    "分享", "收藏", "导出",
    "撤销", "重做", "字体", "字号", "样式",
    "保存", "另存为", "另存为", "新建", "打开", "关闭",
    "首页", "目录", "搜索", "查找替换",
    "大纲", "页面设置", "全屏",
    "评论", "批注", "修订",
    "无障碍", "辅助功能", "帮助",
}


class Converter:
    """Markdown 转换服务 - 对已有 Markdown 进行后处理，或作为备用将 HTML 转为 Markdown"""

    def to_markdown(self, html: str, options: ExtractOptions | None = None) -> str:
        """将 HTML 转换为 Markdown（备用路径）"""
        if options is None:
            options = ExtractOptions()

        result = md(
            html,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style", "nav", "footer", "header"],
        )

        if not options.include_images:
            result = self._remove_images(result)
        if not options.include_links:
            result = self._remove_links(result)

        return result.strip()

    def post_process(self, markdown: str, options: ExtractOptions) -> str:
        """对 Markdown 进行后处理"""
        if not options.include_images:
            markdown = self._remove_images(markdown)
        if not options.include_links:
            markdown = self._remove_links(markdown)
        markdown = self._filter_ui_noise(markdown)
        return markdown.strip()

    def _filter_ui_noise(self, markdown: str) -> str:
        """过滤 Markdown 中的 UI 噪音（工具栏、菜单、状态栏等）"""
        lines = markdown.split('\n')
        filtered = []
        prev_line = ""

        for line in lines:
            stripped = line.strip()

            # 保留空行
            if not stripped:
                filtered.append(line)
                prev_line = ""
                continue

            # 保留 frontmatter 分隔符
            if stripped in ('---', '==='):
                filtered.append(line)
                prev_line = stripped
                continue

            # 跳过内联 SVG 元素（编辑器 UI 噪音），但保留 Markdown 图片引用
            if not stripped.startswith("![") and ('data:image/svg' in stripped or '<svg' in stripped.lower()):
                continue

            # 跳过纯数字/百分比统计行（如 "13390 个字", "100%"）
            if re.match(r'^[\d\s%,.+xX×万字个]+$', stripped) and len(stripped) < 30:
                continue

            # 跳过 UI 关键词精确匹配
            if stripped in UI_NOISE_KEYWORDS:
                continue

            # 跳过重复连续行（工具栏项常重复出现）
            if stripped == prev_line and len(stripped) < 30:
                continue

            # 跳过极短的单字符/纯符号行（不影响中文双字词如"技能"）
            if len(stripped) == 1 and not stripped.startswith('#'):
                continue

            filtered.append(line)
            prev_line = stripped

        return '\n'.join(filtered)

    @staticmethod
    def _remove_images(text: str) -> str:
        """移除 Markdown 图片引用"""
        # ![alt](url) 格式
        text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
        return text

    @staticmethod
    def _remove_links(text: str) -> str:
        """移除 Markdown 超链接，保留链接文本"""
        # [text](url) 格式
        text = re.sub(r'\[([^\]]*)\]\([^\)]+\)', r'\1', text)
        return text
