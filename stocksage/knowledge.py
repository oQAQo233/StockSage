"""内置新闻知识引擎与主项目数据契约之间的处理边界。"""
import hashlib
import json
import math
import re
from dataclasses import asdict

from .news_knowledge import dedup, models
from .news_knowledge.kg import build_extractor


def make_extractor(backend):
    if backend not in {"rule", "llm", "kggen"}:
        raise ValueError("知识抽取后端须为 rule、llm 或 kggen")
    try:
        return build_extractor(backend)
    except ModuleNotFoundError as error:
        raise ValueError(f"{backend} 缺少依赖 {error.name}，请安装对应可选依赖") from error
    except SystemExit as error:
        raise ValueError(str(error)) from error


def to_article(item):
    text = item["title"] + ("\n" + item["body"] if item.get("body") else "")
    return models.Article(article_id=item["id"], source=item["source"], url=item.get("url", ""),
                          canonical_url=dedup.canonicalize_url(item.get("url", "")),
                          title=item["title"], published_at=item["published_at"], lang=item.get("lang", "zh"),
                          license=item.get("license", "来源许可需核验"), authors=[], body=text,
                          content_sha1=models.sha1_of(text), simhash=dedup.simhash(text),
                          raw_path=item.get("raw_ref", ""), markdown_path=item.get("markdown_ref", ""),
                          fetched_at=item.get("fetched_at", ""))


def link_symbols(item, stocks):
    """精确证券代码/长别名匹配；同名候选不擅自消歧。"""
    text = item["title"] + "\n" + item.get("body", "")
    names = {}
    for stock in stocks:
        for alias in [stock["company"], *stock.get("aliases", "").split("|")]:
            alias = alias.strip()
            if len(alias) >= 3:
                names.setdefault(alias.casefold(), set()).add(stock["symbol"])
    linked, evidence, ambiguous = set(), [], []
    for stock in stocks:
        symbol, code = stock["symbol"], stock["symbol"].split(".")[0]
        if symbol.casefold() in text.casefold() or (len(code) == 6 and code.isdigit() and re.search(rf"(?<!\d){code}(?!\d)", text)):
            linked.add(symbol)
            evidence.append(dict(symbol=symbol, match=code, method="exact_code"))
    for alias, candidates in names.items():
        if alias in text.casefold():
            if len(candidates) == 1:
                symbol = next(iter(candidates))
                linked.add(symbol)
                evidence.append(dict(symbol=symbol, match=alias, method="exact_alias"))
            else:
                ambiguous.append(dict(alias=alias, candidates=sorted(candidates)))
    return sorted(linked), evidence, ambiguous


def normalize_news(items):
    """内置双层去重；限制为同一发布日，避免吞掉重复发生的事件。"""
    from .data import timestamp
    ordered = sorted(items, key=lambda item: timestamp(item["available_at"]))
    groups = {}
    for item in ordered:
        groups.setdefault(item["published_at"][:10], []).append(to_article(item))
    # 空 URL 的合成样例不能因 URL 相同被误并；正文去重与源项目保持一致。
    mapping = {}
    for articles in groups.values():
        mapping.update(dedup.find_duplicates(articles))
    kept, records = {}, []
    for item in ordered:
        duplicate = mapping.get(item["id"])
        if duplicate:
            records.append(dict(id=item["id"], duplicate_of=duplicate, source=item["source"],
                                available_at=item["available_at"], symbols=item["symbols"]))
            # 关联信息在较晚版本才出现时不回填早版本，保留在 duplicate_records 中。
            continue
        kept[item["id"]] = {**item}
    return list(kept.values()), records


def extract_news(news, backend="rule"):
    """每篇只抽一次；不将人工 event_type 文案拼回原文伪装成抽取结果。"""
    if backend == "none":
        return [{**item, "triples": [], "extraction": dict(status="disabled", backend="none")} for item in news]
    extractor = make_extractor(backend)
    results = []
    for item in news:
        article = to_article(item)
        triples, rejected = [], 0
        try:
            candidates = extractor.extract(article)
        except Exception as error:
            # 用户显式选中的后端失败时中断，不能把服务失败悄悄当成“无关系”。
            raise ValueError(f"{backend} 抽取失败，新闻 {item['id']}：{error}") from error
        seen = set()
        for candidate in candidates:
            raw = asdict(candidate)
            subject, predicate, obj = (str(raw[key]).strip() for key in ("subject", "predicate", "object"))
            key = subject.casefold(), predicate, obj.casefold()
            # 引用必须落在当前原文；分数只表示后端启发值，不是真实概率。
            if not all((subject, predicate, obj)) or subject.casefold() == obj.casefold() or not math.isfinite(float(raw["confidence"])):
                rejected += 1
                continue
            if subject.casefold() not in article.body.casefold() or obj.casefold() not in article.body.casefold():
                rejected += 1
                continue
            # 原规则可能把“通过股份回购，可以让投资人…”切成机构/实体；
            # 这些功能短语即使在原文出现，也不能作为具名实体进入主图谱。
            fragment_prefixes = ("通过", "根据", "按照", "对于", "可以", "能够", "使得", "进行", "从而", "让投资", "项目计划")
            if subject.startswith(fragment_prefixes) or obj.startswith(fragment_prefixes):
                rejected += 1
                continue
            if key in seen:
                continue
            seen.add(key)
            sentences = re.split(r"(?<=[。！？.!?])\s*|\n+", article.body)
            excerpt = next((s for s in sentences if subject.casefold() in s.casefold() and obj.casefold() in s.casefold()), "")
            identifier = hashlib.sha256(json.dumps([item["id"], backend, *key], ensure_ascii=False).encode()).hexdigest()[:24]
            triples.append(dict(id=identifier, subject=subject, predicate=predicate, object=obj,
                                evidence_id=item["id"], source=item["source"], url=item.get("url", ""),
                                published_at=item["published_at"], available_at=item["available_at"], backend=backend,
                                confidence=raw["confidence"], evidence_text=excerpt,
                                evidence_match="cooccurrence" if excerpt else "document_only", verification="unverified_candidate"))
        results.append({**item, "triples": triples,
                        "extraction": dict(status="completed" if triples else "no_relations", backend=backend,
                                           input_kind="title_and_body" if item.get("body") else "title_only",
                                           rejected=rejected, triples=len(triples))})
    return results


def summarize(news, duplicates, backend):
    triples = [triple for item in news for triple in item.get("triples", [])]
    return dict(backend=backend, engine="stocksage.news_knowledge", articles=len(news),
                duplicate_articles=len(duplicates), triples=len(triples),
                no_relations=sum(item.get("extraction", {}).get("status") == "no_relations" for item in news),
                rejected_relations=sum(item.get("extraction", {}).get("rejected", 0) for item in news),
                linked_articles=sum(bool(item["symbols"]) for item in news),
                unlinked_articles=sum(not item["symbols"] for item in news),
                note="实体为原文表面名称；精确别名匹配不等于完成实体消歧。证据句为共现定位，未验证关系语义。")
