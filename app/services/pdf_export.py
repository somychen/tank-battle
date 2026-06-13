"""Markdown -> HTML for browser print-to-PDF"""

from __future__ import annotations

import markdown


_STYLE = """
<style>
  body {
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif;
    font-size: 14px; line-height: 1.8; color: #333;
    max-width: 800px; margin: 20px auto; padding: 20px;
  }
  h1 { font-size: 24px; border-bottom: 2px solid #2563eb; padding-bottom: 8px; margin-top: 32px; }
  h2 { font-size: 20px; border-bottom: 1px solid #e5e7eb; padding-bottom: 6px; margin-top: 28px; }
  h3 { font-size: 17px; margin-top: 24px; }
  pre { background: #f3f4f6; padding: 16px; border-radius: 6px; overflow-x: auto; font-size: 13px; white-space: pre-wrap; }
  code { background: #f3f4f6; padding: 2px 6px; border-radius: 3px; font-size: 13px; font-family: monospace; }
  pre code { background: none; padding: 0; }
  table { border-collapse: collapse; width: 100%; margin: 16px 0; }
  th, td { border: 1px solid #e5e7eb; padding: 8px 12px; text-align: left; }
  th { background: #f9fafb; font-weight: 600; }
  blockquote { border-left: 4px solid #2563eb; margin: 16px 0; padding: 8px 16px; background: #f9fafb; color: #666; }
  ul, ol { padding-left: 24px; }
  li { margin: 4px 0; }
  a { color: #2563eb; }
  img { max-width: 100%; }
  hr { border: none; border-top: 1px solid #e5e7eb; margin: 24px 0; }
  @media print {
    body { margin: 0; padding: 0; }
    @page { size: A4; margin: 2cm; }
  }
</style>
"""


def markdown_to_print_html(md_content: str, title: str = "") -> str:
    """Convert markdown to styled HTML that auto-opens print dialog"""
    html_body = markdown.markdown(
        md_content,
        extensions=["tables", "fenced_code", "nl2br"],
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{title or "Document"}</title>
{_STYLE}
</head>
<body>
{html_body}
<script>
  window.onload = function() {{ setTimeout(function() {{ window.print(); }}, 300); }};
</script>
</body>
</html>"""
