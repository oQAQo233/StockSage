"""正文抽取 + 统一格式转换。

这一层是整个管线的关键，也是大多数人踩坑的地方：

  ❌ 直接把新闻 URL 丢给 markitdown
     HtmlConverter 会转换整个 <body>，导航栏、cookie 横幅、侧栏、页脚、
     「相关阅读」全部混进 Markdown。一篇 800 词的新闻能产出 6 万 token 的噪音。

  ✅ 先 trafilatura 抽正文，再 markitdown 转 Markdown
     trafilatura 基于文本密度 + XPath 规则定位正文容器，输出干净的 HTML 片段；
     markitdown 只负责「HTML 片段 → Markdown」这一步，两者各司其职。

markitdown 真正的战场是 PDF / DOCX / PPTX / XLSX 这类异构文件——
研报、政府公报、财报附件，正是新闻管线常见的补充输入。
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import trafilatura

from .config import USER_AGENT, RUN, MAX_BODY_CHARS


@dataclass
class Extracted:
    title: str = ""
    authors: list[str] = field(default_factory=list)
    published_at: str = ""
    sitename: str = ""
    body_text: str = ""
    body_markdown: str = ""
    engine: str = ""


# --------------------------------------------------------------------------- #
# 抓取
# --------------------------------------------------------------------------- #

def fetch_html(url: str, timeout: int | None = None) -> str:
    """抓一个页面。失败抛异常，由 pipeline 记账。"""
    import requests

    resp = requests.get(
        url,
        timeout=timeout or RUN.request_timeout,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en;q=0.8,zh;q=0.9",
        },
    )
    resp.raise_for_status()
    # 有些站点不声明编码，requests 会猜错；交给 apparent_encoding 兜一层
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


# --------------------------------------------------------------------------- #
# 正文抽取
# --------------------------------------------------------------------------- #

def _normalize_date(raw: str) -> str:
    if not raw:
        return ""
    try:
        from dateutil import parser as date_parser

        dt = date_parser.parse(raw, fuzzy=True)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ""


def extract_article(html: str, url: str = "") -> Extracted:
    """从原始 HTML 里抽出正文与元数据。"""
    out = Extracted()

    # 1) 元数据（标题、作者、日期、站点名）
    try:
        meta = trafilatura.extract_metadata(html, default_url=url)
    except Exception:
        meta = None

    if meta is not None:
        out.title = (getattr(meta, "title", "") or "").strip()
        out.sitename = (getattr(meta, "sitename", "") or "").strip()
        author = (getattr(meta, "author", "") or "").strip()
        if author:
            out.authors = [a.strip() for a in re.split(r"[;,/]| and ", author) if a.strip()]
        out.published_at = _normalize_date(getattr(meta, "date", "") or "")

    # 2) 正文：先拿 HTML 片段（交给 markitdown），再拿纯文本（算指纹/兜底）
    clean_html = None
    try:
        clean_html = trafilatura.extract(
            html,
            url=url,
            output_format="html",
            include_links=False,
            include_images=False,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
    except Exception:
        clean_html = None

    text = ""
    try:
        text = trafilatura.extract(
            html,
            url=url,
            output_format="txt",
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        ) or ""
    except Exception:
        text = ""

    # 3) HTML 片段 → Markdown（markitdown 负责）
    markdown = ""
    if clean_html:
        markdown = html_fragment_to_markdown(clean_html)

    if not markdown and text:
        markdown = text
        out.engine = "trafilatura-text"
    elif markdown:
        out.engine = "trafilatura+markitdown"

    # 中文站点的页面模板（导航条、免责声明、备案号、页脚）会被 trafilatura 当成正文，
    # 短快讯尤其严重：一篇 60 字的新闻能抽出 400 字的壳。这里是通用清洗，只对中文生效。
    if _has_cjk(markdown) or _has_cjk(text):
        markdown = _strip_zh_chrome(markdown)
        text = _strip_zh_chrome(text)

    out.body_text = _squash(text or _strip_markdown(markdown))
    out.body_markdown = markdown.strip()
    return out


#: 整行等于这些内容的，一定是页面框架而不是正文
_ZH_CHROME_EXACT = {
    "首页", "行情", "商城", "登录", "注册", "下载客户端", "分享", "正文", "返回首页",
    "加载中", "加载中...", "股票", "要闻精华", "A股", "港股", "美股", "期货", "基金",
    "文章提及标的", "查看股票机会", "--", "。", "免责声明", "声明",
}

#: 含这些字样的行一律丢弃（免责声明、备案号、页脚栏目）
_ZH_CHROME_SUBSTR = (
    "免责声明", "风险提示", "风险自担", "仅供参考", "不构成投资建议", "版权所有",
    "ICP备", "不良信息举报", "证券投资咨询", "网站备案号", "友情链接", "招聘英才",
    "用户体验计划", "投资者关系", "法律声明", "运营许可", "联系我们", "软件下载",
    "市场监管", "个人信息保护", "涉企侵权", "涉算法推荐", "加载中",
)


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _strip_zh_chrome(text: str) -> str:
    """按行剔除中文页面模板。保留段落结构（空行压成一个）。"""
    if not text:
        return text
    kept: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            kept.append("")
            continue
        bare = line.strip("#*-> ").strip()
        if bare in _ZH_CHROME_EXACT:
            continue
        if any(marker in bare for marker in _ZH_CHROME_SUBSTR):
            continue
        kept.append(raw.rstrip())

    # 连续空行压成一个
    out_lines: list[str] = []
    for line in kept:
        if not line and (not out_lines or not out_lines[-1]):
            continue
        out_lines.append(line)
    return "\n".join(out_lines).strip()


def html_fragment_to_markdown(html: str) -> str:
    """用 markitdown 把干净 HTML 片段转成 Markdown；没装则退回朴素转换。"""
    try:
        from markitdown import MarkItDown

        md = MarkItDown(enable_plugins=False)
        result = md.convert_stream(
            io.BytesIO(html.encode("utf-8")), file_extension=".html"
        )
        return (result.text_content or "").strip()
    except Exception:
        return _naive_html_to_markdown(html)


def file_to_markdown(path) -> str:
    """PDF / DOCX / XLSX 等异构文件统一转 Markdown —— 这才是 markitdown 的主场。"""
    try:
        from markitdown import MarkItDown

        return (MarkItDown(enable_plugins=False).convert(str(path)).text_content or "").strip()
    except Exception as exc:
        raise RuntimeError(f"markitdown 转换 {path} 失败：{exc}") from exc


# --------------------------------------------------------------------------- #
# 兜底与工具
# --------------------------------------------------------------------------- #

def _naive_html_to_markdown(html: str) -> str:
    """markitdown 缺席时的最小可用转换：只保留段落与标题层级。"""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", html)
    text = re.sub(r"(?i)<h([1-6])[^>]*>", lambda m: "\n" + "#" * int(m.group(1)) + " ", text)
    text = re.sub(r"(?i)</h[1-6]>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    import html as html_lib

    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_markdown(md: str) -> str:
    return re.sub(r"[#>*`_\[\]()!-]", " ", md)


def _squash(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_BODY_CHARS]


def build_markdown_document(meta: dict, body_md: str) -> str:
    """统一格式 = YAML front matter + 正文 Markdown。

    统一的不该只是「都是 .md」，而是同一组字段名：
    article_id / source / url / published_at / lang / license / content_sha1。
    后面建图、去重、评估都靠这几个字段。
    """
    lines = ["---"]
    for key, value in meta.items():
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value)
        value = str(value).replace("\n", " ")
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(body_md.strip())
    lines.append("")
    return "\n".join(lines)