"""正文质量闸：清洗站点页脚 / 判语言 / 拒收废稿。

放这里而不是塞进 `convert.py`，因为有三处要用：
* `pipeline.process_stub` 抓完立刻判，废稿不进库、不写 md；
* `pipeline.build_graph` 抽之前再洗一遍（老库里的历史文章也受保护）；
* 想把历史文章回头重判时，直接 import 这个模块调 assess() 就行。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

#: 硬标记：出现即说明正文结束，后面基本是推荐列表。位置要求在全文 40% 之后，
#: 避免正文开头写「本文来源：界面新闻」时把整篇截掉。
HARD_CUT = re.compile(
    r"^\s*(?:"
    r"原创新闻来源|原创来源|本文来源|文章来源|稿件来源|"
    r"相关阅读|相关推荐|推荐阅读|延伸阅读|更多阅读|猜你喜欢|"
    r"版权声明|免责声明|特别声明|"
    r"扫一扫|关注我们|下载.*客户端|客户端下载"
    r")"
)

#: 软标记：署名/编辑这类词在正文里也会出现（"编辑：李四"可能在第 6 段），
#: 所以只有出现在 80% 之后才当页脚处理。漏截一行页脚，比删掉一段正文便宜得多。
SOFT_CUT = re.compile(r"^\s*(?:责任编辑|责编|编辑[:：]|校对[:：]|作者[:：]|记者[:：]|声明[:：])")

#: 兼容旧调用名
FOOTER_CUT = HARD_CUT

#: 页脚特征：许可证号、电话、备案号、二维码之类。用来估算"这篇还剩多少是正文"
FOOTER_LINE = re.compile(
    r"(互联网新闻信息服务许可证|增值电信业务|京B2|京ICP|备案号|"
    r"电话[:：]|传真[:：]|邮箱[:：]|邮编[:：]|"
    r"扫一扫|二维码|官方微信|官方微博|官方客户端|"
    r"版权所有|Copyright|All Rights Reserved)",
    re.IGNORECASE,
)



CJK = re.compile(r"[\u4e00-\u9fff]")
WORDY = re.compile(r"[A-Za-z0-9]")

#: 尾巴上的"相关链接"块：连续以 * / - / · 开头、且不含句末标点
_BULLET = re.compile(r"^\s*[*\-·•]\s+")

#: 正文低于这个字数就不值得花钱抽（页脚、付费墙截断都会掉到这以下）。
#: 150 是在 20 篇语料上定的：两篇垃圾分别是 84 / 120 字，最短的合法稿件 219 字。
MIN_CONTENT_CHARS = int(os.getenv("NEWSKG_MIN_BODY_CHARS", "150"))


@dataclass
class Verdict:
    ok: bool
    reason: str
    body: str
    lang: str
    content_chars: int
    footer_ratio: float


def content_len(text: str) -> int:
    """正文长度：中文按字、西文按字母数字块，标点与空白不算。"""
    return len(CJK.findall(text)) + len(WORDY.findall(text)) // 2


def detect_lang(text: str) -> str:
    """按字符占比判语言，不信源的注册语言（tmtpost 会发英文稿却注册成 zh）。"""
    cjk = len(CJK.findall(text))
    wordy = len(WORDY.findall(text))
    if cjk == 0 and wordy == 0:
        return "en"
    return "zh" if cjk / max(cjk + wordy, 1) >= 0.2 else "en"


def clean(body: str, title: str = "") -> str:
    """截掉页脚之后的推荐列表，并去掉与标题重复的首个一级标题。

    只做"减法"，不改写任何正文；宁可少留一行，也不拼进别的内容。

    截断位置有门槛：硬标记要在全文 40% 之后、软标记要在 80% 之后才算数。
    因为「记者：」「来源：」也可能出现在开头（署名行），无脑从头往下截会把
    整篇正文删光——那比"留着页脚"严重得多。
    """
    lines = body.splitlines()
    total = sum(len(ln) for ln in lines) or 1

    # 1) 从第一处合格的页脚标记截断
    kept: list[str] = []
    consumed = 0
    for line in lines:
        pos = consumed / total
        if (HARD_CUT.match(line) and pos >= 0.4) or (SOFT_CUT.match(line) and pos >= 0.8):
            break
        kept.append(line)
        consumed += len(line)

    # 2) 去掉尾部连着的一串短条目（相关链接），它们通常没有句号
    while kept:
        tail = kept[-1].strip()
        if not tail:
            kept.pop()
            continue
        if _BULLET.match(tail) and not re.search(r"[。！？]$", tail) and len(tail) < 60:
            kept.pop()
            continue
        break

    text = "\n".join(kept).strip()

    # 3) 首个一级标题若与文章标题重复，去掉（front matter 之外又抄了一遍）
    first, _, rest = text.partition("\n")
    if first.startswith("# ") and (not title or title[:12] in first):
        text = rest.strip()
    return text


def assess(body: str, title: str = "") -> Verdict:
    """判一篇正文能不能进库 / 能不能花钱抽。

    拒收只针对两种确定的情况：正文几乎不剩什么、或者剩下的主要是许可证与电话。
    门槛压低（200 字）是有意的——短快讯是合法新闻，只是长文被拦在门外。
    """
    cleaned = clean(body, title)
    total = max(sum(len(ln) for ln in cleaned.splitlines() if ln.strip()), 1)
    footer_chars = sum(
        len(ln) for ln in cleaned.splitlines() if ln.strip() and FOOTER_LINE.search(ln)
    )
    footer_ratio = footer_chars / total
    size = content_len(cleaned)
    lang = detect_lang(cleaned)

    if size < MIN_CONTENT_CHARS:
        reason = f"正文仅 {size} 字（<{MIN_CONTENT_CHARS}）"
        if footer_ratio >= 0.5:
            reason += f"，且 {footer_ratio:.0%} 是页脚"
        return Verdict(False, reason, cleaned, lang, size, footer_ratio)

    if footer_ratio >= 0.5:
        return Verdict(
            False, f"正文 {size} 字但页脚占 {footer_ratio:.0%}", cleaned, lang, size, footer_ratio
        )

    return Verdict(True, "", cleaned, lang, size, footer_ratio)


__all__ = ["Verdict", "assess", "clean", "content_len", "detect_lang", "MIN_CONTENT_CHARS"]

